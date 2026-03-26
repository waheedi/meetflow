import unittest
from unittest.mock import AsyncMock

from app.core.config import Settings
from app.services.llm_client import LLMClient


class TestLLMClientPricing(unittest.IsolatedAsyncioTestCase):
    async def test_pricing_lookup_is_cached_per_model(self) -> None:
        settings = Settings(OPENAI_API_KEY="test-key")
        client = LLMClient(settings)

        client._fetch_pricing_payload = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                {"data": [{"id": "gpt-5-mini", "input_per_1m": 0.25, "output_per_1m": 2.0}]},
                "mock://pricing",
            )
        )

        first = await client._resolve_model_pricing("gpt-5-mini")
        second = await client._resolve_model_pricing("gpt-5-mini")

        self.assertEqual(first, (0.25, 2.0))
        self.assertEqual(second, (0.25, 2.0))
        client._fetch_pricing_payload.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_pricing_lookup_returns_zero_if_api_key_missing(self) -> None:
        settings = Settings(OPENAI_API_KEY="")
        client = LLMClient(settings)
        self.assertEqual(await client._resolve_model_pricing("gpt-5"), (0.0, 0.0))

    async def test_pricing_lookup_uses_fallback_on_fetch_failure(self) -> None:
        settings = Settings(OPENAI_API_KEY="test-key")
        client = LLMClient(settings)
        client._fetch_pricing_payload = AsyncMock(side_effect=ValueError("boom"))  # type: ignore[method-assign]

        self.assertEqual(await client._resolve_model_pricing("gpt-5"), (0.15, 0.60))


if __name__ == "__main__":
    unittest.main()
