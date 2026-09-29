"""Async client for the Companies House public data API and Document API.

Responsibilities, in order, for every call:
  1. validate identifiers (company numbers come from users and the LLM)
  2. serve from the disk cache when fresh
  3. wait for the rate limiter
  4. send, retrying 429 / 5xx / network errors with backoff
  5. parse into typed models
"""

import asyncio
import datetime as dt
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urlparse

import httpx

from ukco.companies_house.cache import DiskCache
from ukco.companies_house.models import (
    CompanyProfile,
    CompanySearchResult,
    DocumentMetadata,
    FilingHistoryItem,
    FilingHistoryList,
    Officer,
    OfficerList,
)
from ukco.companies_house.ratelimit import RateLimiter

API_BASE_URL = "https://api.company-information.service.gov.uk"
DOCUMENT_API_BASE_URL = "https://document-api.company-information.service.gov.uk"

HOUR = 3600.0
DAY = 24 * HOUR

_COMPANY_NUMBER = re.compile(r"^[A-Z0-9]{8}$")
_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class CompaniesHouseError(Exception):
    """Base error for this client."""


class NotFoundError(CompaniesHouseError):
    pass


class AuthError(CompaniesHouseError):
    pass


class RateLimitedError(CompaniesHouseError):
    pass


def normalise_company_number(value: str) -> str:
    """'502851' -> '00502851', 'sc123456' -> 'SC123456'. Rejects anything else.

    Validation matters: this value is interpolated into URL paths and may come
    straight from an LLM tool call.
    """
    number = value.strip().upper().replace(" ", "")
    if number.isdigit() and len(number) < 8:
        number = number.zfill(8)
    if not _COMPANY_NUMBER.fullmatch(number):
        raise ValueError(f"Invalid UK company number: {value!r}")
    return number


def _validate_document_id(value: str) -> str:
    if not _DOCUMENT_ID.fullmatch(value):
        raise ValueError(f"Invalid document id: {value!r}")
    return value


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 30.0

    def backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter; attempt starts at 1."""
        return random.uniform(0, min(self.max_delay, self.base_delay * 2 ** (attempt - 1)))


class CompaniesHouseClient:
    def __init__(
        self,
        api_key: str,
        *,
        cache: DiskCache | None = None,
        limiter: RateLimiter | None = None,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise AuthError("Companies House API key is empty; set COMPANIES_HOUSE_API_KEY")
        self._cache = cache
        self._limiter = limiter or RateLimiter(sleep=sleep)
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        # API key goes in as HTTP Basic username with an empty password.
        self._http = httpx.AsyncClient(
            auth=(api_key, ""),
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "ukco-research-agent/0.1"},
        )
        # Document content redirects to pre-signed S3 URLs. Those are fetched with a
        # separate client that never carries our credentials.
        self._downloads = httpx.AsyncClient(timeout=timeout * 4, transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._downloads.aclose()

    # --- Public API ---------------------------------------------------------

    async def search_companies(
        self, query: str, *, limit: int = 20, start_index: int = 0
    ) -> CompanySearchResult:
        query = query.strip()
        if not query:
            raise ValueError("Search query is empty")
        data = await self._get_json(
            f"{API_BASE_URL}/search/companies",
            {"q": query, "items_per_page": min(limit, 100), "start_index": start_index},
            ttl=HOUR,
        )
        return CompanySearchResult.model_validate(data)

    async def get_company(self, company_number: str) -> CompanyProfile:
        return CompanyProfile.model_validate(await self.get_company_raw(company_number))

    async def get_company_raw(self, company_number: str) -> dict[str, Any]:
        """Unparsed profile JSON, for storing exactly what Companies House returned."""
        number = normalise_company_number(company_number)
        return await self._get_json(f"{API_BASE_URL}/company/{number}", ttl=DAY)

    async def list_officers(
        self, company_number: str, *, start_index: int = 0, items_per_page: int = 100
    ) -> OfficerList:
        number = normalise_company_number(company_number)
        data = await self._get_json(
            f"{API_BASE_URL}/company/{number}/officers",
            {"start_index": start_index, "items_per_page": items_per_page},
            ttl=DAY,
        )
        return OfficerList.model_validate(data)

    async def iter_officers(self, company_number: str) -> AsyncIterator[Officer]:
        start = 0
        while True:
            page = await self.list_officers(company_number, start_index=start)
            for officer in page.items:
                yield officer
            start += len(page.items)
            if not page.items or start >= page.total_results:
                return

    async def list_filing_history(
        self,
        company_number: str,
        *,
        category: str | None = None,
        start_index: int = 0,
        items_per_page: int = 100,
    ) -> FilingHistoryList:
        number = normalise_company_number(company_number)
        params: dict[str, Any] = {"start_index": start_index, "items_per_page": items_per_page}
        if category:
            params["category"] = category
        data = await self._get_json(
            f"{API_BASE_URL}/company/{number}/filing-history", params, ttl=DAY
        )
        return FilingHistoryList.model_validate(data)

    async def iter_filing_history(
        self,
        company_number: str,
        *,
        category: str | None = None,
        since: dt.date | None = None,
    ) -> AsyncIterator[FilingHistoryItem]:
        """Walk filing history newest-first. With `since`, stop paging at older filings:
        big old companies have thousands of filings, and each page costs rate limit."""
        start = 0
        while True:
            page = await self.list_filing_history(
                company_number, category=category, start_index=start
            )
            for item in page.items:
                if since and item.date and item.date < since:
                    return
                yield item
            start += len(page.items)
            if not page.items or start >= page.total_count:
                return

    async def get_document_metadata(self, document_id: str) -> DocumentMetadata:
        doc_id = _validate_document_id(document_id)
        data = await self._get_json(f"{DOCUMENT_API_BASE_URL}/document/{doc_id}", ttl=None)
        return DocumentMetadata.model_validate(data)

    async def get_document_content(
        self, document_id: str, media_type: str = "application/pdf"
    ) -> bytes:
        """Download a filing document. Immutable once filed, so cached forever."""
        doc_id = _validate_document_id(document_id)
        cache_key = DiskCache.key("document", doc_id, media_type)
        if self._cache and (hit := self._cache.get_bytes(cache_key)) is not None:
            return hit

        response = await self._send(
            f"{DOCUMENT_API_BASE_URL}/document/{doc_id}/content",
            headers={"Accept": media_type},
        )
        if response.is_redirect:
            location = response.headers.get("location", "")
            if urlparse(location).scheme != "https":
                raise CompaniesHouseError(f"Unexpected document redirect for {doc_id}")
            download = await self._downloads.get(location)
            download.raise_for_status()
            content = download.content
        else:
            content = response.content

        if self._cache:
            self._cache.set_bytes(cache_key, content)
        return content

    # --- Internals ------------------------------------------------------------

    async def _get_json(
        self, url: str, params: Mapping[str, Any] | None = None, *, ttl: float | None
    ) -> Any:
        cache_key = DiskCache.key(url, dict(params or {}))
        if self._cache and (hit := self._cache.get_json(cache_key, ttl)) is not None:
            return hit
        data = (await self._send(url, params=params)).json()
        if self._cache:
            self._cache.set_json(cache_key, data)
        return data

    async def _send(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            await self._limiter.acquire()
            try:
                response = await self._http.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if attempt >= self._retry.max_attempts:
                    raise CompaniesHouseError(f"Network error calling {_path(url)}: {exc}") from exc
                await self._sleep(self._retry.backoff(attempt))
                continue

            self._limiter.observe(response.headers)

            if response.status_code in _RETRYABLE_STATUS:
                if attempt >= self._retry.max_attempts:
                    if response.status_code == 429:
                        raise RateLimitedError(f"Rate limited on {_path(url)}")
                    raise CompaniesHouseError(f"HTTP {response.status_code} from {_path(url)}")
                if response.status_code == 429 and "x-ratelimit-reset" in response.headers:
                    # Let the limiter hold every caller until the window resets.
                    self._limiter.block_until_epoch(float(response.headers["x-ratelimit-reset"]))
                else:
                    await self._sleep(self._retry.backoff(attempt))
                continue

            if response.status_code == 404:
                raise NotFoundError(f"Not found: {_path(url)}")
            if response.status_code in (400, 401, 403) and _is_auth_error(response):
                raise AuthError(
                    "Companies House rejected the API key. Check COMPANIES_HOUSE_API_KEY "
                    "is a REST API key (not a streaming key) with no quotes or spaces."
                )
            if response.is_error:
                raise CompaniesHouseError(f"HTTP {response.status_code} from {_path(url)}")
            return response


def _path(url: str) -> str:
    """Log-safe form of a URL (no host, no query)."""
    return urlparse(url).path


def _is_auth_error(response: httpx.Response) -> bool:
    if response.status_code in (401, 403):
        return True
    try:
        return "authorization" in str(response.json().get("error", "")).lower()
    except ValueError:
        return False
