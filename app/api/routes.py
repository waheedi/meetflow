from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.domain.personas import AGENT_PROFILES, initial_agent_state
from app.schemas.models import (
    ChatMessage,
    CreateSessionRequest,
    CreateSessionResponse,
    SendMessageRequest,
    SendMessageResponse,
    SessionSnapshotResponse,
)
from app.services.llm_client import LLMClient
from app.services.orchestrator import AgentOrchestrator
from app.services.repository_analyzer import RepositoryAnalyzer
from app.services.session_manager import SessionManager
from app.services.source_resolver import SourceResolver, SourceResolverError

settings = get_settings()
repo_analyzer = RepositoryAnalyzer()
session_manager = SessionManager()
llm_client = LLMClient(settings)
source_resolver = SourceResolver(settings)
orchestrator = AgentOrchestrator(
    llm_client=llm_client,
    session_manager=session_manager,
    repository_analyzer=repo_analyzer,
    settings=settings,
)

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.post("/api/session", response_model=CreateSessionResponse)
async def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    source_input = payload.source or payload.repo_path or ""

    try:
        resolved = source_resolver.resolve(
            source_input=source_input,
            source_type=payload.source_type,
            ref=payload.ref,
        )
        repo_context = repo_analyzer.analyze(resolved.local_path)
    except (ValueError, SourceResolverError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session = session_manager.create_session(repo_context)
    session.agent_states = initial_agent_state()

    roster = ", ".join(f"{name} ({profile.role})" for name, profile in AGENT_PROFILES.items())
    system_message = ChatMessage(
        id=f"system-{session.id}",
        role="system",
        author="System",
        content=(
            f"Session initialized for `{repo_context.root_path}`. "
            f"Detected stack: {', '.join(repo_context.stack) if repo_context.stack else 'Unknown'}. "
            f"Source kind: {resolved.source_kind} (cache hit: {resolved.cache_hit}). "
            f"Active team: {roster}."
        ),
        timestamp=_now(),
    )
    await session_manager.add_message(session, system_message)

    return CreateSessionResponse(
        session_id=session.id,
        repo_path=repo_context.root_path,
        source_kind=resolved.source_kind,
        cache_hit=resolved.cache_hit,
        stack=repo_context.stack,
        repo_summary="; ".join(repo_context.architecture_notes[:3]),
    )


@app.post("/api/message", response_model=SendMessageResponse)
async def send_message(payload: SendMessageRequest) -> SendMessageResponse:
    session = session_manager.get_session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    queued = await session_manager.enqueue_user_input(
        payload.session_id,
        payload.content.strip(),
        orchestrator.process_user_input,
    )
    return SendMessageResponse(status="queued", queued_messages=queued)


@app.get("/api/session/{session_id}", response_model=SessionSnapshotResponse)
async def get_session_snapshot(session_id: str) -> SessionSnapshotResponse:
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async with session.lock:
        messages = [
            {
                "id": m.id,
                "role": m.role,
                "author": m.author,
                "content": m.content,
                "timestamp": m.timestamp,
                "references": m.references,
                "metadata": m.metadata,
            }
            for m in session.messages
        ]
        total_input_tokens = session.total_input_tokens
        total_output_tokens = session.total_output_tokens
        total_cost_usd = session.total_cost_usd

    return SessionSnapshotResponse(
        session_id=session.id,
        repo_path=session.repo_context.root_path,
        stack=session.repo_context.stack,
        messages=messages,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cost_usd=total_cost_usd,
    )


@app.get("/api/stream/{session_id}")
async def stream_events(request: Request, session_id: str) -> StreamingResponse:
    if not session_manager.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    queue = await session_manager.subscribe(session_id)

    async def event_generator() -> AsyncIterator[str]:
        try:
            yield "event: session_status\ndata: {\"type\":\"session_status\",\"status\":\"connected\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    payload = json.dumps(event, ensure_ascii=False)
                    yield f"event: {event['type']}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield "event: heartbeat\ndata: {\"type\":\"heartbeat\"}\n\n"
        finally:
            await session_manager.unsubscribe(session_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/")
async def frontend_index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/{_:path}")
async def frontend_fallback(_: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not found",
            "message": "Use / for UI, /api/* for API endpoints.",
        },
    )
