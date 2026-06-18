"""Per-session sliding-window rate limiter for tool calls."""

import time


class ToolCallRateLimiter:
    """Per-session sliding-window rate limiter for tool calls.

    Default: 30 calls per 60-second window.
    """

    def __init__(self, max_calls: int = 30, window: float = 60.0) -> None:
        self._max_calls = max_calls
        self._window = window
        self._buckets: dict[str, list[float]] = {}

    def allow(self, session_id: str) -> bool:
        now = time.time()
        bucket = self._buckets.setdefault(session_id, [])
        cutoff = now - self._window
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= self._max_calls:
            return False
        bucket.append(now)
        return True

    def clear(self, session_id: str) -> None:
        self._buckets.pop(session_id, None)


# Module-level singleton for use across the app
tool_call_rate_limiter = ToolCallRateLimiter()
