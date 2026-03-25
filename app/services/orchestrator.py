from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.domain.personas import AGENT_ORDER, AGENT_PROFILES
from app.schemas.models import ChatMessage, SessionState
from app.services.llm_client import LLMCallError, LLMClient
from app.services.prompts import (
    build_agent_system_prompt,
    build_agent_user_prompt,
    build_facilitator_system_prompt,
    build_facilitator_user_prompt,
)
from app.services.repository_analyzer import RepositoryAnalyzer
from app.services.session_manager import SessionManager

logger = logging.getLogger(__name__)
FALLBACK_NO_GROUNDED_EVIDENCE = "I do not have enough grounded evidence yet; I need additional repository context."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentOrchestrator:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        session_manager: SessionManager,
        repository_analyzer: RepositoryAnalyzer,
        settings: Settings,
    ) -> None:
        self.llm_client = llm_client
        self.session_manager = session_manager
        self.repository_analyzer = repository_analyzer
        self.settings = settings

    async def process_user_input(self, session: SessionState, user_input: str) -> None:
        user_message = ChatMessage(
            id=str(uuid.uuid4()),
            role="human",
            author="Operator",
            content=user_input,
            timestamp=_now(),
        )
        await self.session_manager.add_message(session, user_message)
        operator_inputs: list[str] = [user_input]

        round_messages: list[ChatMessage] = []

        await self.session_manager.publish_event(
            session,
            "session_status",
            {"status": "running", "phase": "agent_round"},
        )

        for agent_name in AGENT_ORDER:
            await self._ingest_operator_interruptions(session, operator_inputs)
            operator_focus_for_retrieval = operator_inputs[-1]
            operator_message = self._compose_operator_message(operator_inputs)
            relevant_evidence = self.repository_analyzer.select_relevant_evidence(
                session.repo_context,
                operator_focus_for_retrieval,
                limit=10,
            )

            profile = AGENT_PROFILES[agent_name]
            await self.session_manager.publish_event(
                session,
                "thinking",
                {"agent": agent_name, "state": "start"},
            )

            try:
                result = await self.llm_client.generate_text(
                    system_prompt=build_agent_system_prompt(profile),
                    user_prompt=build_agent_user_prompt(
                        session=session,
                        profile=profile,
                        operator_message=operator_message,
                        relevant_evidence=relevant_evidence,
                    ),
                    agent_name=agent_name,
                    model=self.settings.llm_model_agent,
                    max_tokens=self.settings.llm_agent_max_output_tokens,
                )
                await self.session_manager.add_usage(session, result.usage)
                payload = self._parse_agent_text_payload(result.text)

                requested_path = self._extract_context_request(result.text)
                if requested_path:
                    logger.info("Agent requested additional context agent=%s path=%s", agent_name, requested_path)
                    additional_context = self.repository_analyzer.get_path_context(session.repo_context, requested_path)
                    followup = await self.llm_client.generate_text(
                        system_prompt=build_agent_system_prompt(profile),
                        user_prompt=build_agent_user_prompt(
                            session=session,
                            profile=profile,
                            operator_message=operator_message,
                            relevant_evidence=relevant_evidence,
                            additional_context=additional_context,
                            force_final=True,
                        ),
                        agent_name=agent_name,
                        model=self.settings.llm_model_agent,
                        max_tokens=self.settings.llm_agent_max_output_tokens,
                    )
                    await self.session_manager.add_usage(session, followup.usage)
                    payload = self._parse_agent_text_payload(followup.text)
                    if requested_path and requested_path not in payload["references"]:
                        payload["references"].append(requested_path)

                if payload["message"] == FALLBACK_NO_GROUNDED_EVIDENCE:
                    logger.warning("Fallback message injected agent=%s evidence_items=%d", agent_name, len(relevant_evidence))
                self._apply_state_delta(session, agent_name, payload)

                agent_message = ChatMessage(
                    id=str(uuid.uuid4()),
                    role="agent",
                    author=agent_name,
                    content=payload["message"],
                    timestamp=_now(),
                    references=payload["references"],
                    metadata={
                        "risks": payload["risks"],
                        "open_questions": payload["open_questions"],
                    },
                )
                await self.session_manager.add_message(session, agent_message)
                round_messages.append(agent_message)
            except LLMCallError as exc:
                fallback = ChatMessage(
                    id=str(uuid.uuid4()),
                    role="agent",
                    author=agent_name,
                    content=(
                        "I could not complete my analysis due to an LLM API issue. "
                        "Please retry or check API configuration."
                    ),
                    timestamp=_now(),
                    references=[],
                    metadata={"error": str(exc)},
                )
                await self.session_manager.add_message(session, fallback)
                round_messages.append(fallback)
                await self.session_manager.publish_event(
                    session,
                    "agent_error",
                    {"message": f"{agent_name} failed to respond cleanly: {exc}"},
                )
            finally:
                await self.session_manager.publish_event(
                    session,
                    "thinking",
                    {"agent": agent_name, "state": "stop"},
                )

        await self._ingest_operator_interruptions(session, operator_inputs)
        await self._run_facilitator_summary(
            session,
            self._compose_operator_message(operator_inputs),
            round_messages,
        )

        await self.session_manager.publish_event(
            session,
            "session_status",
            {"status": "idle", "phase": "complete"},
        )

    async def _run_facilitator_summary(
        self,
        session: SessionState,
        operator_message: str,
        round_messages: list[ChatMessage],
    ) -> None:
        facilitator_name = "Facilitator"
        await self.session_manager.publish_event(
            session,
            "thinking",
            {"agent": facilitator_name, "state": "start"},
        )
        try:
            result = await self.llm_client.generate_json(
                system_prompt=build_facilitator_system_prompt(),
                user_prompt=build_facilitator_user_prompt(operator_message, round_messages),
                agent_name=facilitator_name,
                model=self.settings.llm_model_synthesis,
                max_tokens=self.settings.llm_synthesis_max_output_tokens,
            )
            payload = self._normalize_facilitator_payload(result.payload)
            content = self._render_facilitator_markdown(payload)
            msg = ChatMessage(
                id=str(uuid.uuid4()),
                role="facilitator",
                author=facilitator_name,
                content=content,
                timestamp=_now(),
                metadata=payload,
            )
            await self.session_manager.add_message(session, msg)
            await self.session_manager.add_usage(session, result.usage)
        except LLMCallError as exc:
            msg = ChatMessage(
                id=str(uuid.uuid4()),
                role="facilitator",
                author=facilitator_name,
                content=(
                    "Could not generate consolidated summary due to an LLM API issue. "
                    "Please retry once the provider is available."
                ),
                timestamp=_now(),
                metadata={"error": str(exc)},
            )
            await self.session_manager.add_message(session, msg)
            await self.session_manager.publish_event(
                session,
                "agent_error",
                {"message": f"Facilitator summary failed: {exc}"},
            )
        finally:
            await self.session_manager.publish_event(
                session,
                "thinking",
                {"agent": facilitator_name, "state": "stop"},
            )

    async def _ingest_operator_interruptions(self, session: SessionState, operator_inputs: list[str]) -> None:
        queued_inputs = await self.session_manager.pop_pending_inputs(session)
        for raw in queued_inputs:
            content = str(raw or "").strip()
            if not content:
                continue
            operator_inputs.append(content)
            interjection = ChatMessage(
                id=str(uuid.uuid4()),
                role="human",
                author="Operator",
                content=content,
                timestamp=_now(),
            )
            await self.session_manager.add_message(session, interjection)

    @staticmethod
    def _compose_operator_message(operator_inputs: list[str]) -> str:
        cleaned = [item.strip() for item in operator_inputs if item and item.strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]

        latest = cleaned[-1]
        earlier = "\n".join(f"- {item}" for item in cleaned[:-1][-4:])
        return (
            f"Latest operator input:\n{latest}\n\n"
            f"Earlier operator inputs in this round:\n{earlier}"
        )

    @staticmethod
    def _extract_context_request(text: str) -> str | None:
        patterns = [
            r"(?im)^\s*request_context\s*[:=]\s*([^\n]+?)\s*$",
            r"(?im)^\s*more_context_on\s*[:=]\s*([^\n]+?)\s*$",
            r"(?im)^\s*more\s+context\s+on\s*[:=]?\s*([^\n]+?)\s*$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip().strip("`")
                if value:
                    return value
        return None

    @staticmethod
    def _parse_agent_text_payload(text: str) -> dict[str, Any]:
        references: list[str] = []
        risks: list[str] = []
        open_questions: list[str] = []
        message_lines: list[str] = []
        deltas = {
            "confidence_delta": 0.0,
            "engagement_delta": 0.0,
            "caution_delta": 0.0,
            "friction_delta": 0.0,
        }

        section = "message"
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                if section == "message":
                    message_lines.append("")
                continue

            lowered = stripped.lower().rstrip(":")
            if lowered in {"analysis", "answer", "response"}:
                section = "message"
                continue
            if lowered in {"references", "reference"}:
                section = "references"
                continue
            if lowered in {"risks", "risk"}:
                section = "risks"
                continue
            if lowered in {"open_questions", "open questions", "questions", "open question"}:
                section = "open_questions"
                continue
            if lowered in {"state_deltas", "state deltas", "deltas"}:
                section = "state"
                continue
            if lowered.startswith("request_context") or lowered.startswith("more_context_on") or lowered.startswith("more context on"):
                continue

            if section == "references":
                value = stripped.lstrip("-").strip()
                if value:
                    references.append(value)
                continue
            if section == "risks":
                value = stripped.lstrip("-").strip()
                if value:
                    risks.append(value)
                continue
            if section == "open_questions":
                value = stripped.lstrip("-").strip()
                if value:
                    open_questions.append(value)
                continue
            if section == "state":
                match = re.match(r"(?i)^\s*([a-z_]+)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*$", stripped)
                if match:
                    key = match.group(1).lower()
                    if key in deltas:
                        deltas[key] = AgentOrchestrator._to_delta(match.group(2))
                continue

            message_lines.append(line.rstrip())

        message = "\n".join(message_lines).strip()
        if not message:
            message = FALLBACK_NO_GROUNDED_EVIDENCE

        if not references:
            inferred = re.findall(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+(?::[A-Za-z0-9_]+)?)", message)
            references = [item for item in inferred if item]

        references = list(dict.fromkeys(ref for ref in references if ref.strip()))[:8]
        risks = list(dict.fromkeys(item for item in risks if item.strip()))[:8]
        open_questions = list(dict.fromkeys(item for item in open_questions if item.strip()))[:8]

        return {
            "message": message,
            "references": references,
            "risks": risks,
            "open_questions": open_questions,
            "confidence_delta": deltas["confidence_delta"],
            "engagement_delta": deltas["engagement_delta"],
            "caution_delta": deltas["caution_delta"],
            "friction_delta": deltas["friction_delta"],
        }

    @staticmethod
    def _normalize_facilitator_payload(payload: dict[str, Any]) -> dict[str, Any]:
        def as_list(key: str) -> list[str]:
            value = payload.get(key)
            if not isinstance(value, list):
                return []
            return [str(item) for item in value if str(item).strip()]

        return {
            "summary": str(payload.get("summary") or "No summary available."),
            "consensus": as_list("consensus"),
            "disagreements": as_list("disagreements"),
            "action_plan": as_list("action_plan"),
            "risks": as_list("risks"),
            "unknowns": as_list("unknowns"),
        }

    @staticmethod
    def _render_facilitator_markdown(payload: dict[str, Any]) -> str:
        def section(title: str, items: list[str]) -> str:
            if not items:
                return f"**{title}:** none"
            bullets = "\n".join(f"- {item}" for item in items)
            return f"**{title}:**\n{bullets}"

        parts = [
            f"**Consolidated Outcome**\n{payload['summary']}",
            section("Consensus", payload["consensus"]),
            section("Disagreements", payload["disagreements"]),
            section("Action Plan", payload["action_plan"]),
            section("Risks", payload["risks"]),
            section("Unknowns", payload["unknowns"]),
        ]
        return "\n\n".join(parts)

    @staticmethod
    def _to_delta(raw: Any) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if value > 0.2:
            return 0.2
        if value < -0.2:
            return -0.2
        return value

    @staticmethod
    def _clamp_01(value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value

    def _apply_state_delta(self, session: SessionState, agent_name: str, payload: dict[str, Any]) -> None:
        state = session.agent_states.get(agent_name)
        if not state:
            return

        state["confidence"] = self._clamp_01(state["confidence"] + payload["confidence_delta"])
        state["engagement"] = self._clamp_01(state["engagement"] + payload["engagement_delta"])
        state["caution"] = self._clamp_01(state["caution"] + payload["caution_delta"])
        state["friction"] = self._clamp_01(state["friction"] + payload["friction_delta"])

        # Lightweight generic heuristic to avoid flat state trajectories.
        content = payload["message"].lower()
        if "not sure" in content or "maybe" in content:
            state["confidence"] = self._clamp_01(state["confidence"] - 0.01)
        if len(content) < 140:
            state["engagement"] = self._clamp_01(state["engagement"] - 0.01)
        if "risk" in content:
            state["caution"] = self._clamp_01(state["caution"] + 0.01)
