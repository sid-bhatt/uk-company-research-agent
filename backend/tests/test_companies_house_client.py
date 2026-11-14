"""Companies House client tests. No network: every response comes from a MockTransport."""

import base64
import datetime as dt
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from tests.conftest import load_fixture
from ukco.companies_house import (
    AuthError,
    CompaniesHouseClient,
    CompaniesHouseError,
    DiskCache,
    NotFoundError,
    RateLimiter,
    RetryPolicy,
    normalise_company_number,
)
from ukco.companies_house.models import FilingHistoryItem

pytestmark = pytest.mark.anyio

API_KEY = "test-key-123"
Handler = Callable[[httpx.Request], httpx.Response]


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeTime:
    """A monotonic clock that advances only when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler: Handler, *, cache: DiskCache | None = None, sleep=None, **kw):
    sleep = sleep or FakeSleep()
    return CompaniesHouseClient(
        API_KEY,
        transport=httpx.MockTransport(handler),
        cache=cache,
        sleep=sleep,
        limiter=RateLimiter(sleep=sleep),
        retry=RetryPolicy(max_attempts=3, base_delay=0.01),
        **kw,
    )


# --- Input validation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("00502851", "00502851"), ("502851", "00502851"), (" sc123456 ", "SC123456")],
)
def test_normalise_company_number(raw: str, expected: str) -> None:
    assert normalise_company_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "123456789", "../admin", "0050285/", "AB 12"])
def test_normalise_company_number_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError):
        normalise_company_number(raw)


# --- Parsing -----------------------------------------------------------------------


async def test_search_parses_results_and_sends_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=load_fixture("search_greggs"))

    async with make_client(handler) as client:
        result = await client.search_companies("greggs", limit=5)

    assert result.items[0].company_number == "00502851"
    assert result.items[0].title == "GREGGS PLC"
    request = seen[0]
    assert request.url.path == "/search/companies"
    assert request.url.params["q"] == "greggs"
    expected = "Basic " + base64.b64encode(f"{API_KEY}:".encode()).decode()
    assert request.headers["authorization"] == expected


async def test_company_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/company/00502851"
        return httpx.Response(200, json=load_fixture("company_greggs"))

    async with make_client(handler) as client:
        profile = await client.get_company("502851")

    assert profile.company_name == "GREGGS PLC"
    assert profile.registered_office_address is not None
    assert "NE12 8BU" in profile.registered_office_address.one_line()


async def test_officers_expose_stable_officer_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_fixture("officers_greggs"))

    async with make_client(handler) as client:
        officers = [o async for o in client.iter_officers("00502851")]

    assert officers, "fixture should contain officers"
    for officer in officers:
        assert officer.officer_id
        assert officer.links and officer.links.officer
        assert officer.links.officer.appointments == f"/officers/{officer.officer_id}/appointments"


async def test_filing_history_extracts_document_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["category"] == "accounts"
        return httpx.Response(200, json=load_fixture("filing_history_greggs_accounts"))

    async with make_client(handler) as client:
        page = await client.list_filing_history("00502851", category="accounts")

    first = page.items[0]
    assert first.links and first.links.document_metadata
    assert first.document_id and first.links.document_metadata.endswith(f"/{first.document_id}")


def test_filing_without_document_has_no_document_id() -> None:
    item = FilingHistoryItem.model_validate({"transaction_id": "T1", "links": {"self": "/x"}})
    assert item.document_id is None


async def test_document_metadata_lists_formats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "document-api.company-information.service.gov.uk"
        return httpx.Response(200, json=load_fixture("document_metadata_greggs"))

    async with make_client(handler) as client:
        meta = await client.get_document_metadata("_CoOaKMy2YSbYCuKnKqbwrV3cZVJK5xnwl6fSECy0o0")

    assert "application/pdf" in meta.media_types


# --- Pagination ----------------------------------------------------------------------


async def test_iter_filing_history_walks_all_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start_index"])
        items = [
            {"transaction_id": f"T{i}", "category": "accounts"}
            for i in range(start, min(start + 100, 150))
        ]
        return httpx.Response(200, json={"items": items, "total_count": 150})

    async with make_client(handler) as client:
        items = [i async for i in client.iter_filing_history("00502851")]

    assert len(items) == 150
    assert items[-1].transaction_id == "T149"


async def test_iter_filing_history_stops_paging_at_since() -> None:
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start_index"])
        requested.append(start)
        # 1000 filings, newest first, one per day going back from 2026-09-01
        items = [
            {"transaction_id": f"T{i}", "date": str(dt.date(2026, 9, 1) - dt.timedelta(days=i))}
            for i in range(start, start + 100)
        ]
        return httpx.Response(200, json={"items": items, "total_count": 1000})

    async with make_client(handler) as client:
        items = [i async for i in client.iter_filing_history("00502851", since=dt.date(2026, 5, 1))]

    assert all(i.date and i.date >= dt.date(2026, 5, 1) for i in items)
    assert len(items) == 124  # 2026-05-01 .. 2026-09-01 inclusive
    assert requested == [0, 100]  # stopped without fetching the other 8 pages


# --- Errors and retries ------------------------------------------------------------


async def test_not_found() -> None:
    async with make_client(lambda r: httpx.Response(404, json={"errors": []})) as client:
        with pytest.raises(NotFoundError):
            await client.get_company("00000000")


async def test_invalid_key_raises_auth_error_without_leaking_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Invalid Authorization header"})

    async with make_client(handler) as client:
        with pytest.raises(AuthError) as exc_info:
            await client.get_company("00502851")

    assert API_KEY not in str(exc_info.value)


async def test_retries_server_errors_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(502)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, httpx.Response(200, json=load_fixture("company_greggs")))

    sleep = FakeSleep()
    async with make_client(handler, sleep=sleep) as client:
        profile = await client.get_company("00502851")

    assert profile.company_number == "00502851"
    assert len(sleep.calls) == 2


async def test_gives_up_after_max_attempts() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with make_client(handler) as client:
        with pytest.raises(CompaniesHouseError, match="HTTP 500"):
            await client.get_company("00502851")

    assert calls == 3


async def test_retries_network_errors() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, json=load_fixture("company_greggs"))

    async with make_client(handler) as client:
        await client.get_company("00502851")

    assert attempts == 2


async def test_429_waits_for_rate_limit_reset() -> None:
    wall_now = 1_000_000.0
    responses = iter([httpx.Response(429, headers={"X-Ratelimit-Reset": str(wall_now + 42)})])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, httpx.Response(200, json=load_fixture("company_greggs")))

    t = FakeTime()
    limiter = RateLimiter(clock=t.clock, wall_clock=lambda: wall_now, sleep=t.sleep)
    client = CompaniesHouseClient(
        API_KEY, transport=httpx.MockTransport(handler), sleep=t.sleep, limiter=limiter
    )
    async with client:
        await client.get_company("00502851")

    assert t.sleeps == [42.0]  # waited exactly until the server's reset time


# --- Caching -------------------------------------------------------------------------


async def test_cache_avoids_second_request(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=load_fixture("company_greggs"))

    cache = DiskCache(tmp_path)
    async with make_client(handler, cache=cache) as client:
        await client.get_company("00502851")
        await client.get_company("502851")  # same company, normalised

    assert calls == 1


async def test_cache_expires_after_ttl(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=load_fixture("company_greggs"))

    clock = [0.0]
    cache = DiskCache(tmp_path, clock=lambda: clock[0])
    async with make_client(handler, cache=cache) as client:
        clock[0] = 1e12  # far past any file mtime → every entry stale
        await client.get_company("00502851")
        await client.get_company("00502851")

    assert calls == 2


def test_corrupt_cache_entry_is_a_miss(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    key = cache.key("x")
    cache.set_json(key, {"ok": True})
    next(tmp_path.rglob("*.json")).write_text("{not json")
    assert cache.get_json(key, ttl=None) is None


DOC_ID = "_CoOaKMy2YSbYCuKnKqbwrV3cZVJK5xnwl6fSECy0o0"


# --- Document download -------------------------------------------------------------


async def test_document_download_follows_redirect_without_credentials(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    s3_url = "https://s3.eu-west-2.amazonaws.com/bucket/doc.pdf?X-Amz-Signature=abc"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "document-api.company-information.service.gov.uk":
            assert request.headers["accept"] == "application/pdf"
            return httpx.Response(302, headers={"Location": s3_url})
        return httpx.Response(200, content=b"%PDF-1.7 fake")

    cache = DiskCache(tmp_path)
    async with make_client(handler, cache=cache) as client:
        content = await client.get_document_content("_CoOaKMy2YSbYCuKnKqbwrV3cZVJK5xnwl6fSECy0o0")
        again = await client.get_document_content("_CoOaKMy2YSbYCuKnKqbwrV3cZVJK5xnwl6fSECy0o0")

    assert content == again == b"%PDF-1.7 fake"
    assert len(seen) == 2  # second call served from cache
    assert "authorization" not in seen[1].headers  # API key never sent to S3


@pytest.mark.parametrize(
    ("failures", "outcome"),
    [
        ([httpx.ReadTimeout("slow")], "ok"),  # one timeout, then it works
        ([httpx.Response(503)], "ok"),
        ([httpx.ReadTimeout("slow")] * 3, "Network error downloading"),
        ([httpx.Response(403)], "HTTP 403 downloading"),  # e.g. the signed URL expired
    ],
    ids=["timeout-then-ok", "503-then-ok", "timeouts-exhausted", "forbidden"],
)
async def test_document_download_retries_and_fails_cleanly(failures, outcome: str) -> None:
    """A slow or failing storage download is retried, then reported as a CompaniesHouseError
    (one unreadable document), never as a raw network exception that crashes a report."""
    s3_url = "https://s3.eu-west-2.amazonaws.com/bucket/doc.xhtml?X-Amz-Signature=abc"
    queue = list(failures)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "document-api.company-information.service.gov.uk":
            return httpx.Response(302, headers={"Location": s3_url})
        if queue:
            failure = queue.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return httpx.Response(200, content=b"<html/>")

    async with make_client(handler) as client:
        if outcome == "ok":
            assert await client.get_document_content(DOC_ID) == b"<html/>"
        else:
            with pytest.raises(CompaniesHouseError, match=outcome):
                await client.get_document_content(DOC_ID)


async def test_document_id_is_validated() -> None:
    async with make_client(lambda r: httpx.Response(200)) as client:
        with pytest.raises(ValueError):
            await client.get_document_content("../../etc/passwd")
