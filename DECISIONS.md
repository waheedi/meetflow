# DECISIONS.md

This log captures key implementation decisions and trade-offs made while building the challenge solution.

## 1) Backend Framework: FastAPI

- Decision: Use FastAPI for the backend API and app serving.
- Rationale: Rapid implementation, strong async support, clean request/response modeling.
- Trade-off: A Node-first stack might align with frontend-heavy teams, but FastAPI enabled faster reliable orchestration under time constraints.

## 2) Frontend Scope: Plain HTML/CSS/JS

- Decision: Use a minimal vanilla frontend rather than a full SPA framework.
- Rationale: Keeps focus on required behavior (chat flow, identities, cost counter, thinking states) and reduces setup risk.
- Trade-off: Less component abstraction than React/Vue, but significantly faster to deliver and easier for reviewers to run.

## 3) Transport: SSE instead of WebSockets

- Decision: Use Server-Sent Events (`/api/stream/{session_id}`) for UI updates.
- Rationale: One-way server push is sufficient for this use case and simpler to implement robustly.
- Trade-off: WebSockets offer richer bidirectional patterns, but introduce extra complexity with little challenge benefit, not required in this project scope but was already done.

## 4) Agent Execution Model: Sequential turns

- Decision: Execute all 7 agents in fixed sequence for each operator prompt.
- Rationale: Requirement requires true discussion behavior where agents react to prior statements.
- Trade-off: Higher latency than parallel generation, but stronger conversational coherence and easier state correctness.

## 5) Conversation State Safety: Queue + per-session lock

- Decision: Serialize operator inputs through a session queue and protect shared state with `asyncio.Lock`.
- Rationale: Prevents race conditions, duplicated messages, and state corruption under concurrent inputs.
- Trade-off: Queueing can delay response start under rapid input bursts, but ensures deterministic state transitions.

## 6) LLM Provider and Models: OpenAI GPT-5 family

- Decision: Use OpenAI with:
  - `gpt-5.3-codex` for persona turns
  - `gpt-5` for facilitator synthesis
- Rationale: Aligns with user directive and provides strong code reasoning with high-quality consolidation.
- Trade-off: Higher token cost than lighter models; mitigated through explicit cost tracking and configurable rates.

## 7) Response Contract: JSON-mode outputs

- Decision: Enforce JSON schema-like response shape from agents and facilitator.
- Rationale: Needed for robust parsing of references, risks, unknowns, and state deltas.
- Trade-off: Slightly constrains free-form writing style, but improves runtime resilience.

## 8) Codebase Grounding Strategy

- Decision: Build a repository analyzer that indexes manifests, code files, symbol hints, and excerpts.
- Trade-off: Lightweight indexing may miss deep semantic relationships.

## 9) Cost Tracking

- Decision: Compute cumulative USD cost from API usage (`prompt_tokens`, `completion_tokens`) and runtime model pricing fetched from OpenAI endpoint (`/v1/models/pricing`), keyed by the active env-selected model IDs.
- Trade-off: Removes hardcoded pricing drift, but introduces dependency on provider pricing endpoint availability.


## 10) Failure Handling

- Decision: Retries with exponential backoff for transient LLM failures; emit structured agent error events when exhausted.
- Rationale: Prevent crashes and make failures visible to operator in chat.
- Trade-off: Slightly longer worst-case response latency during outages.

## 11) Emergent Team Dynamics Approach

- Decision: Persona prompts encode interpersonal tensions and behavior tendencies; each turn includes evolving state snapshot.
- Rationale: Encourages natural interaction patterns rather than hardcoded scripted outcomes.
- Trade-off: Behavior remains probabilistic and model-dependent; mitigated by explicit prompt constraints and sequential context.


## 12) Source Resolution Strategy (local + git + archives)

- Decision: Add a dedicated `SourceResolver` service that normalizes local paths, git URLs, and archive URLs/files into a local directory before analysis.
- Rationale: Real evaluation workflows often start from remote repositories or exported archives, not only local folders.
- Trade-off: Additional complexity around downloads/extraction, mitigated via caching and strict safety checks.

## 13) Archive Safety + Limits

- Decision: Enforce secure extraction rules (no traversal paths, no symlinks/links) and hard limits on downloaded bytes, extracted bytes, and file count.
- Rationale: Prevent malicious archive behavior and resource exhaustion during automated source ingestion.
- Trade-off: Some unusual archives may be rejected, but safer default behavior is preferred for a challenge demo tool.
