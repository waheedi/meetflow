def compute_cost_usd(input_tokens: int, output_tokens: int, input_per_1m: float, output_per_1m: float) -> float:
    input_cost = (input_tokens / 1_000_000) * input_per_1m
    output_cost = (output_tokens / 1_000_000) * output_per_1m
    return round(input_cost + output_cost, 6)
