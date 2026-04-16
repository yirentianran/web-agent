"""Token cost estimation for Claude models.

Prices are per 1M tokens (USD), as of 2025-04.
"""

from __future__ import annotations

PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-haiku-4-5-20251001": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    # Alibaba Cloud Bailian pricing (approximate, per 1M tokens USD)
    # Actual pricing may vary — verify at https://help.aliyun.com/zh/model-studio/developer-reference/
    "qwen3.6-plus": {"input": 0.4, "output": 1.2, "cache_read": 0.04, "cache_write": 0.5},
}

DEFAULT_MODEL = "claude-sonnet-4-6"


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str = DEFAULT_MODEL,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimate USD cost for a single API call.

    Args:
        input_tokens: Regular (non-cached) input tokens
        output_tokens: Output tokens generated
        model: Model identifier
        cache_read_tokens: Tokens served from prompt cache
        cache_write_tokens: Tokens written to prompt cache

    Returns:
        Estimated cost in USD
    """
    p = PRICES.get(model, PRICES[DEFAULT_MODEL])
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * p["cache_write"]
    ) / 1_000_000
