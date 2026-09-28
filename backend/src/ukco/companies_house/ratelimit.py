"""Client-side rate limiting for the Companies House API.

Companies House allows 600 requests per 5-minute window per API key and answers
429 once you exceed it. We stay under the limit ourselves (sliding window) and
also obey the server's own view via the X-Ratelimit-* headers, since other
processes (Airflow tasks, the API server) may share the same key.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class RateLimiter:
    def __init__(
        self,
        limit: int = 550,  # headroom under the documented 600
        window_seconds: float = 300.0,
        *,
        reserve: int = 5,
        clock: Clock = time.monotonic,
        wall_clock: Clock = time.time,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._limit = limit
        self._window = window_seconds
        self._reserve = reserve
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until one more request is allowed, then record it."""
        async with self._lock:
            while True:
                now = self._clock()
                while self._sent and self._sent[0] <= now - self._window:
                    self._sent.popleft()

                wait = max(0.0, self._blocked_until - now)
                if len(self._sent) >= self._limit:
                    wait = max(wait, self._sent[0] + self._window - now)
                if wait <= 0:
                    self._sent.append(now)
                    return
                await self._sleep(wait)

    def observe(self, headers: dict[str, str] | object) -> None:
        """Update from X-Ratelimit-Remain / X-Ratelimit-Reset response headers."""
        get = getattr(headers, "get", None)
        if get is None:
            return
        try:
            remaining = int(get("x-ratelimit-remain"))
            reset_epoch = float(get("x-ratelimit-reset"))
        except (TypeError, ValueError):
            return
        if remaining <= self._reserve:
            self.block_until_epoch(reset_epoch)

    def block_until_epoch(self, reset_epoch: float) -> None:
        delay = max(0.0, reset_epoch - self._wall_clock())
        self._blocked_until = max(self._blocked_until, self._clock() + delay)
