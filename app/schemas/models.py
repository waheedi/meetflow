from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Literal, Optional

from pydantic import BaseModel, Field, model_validator


RoleType = Literal["human", "agent", "facilitator", "system"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RepoEvidence:
    path: str
    symbols: list[str]
    excerpt: str


@dataclass
class RepoContext:
    root_path: str
    stack: list[str]
    manifests: dict[str, str]
    architecture_notes: list[str]
    repo_tree: str
    evidence: list[RepoEvidence]


@dataclass
class AgentProfile:
    name: str
    role: str
    traits: str
    visual_color: str
    avatar: str


@dataclass
class ChatMessage:
    id: str
    role: RoleType
    author: str
    content: str
    timestamp: str
    references: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageRecord:
    model: str
    agent: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass
class SessionState:
    id: str
    repo_context: RepoContext
    messages: list[ChatMessage] = field(default_factory=list)
    usage_records: list[UsageRecord] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0

    agent_states: dict[str, dict[str, float]] = field(default_factory=dict)

    pending_inputs: Deque[str] = field(default_factory=deque)
    running: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribers: set[asyncio.Queue] = field(default_factory=set)


class CreateSessionRequest(BaseModel):
    repo_path: Optional[str] = Field(
        default=None,
        description="Local repository path, or any source string (git/archive URL is also accepted).",
    )
    source: Optional[str] = Field(
        default=None,
        description="Optional explicit source input. Supports local dir, git URL, archive URL/path.",
    )
    source_type: Optional[str] = Field(
        default="auto",
        description="auto | local | git | archive",
    )
    ref: Optional[str] = Field(
        default=None,
        description="Optional git branch/tag/ref used when source resolves as git.",
    )

    @model_validator(mode="after")
    def validate_source_fields(self) -> "CreateSessionRequest":
        if not (self.repo_path or self.source):
            raise ValueError("Either repo_path or source must be provided.")
        return self


class CreateSessionResponse(BaseModel):
    session_id: str
    repo_path: str
    source_kind: str
    cache_hit: bool
    stack: list[str]
    repo_summary: str


class SendMessageRequest(BaseModel):
    session_id: str
    content: str


class SendMessageResponse(BaseModel):
    status: str
    queued_messages: int


class SessionSnapshotResponse(BaseModel):
    session_id: str
    repo_path: str
    stack: list[str]
    messages: list[dict[str, Any]]
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
