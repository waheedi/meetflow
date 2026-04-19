from __future__ import annotations

from textwrap import dedent

from app.domain.personas import FACILITATOR_PERSONA_HINTS, get_persona_system_prompt
from app.schemas.models import AgentProfile, ChatMessage, RepoEvidence, SessionState


def _truncate(text: str, limit: int = 900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def format_evidence(evidence: list[RepoEvidence]) -> str:
    if not evidence:
        return "No indexed repository evidence was found."

    lines: list[str] = []
    for item in evidence:
        symbols = ", ".join(item.symbols[:6]) if item.symbols else "(no symbols extracted)"
        lines.append(f"- file: {item.path}")
        lines.append(f"  symbols: {symbols}")
        lines.append(f"  excerpt: {_truncate(item.excerpt, 280)}")
    return "\n".join(lines)


def format_transcript(messages: list[ChatMessage], max_items: int = 12) -> str:
    if not messages:
        return "No prior messages yet."

    tail = messages[-max_items:]
    lines: list[str] = []
    for msg in tail:
        lines.append(f"[{msg.author}] {_truncate(msg.content, 420)}")
    return "\n".join(lines)


def format_team_state(session: SessionState) -> str:
    chunks: list[str] = []
    for name, state in session.agent_states.items():
        chunks.append(
            f"{name}: confidence={state.get('confidence', 0):.2f}, "
            f"engagement={state.get('engagement', 0):.2f}, "
            f"caution={state.get('caution', 0):.2f}, "
            f"friction={state.get('friction', 0):.2f}"
        )
    return "\n".join(chunks)


def build_agent_system_prompt(profile: AgentProfile) -> str:
    persona_specific = get_persona_system_prompt(profile.name)
    return dedent(
        f"""
        You are {profile.name}, {profile.role} in a 7-person engineering team simulation.

        Persona baseline:
        {profile.traits}

        Persona-specific instructions:
        {persona_specific}

        Rules you must follow:
        1) Stay in character at all times.
        2) Respond to teammates naturally: agree, disagree, challenge, or build on earlier comments.
        3) Ground technical claims in the repository evidence provided. Cite concrete file paths and symbols.
        4) Avoid generic advice. If unknown, say exactly what evidence is missing.
        5) Keep output concise but substantive.

        Output format:
        - If you need additional context first, output exactly one line:
          REQUEST_CONTEXT: path/to/file/or/folder
          Then add one brief sentence explaining why.
        - Otherwise provide final answer in this markdown structure:
          ANALYSIS:
          <your answer>

          REFERENCES:
          - path/to/file.py:SymbolOrArea

          RISKS:
          - risk 1

          OPEN_QUESTIONS:
          - question 1

          STATE_DELTAS:
          confidence_delta: <number between -0.20 and 0.20>
          engagement_delta: <number between -0.20 and 0.20>
          caution_delta: <number between -0.20 and 0.20>
          friction_delta: <number between -0.20 and 0.20>
        """
    ).strip()


def build_agent_user_prompt(
    *,
    session: SessionState,
    profile: AgentProfile,
    operator_message: str,
    relevant_evidence: list[RepoEvidence],
    additional_context: str | None = None,
    force_final: bool = False,
) -> str:
    context = session.repo_context
    manifests_summary = "\n".join(f"- {name}" for name in sorted(context.manifests.keys())) or "- none"
    manifest_snippets = []
    for name in sorted(context.manifests.keys())[:5]:
        snippet = _truncate(context.manifests[name], 520)
        manifest_snippets.append(f"- {name}:\n{snippet}")
    manifest_text = "\n".join(manifest_snippets) if manifest_snippets else "- none"
    architecture = "\n".join(f"- {note}" for note in context.architecture_notes)
    additional_block = (
        f"\n\nAdditional requested context:\n{additional_context}\n"
        if additional_context
        else ""
    )
    request_instruction = (
        "Provide final answer now. Do not request more context in this response."
        if force_final
        else "If current context is insufficient, request more by replying with REQUEST_CONTEXT: <path>."
    )

    return dedent(
        f"""
        Operator message:
        {operator_message}

        Repository root:
        {context.root_path}

        Detected stack:
        {', '.join(context.stack) if context.stack else 'Unknown'}

        Dependency manifests:
        {manifests_summary}

        Manifest snippets:
        {manifest_text}

        Architecture notes:
        {architecture}

        Repository tree:
        {_truncate(context.repo_tree, 4200)}

        Relevant repository evidence:
        {format_evidence(relevant_evidence)}
        {additional_block}

        Current transcript (latest at bottom):
        {format_transcript(session.messages)}

        Team state snapshot:
        {format_team_state(session)}

        Important interaction constraints:
        - Explicitly reference at least one teammate by name and react to what they said.
        - Include at least two concrete repository references in "references".
        - Surface at least one risk and one open question.
        - Sarah should drive clarity on decisions, owners, and next steps.
        - Jonas should challenge whether scope ties to user and business outcomes.
        - Belal should pressure-test portfolio impact and prioritization logic.
        - Michael should challenge long-term technical risk and architecture quality.
        - Kai, Tamer, and Lara should ground plans in concrete implementation details.

        Decision rule:
        - {request_instruction}

        You are speaking as {profile.name}. Keep this realistic and useful for engineering planning.
        """
    ).strip()


def build_facilitator_system_prompt() -> str:
    return dedent(
        f"""
        You are the neutral facilitator for this engineering team simulation.
        Consolidate the team discussion into clear decisions and disagreements.

        Persona and team-dynamics guidance:
        {FACILITATOR_PERSONA_HINTS}

        Reply as JSON with this exact shape:
        {{
          "summary": "short synthesis",
          "consensus": ["point 1"],
          "disagreements": ["point 1"],
          "action_plan": ["step 1", "step 2"],
          "risks": ["risk 1"],
          "unknowns": ["unknown 1"]
        }}
        """
    ).strip()


def build_facilitator_user_prompt(operator_message: str, round_messages: list[ChatMessage]) -> str:
    transcript = format_transcript(round_messages, max_items=40)
    return dedent(
        f"""
        Operator prompt for this round:
        {operator_message}

        Team discussion this round:
        {transcript}

        Produce a clear converged outcome.
        If there is no agreement on some points, list those explicitly in disagreements.
        """
    ).strip()
