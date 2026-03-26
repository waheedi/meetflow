from __future__ import annotations

from typing import Any


class PricingLookupError(Exception):
    """Raised when model pricing cannot be extracted from provider payloads."""


_INPUT_KEYS = (
    "input_per_1m",
    "input_price_per_1m",
    "prompt_per_1m",
    "prompt_price_per_1m",
)
_OUTPUT_KEYS = (
    "output_per_1m",
    "output_price_per_1m",
    "completion_per_1m",
    "completion_price_per_1m",
)


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_price_pair(item: dict[str, Any]) -> tuple[float, float] | None:
    input_value: float | None = None
    output_value: float | None = None

    for key in _INPUT_KEYS:
        input_value = _as_float(item.get(key))
        if input_value is not None:
            break

    for key in _OUTPUT_KEYS:
        output_value = _as_float(item.get(key))
        if output_value is not None:
            break

    pricing_block = item.get("pricing")
    if isinstance(pricing_block, dict):
        if input_value is None:
            for key in _INPUT_KEYS:
                input_value = _as_float(pricing_block.get(key))
                if input_value is not None:
                    break
        if output_value is None:
            for key in _OUTPUT_KEYS:
                output_value = _as_float(pricing_block.get(key))
                if output_value is not None:
                    break

    if input_value is None or output_value is None:
        return None
    return input_value, output_value


def _extract_from_list(model: str, items: list[Any]) -> tuple[float, float] | None:
    exact_match: tuple[float, float] | None = None
    prefix_match: tuple[float, float] | None = None

    for raw in items:
        if not isinstance(raw, dict):
            continue

        model_id = str(raw.get("id") or raw.get("model") or raw.get("name") or "")
        price_pair = _extract_price_pair(raw)
        if price_pair is None:
            continue
        if model_id == model:
            exact_match = price_pair
            break
        if model_id and model.startswith(model_id) and prefix_match is None:
            prefix_match = price_pair

    return exact_match or prefix_match


def resolve_model_pricing_from_payload(model: str, payload: dict[str, Any]) -> tuple[float, float]:
    """Extract input/output token prices from the provider pricing endpoint payload."""
    direct = _extract_price_pair(payload)
    if direct is not None:
        return direct

    for key in ("data", "models", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            matched = _extract_from_list(model, value)
            if matched is not None:
                return matched

    per_model_value = payload.get(model)
    if isinstance(per_model_value, dict):
        matched = _extract_price_pair(per_model_value)
        if matched is not None:
            return matched

    raise PricingLookupError(f"Could not resolve pricing for model '{model}' from endpoint payload.")
