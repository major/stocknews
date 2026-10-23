"""Tests for caching Alpaca asset names."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx2
import pytest

from stocknews.asset_names import AssetNameCache

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

_BASE_URL = "https://assets.test"
_API_KEY = "test-api-key"
_API_SECRET = "test-api-secret"  # noqa: S105
_DAY = 24 * 60 * 60
_HOUR = 60 * 60
_MINUTE = 60
_TWO_REQUESTS = 2
_THREE_REQUESTS = 3
_DEADLINE_SECONDS = 2.0


def _cache(
    client: httpx2.AsyncClient,
    clock: Callable[[], float],
    *,
    capacity: int = 1024,
) -> AssetNameCache:
    """Build an asset-name cache using a controllable clock."""
    return AssetNameCache(
        client,
        _API_KEY,
        _API_SECRET,
        base_url=_BASE_URL,
        capacity=capacity,
        clock=clock,
    )


def test_get_authenticates_encodes_symbol_and_caches_success() -> None:
    """Request one encoded symbol and reuse its successful name for 24 hours."""
    now = [0.0]
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Return a name and record the request for assertions."""
        requests.append(request)
        return httpx2.Response(200, json={"name": "Berkshire Hathaway"})

    async def scenario() -> tuple[str | None, str | None, str | None]:
        """Read the name before and after its successful cache lifetime."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: now[0])
            first = await cache.get("BRK/B")
            now[0] = _DAY - 1
            cached = await cache.get("BRK/B")
            now[0] = _DAY
            refreshed = await cache.get("BRK/B")
            return first, cached, refreshed

    assert asyncio.run(scenario()) == (
        "Berkshire Hathaway",
        "Berkshire Hathaway",
        "Berkshire Hathaway",
    )
    assert len(requests) == _TWO_REQUESTS
    assert str(requests[0].url) == f"{_BASE_URL}/v2/assets/BRK%2FB"
    assert requests[0].headers["APCA-API-KEY-ID"] == _API_KEY
    assert requests[0].headers["APCA-API-SECRET-KEY"] == _API_SECRET


@pytest.mark.parametrize(
    ("response", "expected_ttl"),
    [
        (httpx2.Response(404), _HOUR),
        (httpx2.Response(200, json={}), _HOUR),
        (httpx2.Response(200, json={"name": None}), _HOUR),
        (httpx2.Response(200, json={"name": "  "}), _HOUR),
    ],
)
def test_not_found_and_empty_names_use_one_hour_negative_cache(
    response: httpx2.Response,
    expected_ttl: int,
) -> None:
    """Avoid repeated requests for missing or empty asset names for one hour."""
    now = [0.0]
    requests = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Return the configured missing-name response."""
        nonlocal requests
        requests += 1
        return response

    async def scenario() -> tuple[str | None, str | None, str | None]:
        """Check negative-cache hits and expiration."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: now[0])
            first = await cache.get("MISS")
            now[0] = expected_ttl - 1
            cached = await cache.get("MISS")
            now[0] = expected_ttl
            refreshed = await cache.get("MISS")
            return first, cached, refreshed

    assert asyncio.run(scenario()) == (None, None, None)
    assert requests == _TWO_REQUESTS


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b"[]",
        b'{"name": 12}',
    ],
)
def test_malformed_json_or_name_type_uses_transient_ttl(payload: bytes) -> None:
    """Treat malformed JSON and non-string names as short-lived transient failures."""
    now = [0.0]
    requests = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Return a malformed response body."""
        nonlocal requests
        requests += 1
        return httpx2.Response(200, content=payload)

    async def scenario() -> tuple[str | None, str | None, str | None]:
        """Check retry after the one-minute transient cache lifetime."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: now[0])
            first = await cache.get("BAD")
            now[0] = _MINUTE - 1
            cached = await cache.get("BAD")
            now[0] = _MINUTE
            retried = await cache.get("BAD")
            return first, cached, retried

    assert asyncio.run(scenario()) == (None, None, None)
    assert requests == _TWO_REQUESTS


@pytest.mark.parametrize(
    "failure",
    [httpx2.ConnectError("offline"), httpx2.ReadTimeout("timed out"), httpx2.Response(503)],
)
def test_transient_failure_uses_one_minute_cache_and_retries(failure: Exception | httpx2.Response) -> None:
    """Cache network and service failures briefly before retrying."""
    now = [0.0]
    requests = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Fail once, then return a valid asset name."""
        nonlocal requests
        requests += 1
        if requests == 1:
            if isinstance(failure, httpx2.Response):
                return failure
            raise failure
        return httpx2.Response(200, json={"name": "Acme Corporation"})

    async def scenario() -> tuple[str | None, str | None, str | None]:
        """Check the transient failure result, cache hit, and successful retry."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: now[0])
            first = await cache.get("ACME")
            now[0] = _MINUTE - 1
            cached = await cache.get("ACME")
            now[0] = _MINUTE
            retried = await cache.get("ACME")
            return first, cached, retried

    assert asyncio.run(scenario()) == (None, None, "Acme Corporation")
    assert requests == _TWO_REQUESTS


@pytest.mark.parametrize("response", [httpx2.Response(404), httpx2.Response(200, json={})])
def test_missing_refresh_returns_stale_success(response: httpx2.Response) -> None:
    """Keep a known successful name when a refresh reports the asset missing."""
    now = [0.0]
    requests = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Return one successful name followed by a missing response."""
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx2.Response(200, json={"name": "Acme Corporation"})
        return response

    async def scenario() -> tuple[str | None, str | None]:
        """Expire the successful name and request its refresh."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: now[0])
            first = await cache.get("ACME")
            now[0] = _DAY
            refreshed = await cache.get("ACME")
            return first, refreshed

    assert asyncio.run(scenario()) == ("Acme Corporation", "Acme Corporation")
    assert requests == _TWO_REQUESTS


def test_transient_refresh_failure_returns_stale_success() -> None:
    """Keep a known successful name when its refresh encounters a network error."""
    now = [0.0]
    requests = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Return a name once, then simulate an offline service."""
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx2.Response(200, json={"name": "Acme Corporation"})
        raise httpx2.ConnectError("offline")

    async def scenario() -> tuple[str | None, str | None, str | None]:
        """Check stale-name fallback and its short transient cache lifetime."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: now[0])
            first = await cache.get("ACME")
            now[0] = _DAY
            stale = await cache.get("ACME")
            now[0] = _DAY + _MINUTE
            retried = await cache.get("ACME")
            return first, stale, retried

    assert asyncio.run(scenario()) == (
        "Acme Corporation",
        "Acme Corporation",
        "Acme Corporation",
    )
    assert requests == _THREE_REQUESTS


def test_lru_keeps_entries_bounded_and_refreshes_recent_entry() -> None:
    """Evict the least recently used entry when capacity is reached."""
    requests: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Return the requested symbol as its name."""
        symbol = request.url.path.rsplit("/", 1)[-1]
        requests.append(symbol)
        return httpx2.Response(200, json={"name": symbol})

    async def scenario() -> tuple[str | None, ...]:
        """Populate, access, and overflow a two-entry cache."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: 0.0, capacity=2)
            return (
                await cache.get("A"),
                await cache.get("B"),
                await cache.get("A"),
                await cache.get("C"),
                await cache.get("A"),
                await cache.get("B"),
            )

    assert asyncio.run(scenario()) == ("A", "B", "A", "C", "A", "B")
    assert requests == ["A", "B", "C", "B"]


def test_blank_symbol_returns_none_without_request() -> None:
    """Return no name for a blank symbol without contacting Alpaca."""

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Fail if an empty symbol unexpectedly reaches the transport."""
        pytest.fail("blank symbols must not trigger HTTP requests")

    async def scenario() -> str | None:
        """Query a whitespace-only symbol."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await _cache(client, lambda: 0.0).get(" \t ")

    assert asyncio.run(scenario()) is None


@pytest.mark.parametrize("capacity", [0, -1])
def test_capacity_must_be_positive(capacity: int) -> None:
    """Reject cache capacities that cannot hold entries."""

    async def scenario() -> None:
        """Build a cache to validate the supplied capacity."""
        async with httpx2.AsyncClient() as client:
            _cache(client, lambda: 0.0, capacity=capacity)

    with pytest.raises(ValueError, match="capacity"):
        asyncio.run(scenario())


def test_request_deadline_is_two_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrap each request in a two-second total deadline."""
    deadlines: list[float] = []

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Return a successful asset response."""
        return httpx2.Response(200, json={"name": "Acme"})

    original_timeout = asyncio.timeout

    def record_timeout(seconds: float) -> Any:
        """Record the deadline and use asyncio's normal timeout context."""
        deadlines.append(seconds)
        return original_timeout(seconds)

    monkeypatch.setattr(asyncio, "timeout", record_timeout)

    async def scenario() -> None:
        """Issue one request while recording the configured request deadline."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            assert await _cache(client, lambda: 0.0).get("ACME") == "Acme"

    asyncio.run(scenario())
    assert deadlines == [_DEADLINE_SECONDS]


def test_expired_request_deadline_is_cached_as_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel a blocked request immediately and suppress retry for one minute."""
    release = asyncio.Event()
    requests = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Block the transport until its configured request deadline expires."""
        nonlocal requests
        requests += 1
        await release.wait()
        return httpx2.Response(200, json={"name": "Acme"})

    async def scenario() -> tuple[str | None, str | None]:
        """Retry the timed-out lookup while its transient result remains fresh."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            cache = _cache(client, lambda: 0.0)
            return await cache.get("ACME"), await cache.get("ACME")

    monkeypatch.setattr("stocknews.asset_names._REQUEST_TIMEOUT_SECONDS", 0)
    assert asyncio.run(scenario()) == (None, None)
    assert requests == 1


def test_cancellation_propagates() -> None:
    """Do not convert cancellation during an HTTP request into a cache miss."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Wait until the test cancels the pending request."""
        entered.set()
        await release.wait()
        return httpx2.Response(200, json={"name": "Acme"})

    async def scenario() -> None:
        """Cancel a pending lookup and confirm cancellation reaches its caller."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            lookup = asyncio.create_task(_cache(client, lambda: 0.0).get("ACME"))
            await entered.wait()
            lookup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await lookup

    asyncio.run(scenario())
