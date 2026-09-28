import pytest

from tests.test_companies_house_client import FakeTime
from ukco.companies_house import RateLimiter

pytestmark = pytest.mark.anyio


async def test_allows_burst_up_to_limit_then_waits_for_window() -> None:
    t = FakeTime()
    limiter = RateLimiter(limit=3, window_seconds=10, clock=t.clock, sleep=t.sleep)

    for _ in range(3):
        await limiter.acquire()
    assert t.sleeps == []

    await limiter.acquire()  # 4th must wait until the first leaves the window
    assert t.sleeps == [10.0]


async def test_low_remaining_header_blocks_until_reset() -> None:
    t = FakeTime()
    limiter = RateLimiter(
        limit=100, reserve=5, clock=t.clock, wall_clock=lambda: 1000.0, sleep=t.sleep
    )
    limiter.observe({"x-ratelimit-remain": "3", "x-ratelimit-reset": "1030"})

    await limiter.acquire()
    assert t.sleeps == [30.0]


async def test_healthy_headers_do_not_block() -> None:
    t = FakeTime()
    limiter = RateLimiter(clock=t.clock, wall_clock=lambda: 1000.0, sleep=t.sleep)
    limiter.observe({"x-ratelimit-remain": "400", "x-ratelimit-reset": "1030"})
    limiter.observe({"unrelated": "header"})

    await limiter.acquire()
    assert t.sleeps == []
