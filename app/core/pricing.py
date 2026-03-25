from __future__ import annotations

# Defaults are intentionally conservative placeholders.
DEFAULT_MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-5": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gpt-5.3-codex": {"input_per_1m": 0.15, "output_per_1m": 0.60},
}


def resolve_model_pricing(model: str) -> tuple[float, float]:
    catalog = DEFAULT_MODEL_PRICING

    if model in catalog:
        item = catalog[model]
        return float(item["input_per_1m"]), float(item["output_per_1m"])

    # Prefix fallback for model variants (for example provider-specific suffixes).
    for key, item in catalog.items():
        if model.startswith(key):
            return float(item["input_per_1m"]), float(item["output_per_1m"])

    fallback = catalog["gpt-5"]
    return float(fallback["input_per_1m"]), float(fallback["output_per_1m"])
