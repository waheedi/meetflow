from __future__ import annotations

import html as html_lib
import re
from typing import Any


class PricingLookupError(Exception):
    """Raised when model pricing cannot be extracted from provider payloads."""


# Used only when provider pricing endpoint is unavailable or incompatible.
FALLBACK_MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-5": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gpt-5-mini": {"input_per_1m": 0.05, "output_per_1m": 0.20},
    "gpt-5.3-codex": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gpt-5.4-mini": {"input_per_1m": 0.05, "output_per_1m": 0.20},
}

_SERIALIZED_ROW_PATTERN = re.compile(
    r'\[\[0,"(?P<model>[^"]+)"\],\[0,(?P<input>[^,\]]+)\],\[0,(?P<cached>[^,\]]+)\],\[0,(?P<output>[^,\]]+)\]\]'
)


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
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    return None


def _as_float_token(value: str) -> float | None:
    raw = value.strip()
    if raw in {"", "null", "None", "-", '""', "''"}:
        return None
    return _as_float(raw)


def _flatten_numeric_paths(value: Any, path: str = "") -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_str = str(key)
            next_path = f"{path}.{key_str}" if path else key_str
            pairs.extend(_flatten_numeric_paths(item, next_path))
        return pairs

    if isinstance(value, list):
        for idx, item in enumerate(value):
            next_path = f"{path}[{idx}]"
            pairs.extend(_flatten_numeric_paths(item, next_path))
        return pairs

    parsed = _as_float(value)
    if parsed is not None:
        pairs.append((path.lower(), parsed))
    return pairs


def _select_nested_price(pairs: list[tuple[str, float]], kind: str) -> float | None:
    if kind == "input":
        role_hints = ("input", "prompt")
    else:
        role_hints = ("output", "completion")
    scale_hints = ("per_1m", "per1m", "1m", "million")

    for path, numeric in pairs:
        if any(hint in path for hint in role_hints) and any(hint in path for hint in scale_hints):
            return numeric
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

    # Fallback for nested provider payload shapes.
    if input_value is None or output_value is None:
        flattened = _flatten_numeric_paths(item)
        if input_value is None:
            input_value = _select_nested_price(flattened, "input")
        if output_value is None:
            output_value = _select_nested_price(flattened, "output")

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
    has_direct_price_keys = any(key in payload for key in (_INPUT_KEYS + _OUTPUT_KEYS))
    has_direct_pricing_block = isinstance(payload.get("pricing"), dict)
    if has_direct_price_keys or has_direct_pricing_block:
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


def resolve_fallback_model_pricing(model: str) -> tuple[float, float] | None:
    if model in FALLBACK_MODEL_PRICING:
        item = FALLBACK_MODEL_PRICING[model]
        return float(item["input_per_1m"]), float(item["output_per_1m"])

    for key, item in FALLBACK_MODEL_PRICING.items():
        if model.startswith(key):
            return float(item["input_per_1m"]), float(item["output_per_1m"])
    return None


def parse_pricing_docs_rows(html_text: str) -> dict[str, tuple[float, float]]:
    """
    Parse OpenAI pricing docs page serialized table rows.
    Returns a mapping: model_id -> (input_per_1m, output_per_1m).
    """
    text = html_lib.unescape(html_text)
    catalog: dict[str, tuple[float, float]] = {}
    for match in _SERIALIZED_ROW_PATTERN.finditer(text):
        model = (match.group("model") or "").strip()
        input_token = _as_float_token(match.group("input") or "")
        output_token = _as_float_token(match.group("output") or "")
        if not model or input_token is None or output_token is None:
            continue
        # Keep first occurrence because the docs page contains multiple tabs
        # (for example standard/priority); query param preselects target tab first.
        if model not in catalog:
            catalog[model] = (input_token, output_token)
    return catalog


def resolve_model_pricing_from_docs_catalog(
    model: str,
    catalog: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    if model in catalog:
        return catalog[model]

    for key, values in catalog.items():
        if model.startswith(key):
            return values
    return None
