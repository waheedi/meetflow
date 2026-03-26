from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx
from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, BadRequestError, RateLimitError

from app.core.config import Settings
from app.core.costing import compute_cost_usd
from app.core.pricing import (
    PricingLookupError,
    parse_pricing_docs_rows,
    resolve_fallback_model_pricing,
    resolve_model_pricing_from_docs_catalog,
    resolve_model_pricing_from_payload,
)
from app.schemas.models import UsageRecord


class LLMCallError(Exception):
    pass


logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    payload: dict[str, Any]
    usage: UsageRecord


@dataclass
class LLMTextResult:
    text: str
    usage: UsageRecord


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: AsyncOpenAI | None = None
        self._pricing_cache: dict[str, tuple[float, float]] = {}
        self._pricing_cache_lock = asyncio.Lock()

        if settings.mock_llm:
            return

        if settings.llm_provider.lower() != "openai":
            raise ValueError(f"Unsupported llm_provider={settings.llm_provider}; only 'openai' is currently implemented.")

        if not settings.openai_api_key:
            return

        self._client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    async def preload_pricing_for_models(self, models: set[str]) -> None:
        targets = {m.strip() for m in models if m and m.strip()}
        if not targets:
            return

        try:
            timeout = self.settings.request_timeout_seconds
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(self.settings.openai_pricing_docs_url)
                response.raise_for_status()
            catalog = parse_pricing_docs_rows(response.text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Startup pricing preload from docs failed: %s", exc)
            return

        loaded = 0
        async with self._pricing_cache_lock:
            for model in targets:
                resolved = resolve_model_pricing_from_docs_catalog(model, catalog)
                if resolved is None:
                    continue
                self._pricing_cache[model] = resolved
                loaded += 1
                logger.info(
                    "Startup pricing preload model=%s input_per_1m=%s output_per_1m=%s",
                    model,
                    resolved[0],
                    resolved[1],
                )
        if loaded < len(targets):
            missing = sorted(targets - set(self._pricing_cache.keys()))
            if missing:
                logger.warning("Startup pricing preload missing models=%s", ", ".join(missing))

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        agent_name: str,
        model: str | None = None,
        max_tokens: int = 8192,
    ) -> LLMResult:
        if self.settings.mock_llm:
            return self._mock_result(agent_name=agent_name, user_prompt=user_prompt)

        selected_model = model or self.settings.llm_model_agent

        if self._client is None:
            raise LLMCallError(
                "LLM client is not configured. Set OPENAI_API_KEY (or enable MOCK_LLM=true for local demo mode)."
            )

        attempt = 0
        last_error: Exception | None = None

        while attempt < self.settings.llm_max_retries:
            attempt += 1
            try:
                try:
                    response = await self._create_response(
                        agent_name=agent_name,
                        model=selected_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        max_tokens=max_tokens,
                        json_mode=True,
                    )
                except BadRequestError:
                    # Fallback for models/providers that do not support json_object response format.
                    response = await self._create_response(
                        agent_name=agent_name,
                        model=selected_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        max_tokens=max_tokens,
                        json_mode=False,
                    )

                content, finish_reason = self._extract_response_text_and_finish_reason(response)
                content = (content or "{}").strip()
                logger.info(
                    "LLM raw response agent=%s model=%s chars=%d finish_reason=%s preview=%r",
                    agent_name,
                    selected_model,
                    len(content),
                    finish_reason,
                    content[:320],
                )
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    logger.warning(
                        "LLM non-JSON response agent=%s model=%s; using content as message.",
                        agent_name,
                        selected_model,
                    )
                    payload = {"message": content, "references": [], "risks": [], "open_questions": []}

                if not str(payload.get("message") or "").strip():
                    logger.warning(
                        "LLM payload missing message agent=%s model=%s keys=%s payload_preview=%r",
                        agent_name,
                        selected_model,
                        sorted(payload.keys()),
                        str(payload)[:320],
                    )

                input_tokens, output_tokens = self._extract_usage_tokens(response)
                input_price_per_1m, output_price_per_1m = await self._resolve_model_pricing(selected_model)
                cost = compute_cost_usd(
                    input_tokens,
                    output_tokens,
                    input_price_per_1m,
                    output_price_per_1m,
                )

                usage_record = UsageRecord(
                    model=selected_model,
                    agent=agent_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )
                return LLMResult(payload=payload, usage=usage_record)

            except (RateLimitError, APITimeoutError, APIConnectionError, APIError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt >= self.settings.llm_max_retries:
                    break
                backoff = self.settings.llm_retry_backoff_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                break

        raise LLMCallError(f"LLM call failed after {attempt} attempt(s): {last_error}")

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        agent_name: str,
        model: str | None = None,
        max_tokens: int = 8192,
    ) -> LLMTextResult:
        if self.settings.mock_llm:
            mocked = self._mock_result(agent_name=agent_name, user_prompt=user_prompt)
            return LLMTextResult(text=str(mocked.payload.get("message", "")), usage=mocked.usage)

        selected_model = model or self.settings.llm_model_agent
        if self._client is None:
            raise LLMCallError(
                "LLM client is not configured. Set OPENAI_API_KEY (or enable MOCK_LLM=true for local demo mode)."
            )

        attempt = 0
        last_error: Exception | None = None
        while attempt < self.settings.llm_max_retries:
            attempt += 1
            try:
                response = await self._create_response(
                    agent_name=agent_name,
                    model=selected_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    json_mode=False,
                )
                text, finish_reason = self._extract_response_text_and_finish_reason(response)
                text = (text or "").strip()
                logger.info(
                    "LLM text response agent=%s model=%s chars=%d finish_reason=%s preview=%r",
                    agent_name,
                    selected_model,
                    len(text),
                    finish_reason,
                    text[:320],
                )

                if not text:
                    raise LLMCallError(f"LLM returned empty text output (finish_reason={finish_reason})")

                input_tokens, output_tokens = self._extract_usage_tokens(response)
                input_price_per_1m, output_price_per_1m = await self._resolve_model_pricing(selected_model)
                cost = compute_cost_usd(
                    input_tokens,
                    output_tokens,
                    input_price_per_1m,
                    output_price_per_1m,
                )
                usage_record = UsageRecord(
                    model=selected_model,
                    agent=agent_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )
                return LLMTextResult(text=text, usage=usage_record)
            except (RateLimitError, APITimeoutError, APIConnectionError, APIError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt >= self.settings.llm_max_retries:
                    break
                backoff = self.settings.llm_retry_backoff_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                break

        raise LLMCallError(f"LLM text call failed after {attempt} attempt(s): {last_error}")

    async def _resolve_model_pricing(self, model: str) -> tuple[float, float]:
        cached = self._pricing_cache.get(model)
        if cached is not None:
            return cached

        async with self._pricing_cache_lock:
            cached = self._pricing_cache.get(model)
            if cached is not None:
                return cached

            if not self.settings.openai_api_key:
                logger.warning("Pricing lookup skipped for model=%s because OPENAI_API_KEY is not configured.", model)
                return 0.0, 0.0

            try:
                payload, source_endpoint = await self._fetch_pricing_payload(model=model)
                resolved = resolve_model_pricing_from_payload(model, payload)
                logger.info(
                    "Pricing resolved model=%s source=%s input_per_1m=%s output_per_1m=%s",
                    model,
                    source_endpoint,
                    resolved[0],
                    resolved[1],
                )
                self._pricing_cache[model] = resolved
                return resolved
            except (httpx.HTTPError, PricingLookupError, ValueError) as exc:
                fallback = resolve_fallback_model_pricing(model)
                if fallback is not None:
                    logger.warning(
                        "Pricing endpoint failed for model=%s; using fallback rates input_per_1m=%s output_per_1m=%s reason=%s",
                        model,
                        fallback[0],
                        fallback[1],
                        exc,
                    )
                    self._pricing_cache[model] = fallback
                    return fallback
                logger.warning(
                    "Pricing lookup failed for model=%s via endpoint; falling back to zero-cost estimate. reason=%s",
                    model,
                    exc,
                )
                return 0.0, 0.0

    async def _fetch_pricing_payload(self, *, model: str) -> tuple[dict[str, Any], str]:
        base_url = (self.settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        timeout = self.settings.request_timeout_seconds
        candidates = [
            (f"{base_url}/models/pricing", {"model": model}),
            (f"{base_url}/models/pricing", None),
            (f"{base_url}/models/{model}", None),
        ]
        failures: list[str] = []

        async with httpx.AsyncClient(timeout=timeout) as client:
            for endpoint, params in candidates:
                response = await client.get(endpoint, headers=headers, params=params)
                if response.status_code >= 400:
                    body_preview = (response.text or "")[:220].replace("\n", " ")
                    failures.append(
                        f"{endpoint} status={response.status_code} body={body_preview!r}"
                    )
                    continue

                payload = response.json()
                if not isinstance(payload, dict):
                    failures.append(f"{endpoint} returned non-object JSON payload.")
                    continue
                return payload, endpoint

        raise httpx.HTTPError(
            "All pricing endpoints failed for model="
            f"{model}. Attempts: {' | '.join(failures) if failures else '(none)'}"
        )

    async def _create_response(
        self,
        *,
        agent_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        json_mode: bool,
    ) -> Any:
        request_kwargs: dict[str, Any] = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            "max_output_tokens": max_tokens,
        }
        if json_mode:
            request_kwargs["text"] = {"format": {"type": "json_object"}}

        logger.info(
            (
                "LLM request agent=%s model=%s api=responses json_mode=%s "
                "max_output_tokens=%d system_chars=%d user_chars=%d "
                "system_preview=%r user_preview=%r"
            ),
            agent_name,
            model,
            json_mode,
            max_tokens,
            len(system_prompt),
            len(user_prompt),
            system_prompt[:220],
            user_prompt[:220],
        )

        return await asyncio.wait_for(
            self._client.responses.create(**request_kwargs),
            timeout=self.settings.request_timeout_seconds,
        )

    @staticmethod
    def _to_response_dict(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            try:
                return response.model_dump()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(response, "to_dict"):
            try:
                return response.to_dict()
            except Exception:  # noqa: BLE001
                pass
        return {}

    @staticmethod
    def _extract_response_text_and_finish_reason(response: Any) -> tuple[str, str | None]:
        data = LLMClient._to_response_dict(response)

        text_parts: list[str] = []
        finish_reason: str | None = None

        direct_output_text = data.get("output_text")
        if isinstance(direct_output_text, str) and direct_output_text:
            text_parts.append(direct_output_text)
        elif isinstance(direct_output_text, list):
            text_parts.extend(str(part) for part in direct_output_text if part is not None)

        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if item_type == "output_text":
                    txt = item.get("text")
                    if txt:
                        text_parts.append(str(txt))
                    continue

                if item_type == "message":
                    content = item.get("content")
                    if isinstance(content, list):
                        for seg in content:
                            if not isinstance(seg, dict):
                                continue
                            seg_type = str(seg.get("type") or "")
                            if seg_type in {"output_text", "input_text", "text"}:
                                txt = seg.get("text") or seg.get("content")
                                if txt:
                                    text_parts.append(str(txt))
                            elif seg_type == "refusal":
                                refusal = seg.get("refusal")
                                if refusal:
                                    text_parts.append(str(refusal))
                    continue

        if not text_parts:
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                message = first.get("message") if isinstance(first, dict) else {}
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        text_parts.append(content)
                fr = first.get("finish_reason") if isinstance(first, dict) else None
                if fr is not None:
                    finish_reason = str(fr)

        if finish_reason is None:
            status = data.get("status")
            if isinstance(status, str):
                if status == "incomplete":
                    incomplete = data.get("incomplete_details")
                    reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
                    if reason:
                        finish_reason = "length" if "max_output_tokens" in str(reason) else str(reason)
                    else:
                        finish_reason = "incomplete"
                elif status == "completed":
                    finish_reason = "stop"
                else:
                    finish_reason = status

        text = "".join(text_parts).strip()
        return text, finish_reason

    @staticmethod
    def _extract_usage_tokens(response: Any) -> tuple[int, int]:
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = int(
                getattr(usage, "input_tokens", 0)
                or getattr(usage, "prompt_tokens", 0)
                or 0
            )
            output_tokens = int(
                getattr(usage, "output_tokens", 0)
                or getattr(usage, "completion_tokens", 0)
                or 0
            )
            return input_tokens, output_tokens

        data = LLMClient._to_response_dict(response)
        usage_data = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage_data, dict):
            input_tokens = int(usage_data.get("input_tokens") or usage_data.get("prompt_tokens") or 0)
            output_tokens = int(usage_data.get("output_tokens") or usage_data.get("completion_tokens") or 0)
            return input_tokens, output_tokens

        return 0, 0

    def _mock_result(self, *, agent_name: str, user_prompt: str) -> LLMResult:
        samples = [
            "I can ground this in the repository evidence and propose a practical next step.",
            "I see a risk around assumptions that are not yet validated in code.",
            "I suggest a smaller first milestone and an explicit unknowns list.",
        ]
        payload = {
            "message": f"[{agent_name} mock] {random.choice(samples)}",
            "references": [],
            "risks": ["Mock mode is enabled; quality is not representative."],
            "open_questions": ["Provide OPENAI_API_KEY for real multi-agent behavior."],
            "confidence_delta": 0.0,
            "engagement_delta": 0.0,
            "caution_delta": 0.0,
            "friction_delta": 0.0,
        }

        approximate_in = min(1500, max(200, len(user_prompt) // 3))
        approximate_out = 180
        usage_record = UsageRecord(
            model="mock-llm",
            agent=agent_name,
            input_tokens=approximate_in,
            output_tokens=approximate_out,
            cost_usd=0.0,
        )
        return LLMResult(payload=payload, usage=usage_record)
