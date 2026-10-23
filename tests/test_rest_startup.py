"""Behavioral tests for starting the REST news poller in the application."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from stocknews.__main__ import run_application
from stocknews.models import Config
from stocknews.runtime import DELIVERY_QUEUE_CAPACITY

if TYPE_CHECKING:
    from pathlib import Path

_DUMMY_CREDENTIALS = ("rest-startup-test-key", "rest-startup-test-secret")
_API_KEY, _API_SECRET = _DUMMY_CREDENTIALS
_SECOND_REQUEST = 2
_THIRD_REQUEST = 3
_FOURTH_REQUEST = 4
_BACKPRESSURE_EXTRA_ITEMS = 2


class _LastReceiptObserver(logging.Handler):
    """Signal when the runtime receives the last item in a REST batch."""

    def __init__(self, expected_symbols: tuple[str, ...]) -> None:
        """Store the expected symbols and create a receipt event."""
        super().__init__()
        self._expected_symbols = expected_symbols
        self.observed = asyncio.Event()

    def emit(self, record: logging.LogRecord) -> None:
        """Signal after the runtime logs the receipt for the final batch item."""
        if record.getMessage() == "received news item" and record.symbols == self._expected_symbols:
            self.observed.set()


def _config(state_path: Path) -> Config:
    """Build a default REST configuration using temporary durable state."""
    return Config(
        alpaca_api_key=_API_KEY,
        alpaca_api_secret=_API_SECRET,
        alpaca_news_rest_url="https://alpaca.test/v1beta1/news",
        alpaca_assets_url="https://assets.test",
        news_poll_interval_seconds=1,
        news_poll_overlap_seconds=1,
        news_state_path=str(state_path),
        discord_analyst_webhooks=("https://discord.test/analyst",),
        discord_earnings_webhooks=("https://discord.test/earnings",),
        discord_news_webhooks=("https://discord.test/news",),
        stock_logo="https://logo.test/%s.webp",
        transparent_png="https://image.test/transparent.png",
    )


def _article(
    article_id: int,
    headline: str,
    *,
    symbols: list[str] | None = None,
    author: str = "Benzinga Newsdesk",
) -> dict[str, Any]:
    """Return one valid Alpaca REST article payload."""
    return {
        "id": article_id,
        "symbols": symbols if symbols is not None else ["AAPL"],
        "author": author,
        "headline": headline,
        "summary": "Summary",
        "url": f"https://article.test/{article_id}",
        "source": "benzinga",
        "content": "Full article content",
    }


def _response(news: list[dict[str, Any]], next_page_token: str | None = None) -> httpx2.Response:
    """Build a valid paginated Alpaca REST response."""
    return httpx2.Response(200, json={"news": news, "next_page_token": next_page_token})


async def _poll_immediately(stop_event: asyncio.Event, _interval_seconds: int) -> None:
    """Yield control between polls without waiting for a real interval."""
    await asyncio.sleep(0)


def test_rest_startup_routes_real_polls_and_deduplicates_across_restart(  # noqa: PLR0915
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Route default REST news through runtime and persist deduplication state."""
    monkeypatch.setattr("stocknews.rest_news._wait_for_next_poll", _poll_immediately)
    state_path = tmp_path / "news.sqlite3"
    config = _config(state_path)
    requests: list[httpx2.Request] = []
    asset_requests: list[httpx2.Request] = []
    deliveries: list[tuple[str, str]] = []
    analyst_payloads: list[dict[str, Any]] = []
    active_stop_event = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Serve Alpaca pages and record Discord deliveries."""
        nonlocal active_stop_event
        if request.url.path.startswith("/v2/assets/"):
            asset_requests.append(request)
            return httpx2.Response(200, json={"name": "Apple Inc."})
        if request.url.host == "discord.test":
            payload = json.loads(request.content)
            embed = payload["embeds"][0]
            title = embed.get("title", embed.get("author", {}).get("name"))
            assert isinstance(title, str)
            deliveries.append((request.url.path, title))
            if request.url.path == "/analyst":
                analyst_payloads.append(payload)
            return httpx2.Response(204)

        requests.append(request)
        request_number = len(requests)
        if request_number == 1:
            return _response(
                [
                    _article(1, "Apple Q1 EPS $2.00 beat $1.80 Estimate"),
                    _article(2, "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223"),
                ],
                "page-2",
            )
        if request_number == _SECOND_REQUEST:
            return _response(
                [
                    _article(2, "Duplicate analyst headline"),
                    _article(3, "Apple launches a phone"),
                    _article(4, "Microsoft launches a tablet", author="Other Newsdesk"),
                    _article(
                        5,
                        "Foreign listings should be skipped",
                        symbols=["NASDAQ:NVDA", "NYSE:IBM"],
                    ),
                ]
            )
        if request_number == _THIRD_REQUEST:
            active_stop_event.set()
            return _response(
                [
                    _article(1, "Duplicate earnings headline"),
                    _article(3, "Duplicate general headline"),
                    _article(6, "Microsoft launches a tablet", symbols=["MSFT"]),
                ]
            )

        active_stop_event.set()
        return _response(
            [
                _article(1, "Duplicate after restart"),
                _article(6, "Duplicate after restart", symbols=["MSFT"]),
                _article(7, "Apple says this would be worth reading"),
            ]
        )

    async def scenario() -> None:
        """Run two application lifetimes against the same REST state database."""
        nonlocal active_stop_event
        with caplog.at_level(logging.INFO, logger="stocknews.test.rest_startup"):
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=2) as client:
                for _ in range(2):
                    active_stop_event = asyncio.Event()
                    await run_application(
                        config,
                        client,
                        logging.getLogger("stocknews.test.rest_startup"),
                        active_stop_event,
                    )

    asyncio.run(scenario())

    assert len(requests) == _FOURTH_REQUEST
    assert [request.url.params.get("page_token") for request in requests] == [None, "page-2", None, None]
    assert all(request.headers["APCA-API-KEY-ID"] == _API_KEY for request in requests)
    assert all(request.headers["APCA-API-SECRET-KEY"] == _API_SECRET for request in requests)
    assert len(asset_requests) == 1
    assert str(asset_requests[0].url) == "https://assets.test/v2/assets/AAPL"
    assert asset_requests[0].headers["APCA-API-KEY-ID"] == _API_KEY
    assert asset_requests[0].headers["APCA-API-SECRET-KEY"] == _API_SECRET
    assert [path for path, _title in deliveries] == ["/earnings", "/analyst", "/news", "/news", "/news"]
    assert deliveries[0][1] == "AAPL: Apple Inc."
    assert deliveries[1][1] == "AAPL · Apple Inc."
    assert analyst_payloads[0]["username"] == "Analyst Wire"
    assert analyst_payloads[0]["embeds"][0]["description"] == ("Target raised to $223 · Buy maintained · Goldman Sachs")
    assert deliveries[2][1:] == ("AAPL: Apple launches a phone",)
    assert deliveries[3][1:] == ("MSFT: Microsoft launches a tablet",)
    assert deliveries[4][1:] == ("AAPL: Apple says this would be worth reading",)
    skips = [record.reason for record in caplog.records if record.message == "skipping news item"]
    assert skips == ["unapproved_author", "non_us_exchange"]
    assert state_path.is_file()


def test_rest_startup_authentication_failure_sets_stop_event(tmp_path: Path) -> None:
    """Propagate REST authentication errors and stop the poller during cleanup."""
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Record the authenticated REST request and reject its credentials."""
        requests.append(request)
        return httpx2.Response(401, text="private authentication response")

    async def scenario() -> None:
        """Run the application until Alpaca rejects REST authentication."""
        stop_event = asyncio.Event()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=2) as client:
            with pytest.raises(RuntimeError, match="status 401"):
                await run_application(
                    _config(tmp_path / "news.sqlite3"),
                    client,
                    logging.getLogger("stocknews.test.rest_startup"),
                    stop_event,
                )
        assert stop_event.is_set()

    asyncio.run(scenario())

    assert len(requests) == 1
    assert requests[0].headers["APCA-API-KEY-ID"] == _API_KEY
    assert requests[0].headers["APCA-API-SECRET-KEY"] == _API_SECRET


def test_rest_startup_backpressures_batches_larger_than_delivery_queue(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wait for delivery capacity and send every item from a large REST page."""
    article_count = DELIVERY_QUEUE_CAPACITY + _BACKPRESSURE_EXTRA_ITEMS
    final_symbols = (f"SYM{article_count - 1}",)
    first_delivery_started = asyncio.Event()
    release_delivery = asyncio.Event()
    stop_event = asyncio.Event()
    delivered: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Hold the first Discord delivery while the REST batch fills the queue."""
        if request.url.host == "discord.test":
            delivered.append(request)
            if len(delivered) == 1:
                first_delivery_started.set()
                await release_delivery.wait()
            return httpx2.Response(204)

        stop_event.set()
        return _response(
            [
                _article(
                    article_id,
                    "Apple launches a phone",
                    symbols=[f"SYM{article_id - 1}"],
                )
                for article_id in range(1, article_count + 1)
            ]
        )

    async def scenario() -> None:
        """Release the blocked worker after the runtime reaches the final item."""
        logger = logging.getLogger("stocknews.test.rest_startup.backpressure")
        observer = _LastReceiptObserver(final_symbols)
        logger.addHandler(observer)
        application: asyncio.Task[None] | None = None
        try:
            with caplog.at_level(logging.INFO, logger=logger.name):
                async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=2) as client:
                    application = asyncio.create_task(
                        run_application(
                            _config(tmp_path / "news.sqlite3"),
                            client,
                            logger,
                            stop_event,
                        )
                    )
                    await asyncio.wait_for(first_delivery_started.wait(), timeout=1)
                    await asyncio.wait_for(observer.observed.wait(), timeout=1)

                    assert not application.done()
                    assert len(delivered) == 1

                    release_delivery.set()
                    await asyncio.wait_for(application, timeout=2)
        finally:
            release_delivery.set()
            if application is not None and not application.done():
                application.cancel()
                await asyncio.gather(application, return_exceptions=True)
            logger.removeHandler(observer)
            observer.close()

    asyncio.run(scenario())

    assert len(delivered) == article_count
    assert all(request.url.path == "/news" for request in delivered)
    assert stop_event.is_set()


def test_rest_startup_cancellation_sets_stop_event(tmp_path: Path) -> None:
    """Stop the REST poller when application shutdown cancels a pending request."""
    request_started = asyncio.Event()

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Hold the REST request until the application task is cancelled."""
        request_started.set()
        await asyncio.Event().wait()
        return _response([])

    async def scenario() -> None:
        """Cancel the running application after its first REST request begins."""
        stop_event = asyncio.Event()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=2) as client:
            application = asyncio.create_task(
                run_application(
                    _config(tmp_path / "news.sqlite3"),
                    client,
                    logging.getLogger("stocknews.test.rest_startup"),
                    stop_event,
                )
            )
            await asyncio.wait_for(request_started.wait(), timeout=1)
            application.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(application, timeout=1)
        assert stop_event.is_set()

    asyncio.run(scenario())
