import os
import unittest

from app.core.config import Settings
from app.core.pricing import resolve_model_pricing_from_payload
from app.services.llm_client import LLMClient


class TestLivePricingEndpoint(unittest.IsolatedAsyncioTestCase):
    async def test_live_pricing_for_configured_agent_model(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            self.skipTest("OPENAI_API_KEY is not set in environment.")

        model = os.getenv("LLM_MODEL_AGENT", "gpt-5.3-codex")
        settings = Settings(OPENAI_API_KEY=api_key)
        client = LLMClient(settings)
        payload, source = await client._fetch_pricing_payload(model=model)
        input_per_1m, output_per_1m = resolve_model_pricing_from_payload(model, payload)

        self.assertGreater(input_per_1m, 0.0)
        self.assertGreater(output_per_1m, 0.0)
        self.assertIsInstance(source, str)
        self.assertTrue(source.startswith("https://") or source.startswith("http://"))


if __name__ == "__main__":
    unittest.main()
