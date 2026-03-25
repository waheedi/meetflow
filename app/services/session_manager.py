from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.schemas.models import ChatMessage, RepoContext, SessionState, UsageRecord


def _event_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}

    def create_session(self, repo_context: RepoContext) -> SessionState:
        session_id = str(uuid.uuid4())
        session = SessionState(id=session_id, repo_context=repo_context)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionState | None:
        return self.sessions.get(session_id)

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        session = self._require_session(session_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with session.lock:
            session.subscribers.add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        async with session.lock:
            session.subscribers.discard(queue)

    async def publish_event(self, session: SessionState, event_type: str, payload: dict) -> None:
        event = {
            "type": event_type,
            "timestamp": _event_now(),
            "session_id": session.id,
            **payload,
        }

        async with session.lock:
            subscribers = list(session.subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Avoid blocking all other subscribers if one consumer is slow.
                continue

    async def add_message(self, session: SessionState, message: ChatMessage) -> None:
        async with session.lock:
            session.messages.append(message)
        await self.publish_event(
            session,
            "chat_message",
            {
                "message": {
                    "id": message.id,
                    "role": message.role,
                    "author": message.author,
                    "content": message.content,
                    "timestamp": message.timestamp,
                    "references": message.references,
                    "metadata": message.metadata,
                }
            },
        )

    async def add_usage(self, session: SessionState, usage: UsageRecord) -> None:
        async with session.lock:
            session.usage_records.append(usage)
            session.total_input_tokens += usage.input_tokens
            session.total_output_tokens += usage.output_tokens
            session.total_cost_usd = round(session.total_cost_usd + usage.cost_usd, 6)

            total_payload = {
                "total_input_tokens": session.total_input_tokens,
                "total_output_tokens": session.total_output_tokens,
                "total_cost_usd": session.total_cost_usd,
                "last_usage": {
                    "agent": usage.agent,
                    "model": usage.model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": usage.cost_usd,
                },
            }

        await self.publish_event(session, "cost_update", total_payload)

    async def enqueue_user_input(
        self,
        session_id: str,
        content: str,
        handler: Callable[[SessionState, str], Awaitable[None]],
    ) -> int:
        session = self._require_session(session_id)
        async with session.lock:
            session.pending_inputs.append(content)
            queue_size = len(session.pending_inputs)
            should_start = not session.running
            if should_start:
                session.running = True

        if should_start:
            asyncio.create_task(self._drain_queue(session, handler))

        return queue_size

    async def pop_pending_inputs(self, session: SessionState) -> list[str]:
        async with session.lock:
            if not session.pending_inputs:
                return []
            items = list(session.pending_inputs)
            session.pending_inputs.clear()
            return items

    async def _drain_queue(
        self,
        session: SessionState,
        handler: Callable[[SessionState, str], Awaitable[None]],
    ) -> None:
        try:
            while True:
                async with session.lock:
                    if not session.pending_inputs:
                        session.running = False
                        break
                    content = session.pending_inputs.popleft()

                await handler(session, content)
        except Exception as exc:  # noqa: BLE001
            await self.publish_event(
                session,
                "agent_error",
                {"message": f"Unhandled processing error: {exc}"},
            )
            async with session.lock:
                session.running = False

    def _require_session(self, session_id: str) -> SessionState:
        session = self.get_session(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")
        return session
