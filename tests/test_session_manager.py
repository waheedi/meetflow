import asyncio
import unittest

from app.schemas.models import ChatMessage, RepoContext, SessionState, UsageRecord, utc_now_iso
from app.services.session_manager import SessionManager


def _repo_context() -> RepoContext:
    return RepoContext(
        root_path="/tmp/repo",
        stack=["Python"],
        manifests={},
        architecture_notes=[],
        repo_tree=".",
        evidence=[],
    )


class TestSessionManager(unittest.IsolatedAsyncioTestCase):
    async def test_add_message_publishes_chat_event(self) -> None:
        manager = SessionManager()
        session = manager.create_session(_repo_context())
        queue = await manager.subscribe(session.id)
        self.addAsyncCleanup(manager.unsubscribe, session.id, queue)

        message = ChatMessage(
            id="m1",
            role="human",
            author="Operator",
            content="hello",
            timestamp=utc_now_iso(),
        )
        await manager.add_message(session, message)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        self.assertEqual(event["type"], "chat_message")
        self.assertEqual(event["message"]["id"], "m1")
        self.assertEqual(event["message"]["content"], "hello")
        self.assertEqual(len(session.messages), 1)

    async def test_add_usage_accumulates_totals_and_emits_event(self) -> None:
        manager = SessionManager()
        session = manager.create_session(_repo_context())
        queue = await manager.subscribe(session.id)
        self.addAsyncCleanup(manager.unsubscribe, session.id, queue)

        usage = UsageRecord(
            model="gpt-5-mini",
            agent="Sarah",
            input_tokens=1000,
            output_tokens=2000,
            cost_usd=0.012345,
        )
        await manager.add_usage(session, usage)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        self.assertEqual(event["type"], "cost_update")
        self.assertEqual(event["total_input_tokens"], 1000)
        self.assertEqual(event["total_output_tokens"], 2000)
        self.assertEqual(event["total_cost_usd"], 0.012345)
        self.assertEqual(session.total_input_tokens, 1000)
        self.assertEqual(session.total_output_tokens, 2000)

    async def test_pop_pending_inputs_clears_queue(self) -> None:
        manager = SessionManager()
        session = SessionState(id="s1", repo_context=_repo_context())
        session.pending_inputs.append("one")
        session.pending_inputs.append("two")

        drained = await manager.pop_pending_inputs(session)
        self.assertEqual(drained, ["one", "two"])
        self.assertEqual(len(session.pending_inputs), 0)

    async def test_enqueue_user_input_drains_in_order(self) -> None:
        manager = SessionManager()
        session = manager.create_session(_repo_context())
        calls: list[str] = []

        async def handler(_session: SessionState, content: str) -> None:
            calls.append(content)
            await asyncio.sleep(0.01)

        size1 = await manager.enqueue_user_input(session.id, "first", handler)
        size2 = await manager.enqueue_user_input(session.id, "second", handler)
        self.assertEqual(size1, 1)
        self.assertGreaterEqual(size2, 1)

        for _ in range(60):
            await asyncio.sleep(0.02)
            if calls == ["first", "second"] and not session.running:
                break

        self.assertEqual(calls, ["first", "second"])
        self.assertFalse(session.running)


if __name__ == "__main__":
    unittest.main()
