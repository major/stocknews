"""Cache asset names from Alpaca's REST API."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx2

if TYPE_CHECKING:
    from collections.abc import Callable

_REQUEST_TIMEOUT_SECONDS = 2.0
_SUCCESS_TTL_SECONDS = 24 * 60 * 60
_MISSING_TTL_SECONDS = 60 * 60
_TRANSIENT_TTL_SECONDS = 60
_NOT_FOUND_STATUS = 404


@dataclass(slots=True)
class _CacheEntry:
    """Store a cached name and the time at which it expires."""

    name: str | None
    expires_at: float


class AssetNameCache:
    """Fetch and cache Alpaca asset names with bounded LRU storage."""

    def __init__(  # noqa: PLR0913
        self,
        client: httpx2.AsyncClient,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = "https://api.alpaca.markets",
        capacity: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the cache using the provided client and clock."""
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            msg = "capacity must be a positive integer"
            raise ValueError(msg)

        self._client = client
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._capacity = capacity
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    async def get(self, symbol: str) -> str | None:
        """Return an asset name, reusing fresh cache entries when possible."""
        if not symbol.strip():
            return None

        now = self._clock()
        previous = self._entries.get(symbol)
        if previous is not None:
            self._entries.move_to_end(symbol)
            if now < previous.expires_at:
                return previous.name
        stale_name = previous.name if previous is not None else None

        try:
            name, ttl_seconds = await self._fetch_name(symbol)
        except httpx2.HTTPError, TimeoutError, ValueError:
            name, ttl_seconds = None, _TRANSIENT_TTL_SECONDS

        result = name if name is not None else stale_name
        self._store(symbol, result, ttl_seconds)
        return result

    async def _fetch_name(self, symbol: str) -> tuple[str | None, int]:
        """Fetch and validate an asset name, returning its cache lifetime."""
        response = await self._request(symbol)
        if response.status_code == _NOT_FOUND_STATUS:
            return None, _MISSING_TTL_SECONDS
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, dict):
            return None, _TRANSIENT_TTL_SECONDS
        name = payload.get("name")
        if name is None:
            return None, _MISSING_TTL_SECONDS
        if not isinstance(name, str):
            return None, _TRANSIENT_TTL_SECONDS
        if not name.strip():
            return None, _MISSING_TTL_SECONDS
        return name, _SUCCESS_TTL_SECONDS

    async def _request(self, symbol: str) -> httpx2.Response:
        """Request one asset record with authentication and a total deadline."""
        encoded_symbol = quote(symbol, safe="")
        url = f"{self._base_url}/v2/assets/{encoded_symbol}"
        async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
            return await self._client.get(
                url,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                },
            )

    def _store(self, symbol: str, name: str | None, ttl_seconds: int) -> None:
        """Insert or refresh an entry and evict least recently used entries."""
        self._entries[symbol] = _CacheEntry(name, self._clock() + ttl_seconds)
        self._entries.move_to_end(symbol)
        if len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
