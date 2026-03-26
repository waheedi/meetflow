import unittest

from app.core.pricing import (
    PricingLookupError,
    parse_pricing_docs_rows,
    resolve_model_pricing_from_docs_catalog,
    resolve_model_pricing_from_payload,
)


class TestPricingExtraction(unittest.TestCase):
    def test_extracts_direct_price_fields(self) -> None:
        payload = {"input_per_1m": 1.25, "output_per_1m": 10.0}
        self.assertEqual(resolve_model_pricing_from_payload("gpt-5", payload), (1.25, 10.0))

    def test_extracts_nested_pricing_fields(self) -> None:
        payload = {"pricing": {"input_price_per_1m": "2.0", "output_price_per_1m": "8.0"}}
        self.assertEqual(resolve_model_pricing_from_payload("gpt-5.3-codex", payload), (2.0, 8.0))

    def test_extracts_deep_nested_pricing_fields(self) -> None:
        payload = {
            "data": [
                {
                    "id": "gpt-5.4-mini",
                    "pricing": {
                        "token_pricing": {
                            "input": {"text": {"per_1m_tokens_usd": "$0.05 / 1M tokens"}},
                            "output": {"text": {"per_1m_tokens_usd": "$0.20 / 1M tokens"}},
                        }
                    },
                }
            ]
        }
        self.assertEqual(resolve_model_pricing_from_payload("gpt-5.4-mini", payload), (0.05, 0.20))

    def test_extracts_exact_model_from_data_list(self) -> None:
        payload = {
            "data": [
                {"id": "gpt-5", "input_per_1m": 1.0, "output_per_1m": 2.0},
                {"id": "gpt-5-mini", "input_per_1m": 0.25, "output_per_1m": 2.0},
            ]
        }
        self.assertEqual(resolve_model_pricing_from_payload("gpt-5-mini", payload), (0.25, 2.0))

    def test_extracts_prefix_match_from_data_list(self) -> None:
        payload = {"data": [{"id": "gpt-5", "input_per_1m": 1.0, "output_per_1m": 2.0}]}
        self.assertEqual(
            resolve_model_pricing_from_payload("gpt-5-mini-2026-03-25", payload),
            (1.0, 2.0),
        )

    def test_extracts_model_key_block(self) -> None:
        payload = {"gpt-5.3-codex": {"input_per_1m": 2.0, "output_per_1m": 8.0}}
        self.assertEqual(resolve_model_pricing_from_payload("gpt-5.3-codex", payload), (2.0, 8.0))

    def test_raises_when_model_not_found(self) -> None:
        payload = {"data": [{"id": "other-model", "input_per_1m": 1.0, "output_per_1m": 2.0}]}
        with self.assertRaises(PricingLookupError):
            resolve_model_pricing_from_payload("gpt-5", payload)

    def test_parse_pricing_docs_rows(self) -> None:
        html_snippet = (
            'props="{&quot;rows&quot;:[1,[[1,[[0,&quot;gpt-5.4-mini&quot;],[0,0.75],[0,0.075],[0,4.5]]],'
            '[1,[[0,&quot;gpt-5.3-codex&quot;],[0,1.75],[0,0.175],[0,14]]]]]}"'
        )
        catalog = parse_pricing_docs_rows(html_snippet)
        self.assertEqual(catalog["gpt-5.4-mini"], (0.75, 4.5))
        self.assertEqual(catalog["gpt-5.3-codex"], (1.75, 14.0))

    def test_resolve_model_pricing_from_docs_catalog(self) -> None:
        catalog = {
            "gpt-5.4-mini": (0.75, 4.5),
            "gpt-5.3-codex": (1.75, 14.0),
        }
        self.assertEqual(resolve_model_pricing_from_docs_catalog("gpt-5.4-mini", catalog), (0.75, 4.5))
        self.assertEqual(
            resolve_model_pricing_from_docs_catalog("gpt-5.4-mini-2026-03-25", catalog),
            (0.75, 4.5),
        )


if __name__ == "__main__":
    unittest.main()
