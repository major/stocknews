"""Behavioral tests for stream orchestration and Discord delivery."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Awaitable, Callable, Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import replace
from io import StringIO
from typing import Any

import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport

from stocknews.__main__ import run_application
from stocknews.config import load_config
from stocknews.logging import JSONFormatter, configure_logging
from stocknews.models import Config, NewsItem
from stocknews.runtime import _settle_task, run

_STOCK_LOGO = "https://static.stocktitan.net/company-logo/%s.webp"
_TRANSPARENT_PNG = "https://major.io/transparent.png"
_RECEIPT_CONTENT_LIMIT = 250
_DUMMY_ALPACA_CREDENTIALS = ("key", "secret")
_DUMMY_ALPACA_API_KEY, _DUMMY_ALPACA_API_SECRET = _DUMMY_ALPACA_CREDENTIALS
type ASGIReceive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type ASGISend = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class _ShutdownNewsASGIApp:
    def __init__(self) -> None:
        self.connected = asyncio.Event()
        self.disconnected = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        assert scope.get("path") == "/v1beta1/news"
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            incoming = await receive()
            if incoming.get("type") == "websocket.disconnect":
                self.disconnected.set()
                return
            text = incoming.get("text")
            assert isinstance(text, str)
            message = json.loads(text)
            if message["action"] == "auth":
                await _send_websocket_json(send, {"T": "success", "msg": "authenticated"})
                continue
            await _send_websocket_json(send, {"T": "subscription", "news": ["*"]})
            await _send_websocket_json(
                send,
                {
                    "T": "n",
                    "symbols": [" AAPL "],
                    "author": "Benzinga Newsdesk",
                    "headline": "Apple &amp;amp; launches a phone",
                    "summary": "Company announcement",
                    "url": "https://example.test/news",
                },
            )
            self.connected.set()


class _ShutdownDiscordHandler:
    def __init__(self, shutdown_mode: str) -> None:
        self.shutdown_mode = shutdown_mode
        self.delivered = asyncio.Event()
        self.release_delivery = asyncio.Event()
        self.delivery_cancelled = asyncio.Event()
        self.received_payloads: list[dict[str, object]] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.received_payloads.append(json.loads(request.content))
        self.delivered.set()
        if self.shutdown_mode == "stop-event":
            try:
                await self.release_delivery.wait()
            except asyncio.CancelledError:
                self.delivery_cancelled.set()
                raise
        return httpx2.Response(204)


class _EstablishedNewsFailureASGIApp:
    def __init__(self, terminal_timing: str) -> None:
        self.terminal_timing = terminal_timing
        self.subscribed = asyncio.Event()
        self.report_terminal_error = asyncio.Event()
        self.disconnected = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        assert scope.get("path") == "/v1beta1/news"
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            incoming = await receive()
            if incoming.get("type") == "websocket.disconnect":
                self.disconnected.set()
                return
            text = incoming.get("text")
            assert isinstance(text, str)
            request = json.loads(text)
            if request["action"] == "auth":
                await _send_websocket_json(send, {"T": "success", "msg": "authenticated"})
                continue
            subscription = {"T": "subscription", "news": ["*"]}
            if self.terminal_timing == "before-consumer":
                await _send_websocket_json(
                    send,
                    [subscription, {"T": "error", "code": 403, "msg": "request rejected using private-api-key"}],
                )
            else:
                await _send_websocket_json(send, subscription)
            self.subscribed.set()
            if self.terminal_timing == "late":
                await self.report_terminal_error.wait()
                await _send_websocket_json(
                    send,
                    {"T": "error", "code": 403, "msg": "request rejected using private-api-key"},
                )


class _NewsCancellationASGIApp:
    def __init__(self) -> None:
        self.disconnected = asyncio.Event()
        self.subscribed = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        assert scope.get("path") == "/v1beta1/news"
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            incoming = await receive()
            if incoming.get("type") == "websocket.disconnect":
                self.disconnected.set()
                return
            text = incoming.get("text")
            assert isinstance(text, str)
            request = json.loads(text)
            if request["action"] == "auth":
                await _send_websocket_json(send, {"T": "success", "msg": "authenticated"})
                continue
            await _send_websocket_json(send, {"T": "subscription", "news": ["*"]})
            for headline, author in (
                ("Apple launches a phone", "Benzinga Newsdesk"),
                ("Microsoft launches a tablet", "Benzinga Newsdesk"),
                ("unrouted item", "Other Newsdesk"),
            ):
                await _send_websocket_json(
                    send,
                    {"T": "n", "symbols": ["AAPL"], "author": author, "headline": headline},
                )
            self.subscribed.set()


class _CancellationDiscordHandler:
    def __init__(self) -> None:
        self.first_request_started = asyncio.Event()
        self.release_request = asyncio.Event()
        self.sender_cancelled = asyncio.Event()
        self.requested_titles: list[str] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        title = _payload_title(json.loads(request.content))
        if title == "AAPL: Apple launches a phone":
            self.first_request_started.set()
            try:
                await self.release_request.wait()
            except asyncio.CancelledError:
                self.sender_cancelled.set()
                raise
        self.requested_titles.append(title)
        return httpx2.Response(204)


class _RepeatedCancellationNewsStream:
    def __init__(self, first_request_started: asyncio.Event) -> None:
        self.first_request_started = first_request_started
        self.second_delivery_queued = asyncio.Event()
        self.stream_cancelled = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[NewsItem]:
        try:
            yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")
            await self.first_request_started.wait()
            yield NewsItem(symbols=("MSFT",), author="Benzinga Newsdesk", headline="Microsoft launches a tablet")
            self.second_delivery_queued.set()
            await asyncio.Event().wait()
        finally:
            self.stream_cancelled.set()


class _RepeatedCancellationDiscordHandler:
    def __init__(self) -> None:
        self.first_request_started = asyncio.Event()
        self.release_request = asyncio.Event()
        self.sender_cancelled = asyncio.Event()
        self.delivered: list[str] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        title = _payload_title(json.loads(request.content))
        self.first_request_started.set()
        try:
            await self.release_request.wait()
        except asyncio.CancelledError:
            self.sender_cancelled.set()
            raise
        self.delivered.append(title)
        return httpx2.Response(204)


class _StringableWebhook:
    def __str__(self) -> str:
        return "https://discord.test/api/webhooks/nested/secret-nested"


def _tasks_started_after(existing_tasks: set[asyncio.Task[Any]]) -> set[asyncio.Task[Any]]:
    return {task for task in asyncio.all_tasks() if task not in existing_tasks and task is not asyncio.current_task()}


async def _cancel_tasks_started_after(existing_tasks: set[asyncio.Task[Any]]) -> None:
    tasks = _tasks_started_after(existing_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class _LogMessageEvent(logging.Handler):
    def __init__(self, message: str, observed: asyncio.Event) -> None:
        super().__init__()
        self._message = message
        self._observed = observed

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == self._message:
            self._observed.set()


def _config() -> Config:
    return Config(
        alpaca_api_key=_DUMMY_ALPACA_API_KEY,
        alpaca_api_secret=_DUMMY_ALPACA_API_SECRET,
        alpaca_news_stream_url="wss://news.test",
        discord_analyst_webhooks=("https://discord.test/analyst",),
        discord_earnings_webhooks=("https://discord.test/earnings",),
        discord_news_webhooks=("https://discord.test/news",),
        stock_logo=_STOCK_LOGO,
        transparent_png=_TRANSPARENT_PNG,
    )


@pytest.fixture(autouse=True)
def _restore_runtime_test_logger() -> Iterator[None]:
    logger = logging.getLogger("stocknews.test.runtime")
    previous_handlers = logger.handlers[:]
    previous_level = logger.level
    previous_filters = logger.filters[:]
    previous_propagate = logger.propagate
    previous_disabled = logger.disabled
    yield
    for handler in logger.handlers[:]:
        if handler not in previous_handlers:
            logger.removeHandler(handler)
            if type(handler) is logging.StreamHandler:
                handler.close()
    logger.handlers[:] = previous_handlers
    logger.setLevel(previous_level)
    logger.filters[:] = previous_filters
    logger.propagate = previous_propagate
    logger.disabled = previous_disabled


def _logger(output: StringIO) -> logging.Logger:
    logger = logging.getLogger("stocknews.test.runtime")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    logger.setLevel(logging.INFO)
    logger.filters.clear()
    logger.propagate = False
    logger.disabled = False
    handler = logging.StreamHandler(output)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    return logger


@contextmanager
def _captured_app_logs(output: StringIO) -> Iterator[logging.Logger]:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    httpx_logger = logging.getLogger("httpx2")
    previous_httpx_level = httpx_logger.level
    root.handlers.clear()
    try:
        yield configure_logging(output)
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers.clear()
        root.handlers.extend(previous_handlers)
        root.setLevel(previous_level)
        httpx_logger.setLevel(previous_httpx_level)


def _payload_title(payload: object) -> str:
    assert isinstance(payload, dict)
    embeds = payload["embeds"]
    assert isinstance(embeds, list)
    assert embeds
    embed = embeds[0]
    assert isinstance(embed, dict)
    title = embed.get("title", embed.get("author", {}).get("name"))
    assert isinstance(title, str)
    return title


class _IteratorCloseCancellationScenario:
    """Coordinate stream cleanup with cancellation of a pending webhook."""

    def __init__(self) -> None:
        """Initialize synchronization events and observed webhook titles."""
        self.request_started = asyncio.Event()
        self.iterator_close_started = asyncio.Event()
        self.release_iterator_close = asyncio.Event()
        self.sender_cancelled = asyncio.Event()
        self.second_request_started = asyncio.Event()
        self.requested_titles: list[str] = []

    async def handle_webhook(self, request: httpx2.Request) -> httpx2.Response:
        """Hold the first delivery and record whether it is cancelled.

        Args:
            request: The webhook request made by the runtime.

        Returns:
            A successful response for the second news item.
        """
        title = _payload_title(json.loads(request.content))
        self.requested_titles.append(title)
        if title == "MSFT: Microsoft launches a tablet":
            self.second_request_started.set()
            return httpx2.Response(204)
        self.request_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.sender_cancelled.set()
            raise

    async def connect_news(self) -> AsyncIterable[NewsItem]:
        """Return two news items with a held-open iterator close.

        Returns:
            A stream that exposes iterator cleanup timing.
        """
        scenario = self

        class NewsStream:
            def __init__(self) -> None:
                self._yielded = False
                self._second_yielded = False

            def __aiter__(self) -> AsyncIterator[NewsItem]:
                return self

            async def __anext__(self) -> NewsItem:
                if self._yielded and self._second_yielded:
                    raise StopAsyncIteration
                if self._yielded:
                    self._second_yielded = True
                    return NewsItem(
                        symbols=("MSFT",),
                        author="Benzinga Newsdesk",
                        headline="Microsoft launches a tablet",
                    )
                self._yielded = True
                return NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")

            async def aclose(self) -> None:
                scenario.iterator_close_started.set()
                await scenario.release_iterator_close.wait()

        return NewsStream()


def test_runtime_classifies_formats_and_sends_real_discord_payloads() -> None:
    """Verify news classification produces the expected Discord payloads."""

    async def scenario() -> None:
        sent: dict[str, dict[str, object]] = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            sent[request.url.path] = json.loads(request.content)
            return httpx2.Response(204)

        items = (
            NewsItem(
                symbols=("AAPL",),
                author="",
                headline="Fiscal.ai reports Apple product news",
            ),
            NewsItem(
                symbols=("AAPL",),
                author="Benzinga Newsdesk",
                headline="Apple Q1 EPS $2.00 beat $1.80 Estimate",
            ),
            NewsItem(
                symbols=("AAPL",),
                author="Benzinga Newsdesk",
                headline="Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
            ),
            NewsItem(
                symbols=("AAPL",),
                author="Benzinga Newsdesk",
                headline="Goldman Sachs Maintains Buy on Apple, Announces Price Target of $223",
            ),
            NewsItem(
                symbols=(" AAPL ",),
                author="Benzinga Newsdesk",
                headline="Apple &amp;amp; launches a new iPhone",
                summary="Company announcement",
                url="https://example.test/news",
            ),
        )

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                for item in items:
                    yield item

            return events()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        sent_logs = [
            (record["kind"], record["symbol"], record["webhooks"])
            for record in records
            if record["msg"] == "sent Discord webhook"
        ]
        assert sent_logs == [("earnings", "AAPL", 1), ("analyst", "AAPL", 1), ("news", "AAPL", 1)]
        skipped_logs = [record for record in records if record["msg"] == "skipping news item"]
        assert [record["reason"] for record in skipped_logs] == ["unapproved_author", "no_payload"]
        assert set(sent) == {"/earnings", "/analyst", "/news"}
        assert sent == {
            "/earnings": {
                "embeds": [
                    {
                        "title": "AAPL: Apple",
                        "description": "💚 EPS: $2.00 vs. $1.80 est.",
                        "image": {"url": _TRANSPARENT_PNG},
                        "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
                    }
                ]
            },
            "/analyst": {
                "embeds": [
                    {
                        "author": {
                            "name": "💚 AAPL: Apple $223.00",
                            "icon_url": "https://static.stocktitan.net/company-logo/aapl.webp",
                        },
                        "description": "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
                        "color": 0x4CAF50,
                    }
                ]
            },
            "/news": {
                "embeds": [
                    {
                        "title": "AAPL: Apple &amp; launches a new iPhone",
                        "description": "Company announcement",
                        "url": "https://example.test/news",
                        "image": {"url": _TRANSPARENT_PNG},
                        "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
                    }
                ]
            },
        }

    asyncio.run(scenario())


def test_every_received_news_item_is_logged_once_before_filtering() -> None:
    """Verify each received item gets one exact receipt log before any skip log."""

    async def scenario() -> None:
        """Run an accepted item and a filtered item through the runtime."""
        accepted = NewsItem(
            symbols=(" AAPL ", "NASDAQ:BAD"),
            author="Benzinga Newsdesk",
            headline="Apple launches a new iPhone &amp; more",
            summary="Line one\n<b>Line two</b>",
            source="benzinga",
            content="<p>Body &amp; text</p>\n\tIndented line",
            url="https://example.test/news?id=1&amp;x=2",
        )
        filtered = NewsItem(
            symbols=("NYSE:IBM", "MSFT"),
            author="Someone Else",
            headline="Microsoft launches a tablet",
            summary="Filtered summary",
            source="other-feed",
            content="Filtered content",
            url="https://example.test/filtered",
        )

        async def handler(_request: httpx2.Request) -> httpx2.Response:
            """Accept every webhook request."""
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return the accepted item followed by the filtered item."""

            async def events() -> AsyncIterable[NewsItem]:
                """Yield both items in order."""
                yield accepted
                yield filtered

            return events()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        receipt_and_skip_messages = [
            record["msg"] for record in records if record["msg"] in {"received news item", "skipping news item"}
        ]
        receipts = [record for record in records if record["msg"] == "received news item"]
        accepted_receipt = {
            "level": "INFO",
            "headline": "Apple launches a new iPhone &amp; more",
            "summary": "Line one\n<b>Line two</b>",
            "source": "benzinga",
            "content": "<p>Body &amp; text</p>\n\tIndented line",
            "url": "https://example.test/news?id=1&amp;x=2",
            "symbols": [" AAPL ", "NASDAQ:BAD"],
        }
        filtered_receipt = {
            "level": "INFO",
            "headline": "Microsoft launches a tablet",
            "summary": "Filtered summary",
            "source": "other-feed",
            "content": "Filtered content",
            "url": "https://example.test/filtered",
            "symbols": ["NYSE:IBM", "MSFT"],
        }
        assert receipt_and_skip_messages == ["received news item", "received news item", "skipping news item"]
        assert {key: receipts[0][key] for key in accepted_receipt} == accepted_receipt
        assert {key: receipts[1][key] for key in filtered_receipt} == filtered_receipt

    asyncio.run(scenario())


def test_received_news_item_logs_empty_defaults_for_absent_optional_fields() -> None:
    """Verify absent optional item fields are logged as empty values."""

    async def scenario() -> None:
        """Run a minimal item that omits summary, source, content, and url."""
        item = NewsItem(symbols=(), author="Benzinga Newsdesk", headline="Apple announces a phone")

        async def handler(_request: httpx2.Request) -> httpx2.Response:
            """Accept the webhook request for the minimal item."""
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return a stream containing only the minimal item."""

            async def events() -> AsyncIterable[NewsItem]:
                """Yield the minimal item once."""
                yield item

            return events()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        receipts = [record for record in records if record["msg"] == "received news item"]
        expected_receipt = {
            "headline": "Apple announces a phone",
            "summary": "",
            "source": "",
            "content": "",
            "url": "",
            "symbols": [],
        }
        assert len(receipts) == 1
        assert {key: receipts[0][key] for key in expected_receipt} == expected_receipt

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content",
    [
        "",
        "Short 🙂 café",
        "é" * 250,
        "é" * 251,
        "🙂" * 300,
    ],
    ids=["empty", "short-unicode", "exactly-250", "251-characters", "long-emoji"],
)
def test_received_news_item_logs_content_limited_to_250_characters(content: str) -> None:
    """Verify receipt logs truncate content by characters while the model keeps the full text."""

    async def scenario() -> None:
        """Run one item with the parameterized content through the runtime."""
        item = NewsItem(
            symbols=("AAPL",),
            author="Benzinga Newsdesk",
            headline="Apple announces a phone",
            content=content,
        )

        async def handler(_request: httpx2.Request) -> httpx2.Response:
            """Accept the webhook request for the test item."""
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return a stream containing only the parameterized item."""

            async def events() -> AsyncIterable[NewsItem]:
                """Yield the parameterized item once."""
                yield item

            return events()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        receipts = [record for record in records if record["msg"] == "received news item"]
        logged_content = receipts[0]["content"]
        assert logged_content == content[:_RECEIPT_CONTENT_LIMIT]
        assert len(logged_content) <= _RECEIPT_CONTENT_LIMIT
        assert item.content == content

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("symbols", "headline", "route", "title", "ticker"),
    [
        ((), "Apple Q1 EPS $2.00 beat $1.80 Estimate", "/earnings", "Apple", ""),
        (
            ("NASDAQ:BAD", " AAPL ", "NYSE:IBM"),
            "Apple Q1 EPS $2.00 beat $1.80 Estimate",
            "/earnings",
            "AAPL: Apple",
            "AAPL",
        ),
        ((), "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223", "/analyst", "💚 Apple $223.00", ""),
        (
            ("NASDAQ:BAD", " AAPL ", "NYSE:IBM"),
            "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
            "/analyst",
            "💚 AAPL: Apple $223.00",
            "AAPL",
        ),
        ((), "Apple announces a phone", "/news", "Apple announces a phone", ""),
        ((" ", "\t"), "Apple shares a blank ticker update", "/news", "Apple shares a blank ticker update", ""),
        ((" AAPL ", "MSFT"), "Apple launches a phone", "/news", "AAPL: Apple launches a phone", "AAPL"),
        (
            ("NASDAQ:NVDA", " MSFT "),
            "Microsoft launches a tablet",
            "/news",
            "MSFT: Microsoft launches a tablet",
            "MSFT",
        ),
        (
            (" MSFT ", "NASDAQ:NVDA"),
            "Microsoft reports another update",
            "/news",
            "MSFT: Microsoft reports another update",
            "MSFT",
        ),
        (
            (" AAPL ",),
            "Apple says this would be worth reading",
            "/news",
            "AAPL: Apple says this would be worth reading",
            "AAPL",
        ),
        (("NASDAQ:NVDA", "NYSE:IBM"), "Foreign listings should be skipped", None, None, None),
    ],
    ids=[
        "earnings-no-symbols",
        "earnings-mixed-symbols",
        "analyst-no-symbols",
        "analyst-mixed-symbols",
        "news-no-symbols",
        "news-blank-symbols",
        "news-us-multiple",
        "news-foreign-first",
        "news-us-first",
        "former-blocked-phrase",
        "foreign-only",
    ],
)
def test_runtime_normalizes_symbols_before_building_category_payloads(
    symbols: tuple[str, ...],
    headline: str,
    route: str | None,
    title: str | None,
    ticker: str | None,
) -> None:
    """Route each item through run and verify normalized ticker output."""

    async def scenario() -> None:
        """Send one parameterized news item through the real runtime pipeline."""
        requested: list[tuple[str, dict[str, object]]] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            """Record a webhook request and return success."""
            requested.append((request.url.path, json.loads(request.content)))
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return a stream containing the parameterized news item."""

            async def events() -> AsyncIterable[NewsItem]:
                """Yield the single test item."""
                yield NewsItem(symbols=symbols, author="Benzinga Newsdesk", headline=headline)

            return events()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news)

        if route is None:
            assert requested == []
            skips = [json.loads(line) for line in output.getvalue().splitlines()]
            assert [record["reason"] for record in skips if record["msg"] == "skipping news item"] == [
                "non_us_exchange"
            ]
            return

        assert len(requested) == 1
        requested_route, payload = requested[0]
        assert requested_route == route
        embed = payload["embeds"][0]
        if route == "/analyst":
            assert embed["author"]["name"] == title
            if ticker:
                assert embed["author"]["icon_url"] == _STOCK_LOGO.replace("%s", ticker.lower())
            else:
                assert "icon_url" not in embed["author"]
            assert "image" not in embed
            assert "thumbnail" not in embed
            visible_title = embed["author"]["name"]
        else:
            assert embed["title"] == title
            thumbnail = embed["thumbnail"]["url"]
            expected_thumbnail = _STOCK_LOGO.replace("%s", ticker.lower()) if ticker else _TRANSPARENT_PNG
            assert thumbnail == expected_thumbnail
            visible_title = title
        assert all(symbol not in visible_title for symbol in symbols if ":" in symbol)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("symbol", "company", "headline", "color"),
    [
        ("AA", "Alcoa", "Argus Research Downgrades Alcoa to Hold", 0xD42020),
        ("EFX", "Equifax", "Argus Research Upgrades Equifax to Buy", 0x4CAF50),
        ("OHI", "Omega Healthcare", "Citizens Upgrades Omega Healthcare Invts to Market Outperform", 0x4CAF50),
        ("CME", "CME Group", "JP Morgan Upgrades CME Group to Overweight, Announces $367 Price Target", 0x4CAF50),
        (
            "AA",
            "Alcoa",
            "Argus Research Downgrades Alcoa to Hold, Maintains Price Target of $32",
            0xD42020,
        ),
    ],
    ids=["argus-downgrade", "argus-upgrade", "citizens-upgrade", "jp-morgan-upgrade", "maintains-target-downgrade"],
)
def test_runtime_routes_rating_headlines_to_analyst_payloads(
    symbol: str,
    company: str,
    headline: str,
    color: int,
) -> None:
    """Verify rating headlines produce one correctly routed analyst delivery."""

    async def scenario() -> None:
        """Send one parameterized rating headline through the runtime."""
        requested: list[tuple[str, dict[str, object]]] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            """Record the webhook path and payload, then accept the delivery."""
            requested.append((request.url.path, json.loads(request.content)))
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return a stream with the parameterized rating headline."""

            async def events() -> AsyncIterable[NewsItem]:
                """Yield the rating headline once."""
                yield NewsItem(symbols=(symbol,), author="Benzinga Newsdesk", headline=headline)

            return events()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news)

        assert len(requested) == 1
        route, payload = requested[0]
        assert route == "/analyst"
        embed = payload["embeds"][0]
        title = embed["author"]["name"]
        assert symbol in title
        assert company in title
        assert "$0.00" not in title
        assert embed["description"] == headline
        assert embed["color"] == color
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        sent = [record for record in records if record["msg"] == "sent Discord webhook"]
        assert [(record["kind"], record["symbol"], record["webhooks"]) for record in sent] == [("analyst", symbol, 1)]

    asyncio.run(scenario())


@pytest.mark.parametrize("shutdown_mode", ["cancel", "stop-event"], ids=["task-cancellation", "external-stop"])
def test_application_stops_real_news_adapter_and_delivers_payload_on_shutdown(shutdown_mode: str) -> None:
    """Verify shutdown closes the news adapter and handles queued delivery."""

    async def scenario() -> None:
        alpaca_app = _ShutdownNewsASGIApp()
        discord_handler = _ShutdownDiscordHandler(shutdown_mode)

        config = replace(
            _config(),
            alpaca_news_stream_url="ws://news.alpaca.test/v1beta1/news",
            discord_news_webhooks=("https://discord.test/api/webhooks/123/private-token",),
        )
        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(alpaca_app) as news_transport:
            mounts = {
                "ws://news.alpaca.test": news_transport,
                "https://discord.test": httpx2.MockTransport(discord_handler),
            }
            async with httpx2.AsyncClient(mounts=mounts, timeout=10) as client:
                existing_tasks = asyncio.all_tasks()
                application = asyncio.create_task(run_application(config, client, _logger(StringIO()), stop_event))
                await asyncio.wait_for(discord_handler.delivered.wait(), timeout=2)
                await asyncio.wait_for(alpaca_app.connected.wait(), timeout=2)
                if shutdown_mode == "cancel":
                    application.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(application, timeout=2)
                else:
                    stop_event.set()
                    await asyncio.wait_for(alpaca_app.disconnected.wait(), timeout=2)
                    assert not application.done()
                    assert not discord_handler.delivery_cancelled.is_set()
                    discord_handler.release_delivery.set()
                    await asyncio.wait_for(application, timeout=2)
                await asyncio.wait_for(alpaca_app.disconnected.wait(), timeout=2)
                orphaned_tasks = _tasks_started_after(existing_tasks)
                try:
                    assert not orphaned_tasks
                finally:
                    await _cancel_tasks_started_after(existing_tasks)

        assert alpaca_app.connected.is_set()
        assert alpaca_app.disconnected.is_set()
        assert _payload_title(discord_handler.received_payloads[0]) == "AAPL: Apple &amp; launches a phone"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "terminal_timing",
    ["late", "before-consumer"],
    ids=["late", "before-consumer"],
)
def test_established_real_news_stream_errors_fail_the_application(terminal_timing: str) -> None:
    """Verify terminal errors from the established news stream fail the application."""

    async def scenario() -> None:
        alpaca_app = _EstablishedNewsFailureASGIApp(terminal_timing)
        logs = StringIO()

        async def unused_discord_handler(_request: httpx2.Request) -> httpx2.Response:
            unused_discord_message = "terminal stream errors must not send Discord messages"
            raise AssertionError(unused_discord_message)

        config = replace(
            _config(),
            alpaca_news_stream_url="ws://news.alpaca.test/v1beta1/news",
        )
        async with ASGIWebSocketTransport(alpaca_app) as news_transport:
            mounts = {
                "ws://news.alpaca.test": news_transport,
                "https://discord.test": httpx2.MockTransport(unused_discord_handler),
            }
            async with httpx2.AsyncClient(mounts=mounts, timeout=10) as client:
                application = asyncio.create_task(run_application(config, client, _logger(logs), asyncio.Event()))
                try:
                    await asyncio.wait_for(alpaca_app.subscribed.wait(), timeout=2)
                    if terminal_timing == "late":
                        alpaca_app.report_terminal_error.set()
                    with pytest.raises(RuntimeError, match="alpaca news stream terminated") as terminal:
                        await asyncio.wait_for(application, timeout=2)
                    assert "private-api-key" not in str(terminal.value)
                    await asyncio.wait_for(alpaca_app.disconnected.wait(), timeout=2)
                finally:
                    if not application.done():
                        application.cancel()
                    await asyncio.gather(application, return_exceptions=True)

        assert alpaca_app.disconnected.is_set()

    asyncio.run(scenario())


def test_real_news_cancellation_stops_adapter_and_cancels_delivery() -> None:
    """Verify cancellation closes the news adapter and cancels delivery."""

    async def scenario() -> None:
        alpaca_app = _NewsCancellationASGIApp()
        discord_handler = _CancellationDiscordHandler()
        skipped_later_item = asyncio.Event()
        logs = StringIO()
        logger = _logger(logs)
        logger.addHandler(_LogMessageEvent("skipping news item", skipped_later_item))

        config = replace(
            _config(),
            alpaca_news_stream_url="ws://news.alpaca.test/v1beta1/news",
            discord_news_webhooks=("https://discord.test/api/webhooks/news/private-token",),
        )
        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(alpaca_app) as news_transport:
            mounts = {
                "ws://news.alpaca.test": news_transport,
                "https://discord.test": httpx2.MockTransport(discord_handler),
            }
            async with httpx2.AsyncClient(mounts=mounts, timeout=10) as client:
                existing_tasks = asyncio.all_tasks()
                application = asyncio.create_task(run_application(config, client, logger, stop_event))
                try:
                    await asyncio.wait_for(discord_handler.first_request_started.wait(), timeout=2)
                    await asyncio.wait_for(skipped_later_item.wait(), timeout=2)
                    await asyncio.wait_for(alpaca_app.subscribed.wait(), timeout=2)
                    stop_event.set()
                    application.cancel()
                    await asyncio.wait_for(alpaca_app.disconnected.wait(), timeout=2)
                    discord_handler.release_request.set()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(application, timeout=2)
                    leaked_tasks = _tasks_started_after(existing_tasks)
                    assert not leaked_tasks, f"unexpected tasks after cancellation: {leaked_tasks!r}"
                finally:
                    discord_handler.release_request.set()
                    if not application.done():
                        application.cancel()
                    await asyncio.gather(application, return_exceptions=True)
                    await _cancel_tasks_started_after(existing_tasks)

        assert alpaca_app.disconnected.is_set()
        assert discord_handler.requested_titles == []
        assert discord_handler.sender_cancelled.is_set()

    asyncio.run(scenario())


def test_settle_task_preserves_mixed_base_exception_group() -> None:
    """Verify waiting for a stream task preserves mixed exception groups."""

    async def scenario() -> None:
        ordinary_error = ValueError("stream failed")
        cancellation_error = asyncio.CancelledError("stream cancelled")
        mixed_error = BaseExceptionGroup("mixed stream failure", [ordinary_error, cancellation_error])
        release_failure = asyncio.Event()
        task_started = asyncio.Event()

        async def fail_after_release() -> None:
            task_started.set()
            await release_failure.wait()
            raise mixed_error

        task = asyncio.create_task(fail_after_release())
        await task_started.wait()
        settling_started = asyncio.Event()

        async def settle_started_task() -> tuple[bool, Exception | None]:
            settling_started.set()
            return await _settle_task(task)

        settling = asyncio.create_task(settle_started_task())
        await settling_started.wait()
        release_failure.set()

        with pytest.raises(BaseExceptionGroup) as error:
            await settling

        assert error.value is mixed_error
        assert error.value.exceptions == (ordinary_error, cancellation_error)
        assert task.exception() is mixed_error

    asyncio.run(scenario())


def test_webhook_fanout_continues_after_failure() -> None:
    """Verify a webhook failure does not prevent other queued deliveries."""

    async def scenario() -> None:
        output = StringIO()
        expected_news_titles = {
            "AAPL: Apple launches a phone",
            "MSFT: Microsoft launches a tablet",
        }
        expected_news_item_count = len(expected_news_titles)
        failed_webhook = "https://discord.test/api/webhooks/fail/secret-fail"
        successful_webhook = "https://discord.test/api/webhooks/success/secret-success"
        config = replace(
            _config(),
            discord_news_webhooks=(failed_webhook, successful_webhook),
        )
        requested: list[str] = []
        delivered: list[dict[str, object]] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            requested.append(str(request.url))
            delivered.append(json.loads(request.content))
            status = 503 if request.url.path.endswith("/fail/secret-fail") else 204
            return httpx2.Response(status)

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")
                yield NewsItem(symbols=("MSFT",), author="Benzinga Newsdesk", headline="Microsoft launches a tablet")

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(config, client, _logger(output), connect_news)

        assert len(delivered) == expected_news_item_count * len(config.discord_news_webhooks)
        assert requested.count(failed_webhook) == expected_news_item_count
        assert requested.count(successful_webhook) == expected_news_item_count
        assert {_payload_title(payload) for payload in delivered} == expected_news_titles
        log_output = output.getvalue()
        records = [json.loads(line) for line in log_output.splitlines()]
        failure_logs = [record for record in records if record["msg"] == "failed to send Discord webhook"]
        assert len(failure_logs) == expected_news_item_count
        assert all(record["error"] == "post webhook: unexpected status 503" for record in failure_logs)
        assert not [record for record in records if record["msg"] == "sent Discord webhook"]
        assert failed_webhook not in log_output
        assert "secret-fail" not in log_output

    asyncio.run(scenario())


def _raise_webhook_request_error_for_redaction(webhook_url: str) -> None:
    """Raise a request error containing a fake webhook URL for traceback redaction.

    Args:
        webhook_url: Fake URL intentionally included in the exception message.
    """
    error_message = f"HTTP request failed for {webhook_url}"
    raise RuntimeError(error_message)


def test_webhook_logs_redact_urls_from_library_and_structured_records() -> None:
    """Verify webhook URLs are redacted in library and structured logs."""
    output = StringIO()
    failed_webhook = "https://discord.test/api/webhooks/fail/secret-fail"
    successful_webhook = "https://discord.test/api/webhooks/success/secret-success"
    quoted_webhook = 'https://discord.test/api/webhooks/fail/secret"quoted'
    nested_webhook = "https://discord.test/api/webhooks/nested/secret-nested"
    tuple_webhook = "https://discord.test/api/webhooks/tuple/secret-tuple"

    with _captured_app_logs(output) as logger:
        library_logger = logging.getLogger("httpx2")
        assert library_logger.getEffectiveLevel() >= logging.WARNING
        library_logger.info("HTTP Request: POST %s", failed_webhook)
        library_logger.warning("HTTP transport warning for %s", failed_webhook)
        library_logger.warning("HTTP transport warning for %s", quoted_webhook)
        try:
            _raise_webhook_request_error_for_redaction(quoted_webhook)
        except RuntimeError:
            library_logger.exception("HTTP request exception")
        logger.warning(
            "structured webhook metadata",
            extra={
                "metadata": {
                    "webhook": failed_webhook,
                    "nested": [successful_webhook, (tuple_webhook,)],
                    "stringable": _StringableWebhook(),
                }
            },
        )

    log_output = output.getvalue()
    records = [json.loads(line) for line in log_output.splitlines()]
    exception_log = next(record for record in records if record["msg"] == "HTTP request exception")
    assert "[redacted webhook URL]" in exception_log["exception"]
    structured_log = next(record for record in records if record["msg"] == "structured webhook metadata")
    assert structured_log["metadata"] == {
        "webhook": "[redacted webhook URL]",
        "nested": ["[redacted webhook URL]", ["[redacted webhook URL]"]],
        "stringable": "[redacted webhook URL]",
    }
    assert "[redacted webhook URL]" in log_output
    assert "HTTP Request" not in log_output
    assert failed_webhook not in log_output
    assert successful_webhook not in log_output
    assert quoted_webhook not in log_output
    assert nested_webhook not in log_output
    assert tuple_webhook not in log_output
    assert "secret-fail" not in log_output
    assert "secret-success" not in log_output
    assert "secret-nested" not in log_output
    assert "secret-tuple" not in log_output


@pytest.mark.parametrize("without_aclose", [False, True], ids=["async-generator", "iterator-without-aclose"])
def test_normal_stream_completion_drains_queued_discord_deliveries(without_aclose: bool) -> None:
    """Verify normal completion drains deliveries for both iterator protocols."""

    async def scenario() -> None:
        request_started = asyncio.Event()
        stream_exhausted = asyncio.Event()
        release_request = asyncio.Event()
        delivered: list[dict[str, object]] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            request_started.set()
            await release_request.wait()
            delivered.append(json.loads(request.content))
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return a normal async generator or an iterator wrapper without aclose."""

            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")
                yield NewsItem(symbols=("MSFT",), author="Benzinga Newsdesk", headline="Microsoft launches a tablet")
                stream_exhausted.set()

            stream = events()
            if without_aclose:

                class NewsIteratorWithoutClose:
                    """Expose only the asynchronous iterator protocol."""

                    def __init__(self, iterator: AsyncIterator[NewsItem]) -> None:
                        """Store the underlying exhausted async generator."""
                        self._iterator = iterator

                    def __aiter__(self) -> AsyncIterator[NewsItem]:
                        """Return this wrapper as the news iterator."""
                        return self

                    async def __anext__(self) -> NewsItem:
                        """Delegate the next news read to the underlying generator."""
                        return await anext(self._iterator)

                return NewsIteratorWithoutClose(stream)
            return stream

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            existing_tasks = asyncio.all_tasks()
            runtime = asyncio.create_task(run(_config(), client, _logger(output), connect_news))
            try:
                await asyncio.wait_for(request_started.wait(), timeout=1)
                await asyncio.wait_for(stream_exhausted.wait(), timeout=1)
                assert not runtime.done()
                release_request.set()
                await asyncio.wait_for(runtime, timeout=1)
            finally:
                release_request.set()
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)
            pending_tasks = _tasks_started_after(existing_tasks)
            assert not pending_tasks, f"unexpected tasks after stream completion: {pending_tasks!r}"

        assert {_payload_title(payload) for payload in delivered} == {
            "AAPL: Apple launches a phone",
            "MSFT: Microsoft launches a tablet",
        }
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert not [record for record in records if record["msg"] == "failed to close Alpaca news stream"]

    asyncio.run(scenario())


def test_cancellation_during_iterator_close_cancels_delivery_before_close_finishes() -> None:
    """Verify cancellation stops delivery before iterator cleanup finishes."""

    async def scenario() -> None:
        streams = _IteratorCloseCancellationScenario()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(streams.handle_webhook), timeout=10) as client:
            existing_tasks = asyncio.all_tasks()
            runtime = asyncio.create_task(run(_config(), client, _logger(StringIO()), streams.connect_news))
            try:
                await asyncio.wait_for(streams.request_started.wait(), timeout=1)
                await asyncio.wait_for(streams.iterator_close_started.wait(), timeout=1)
                runtime.cancel()
                await asyncio.wait_for(streams.sender_cancelled.wait(), timeout=1)
                assert not runtime.done()
                assert streams.requested_titles == ["AAPL: Apple launches a phone"]
                assert not streams.second_request_started.is_set()
                streams.release_iterator_close.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(runtime, timeout=1)
                assert streams.requested_titles == ["AAPL: Apple launches a phone"]
                assert not streams.second_request_started.is_set()
                leaked_tasks = {
                    task
                    for task in asyncio.all_tasks()
                    if task not in existing_tasks and task is not asyncio.current_task()
                }
                assert not leaked_tasks, f"unexpected tasks before client cleanup: {leaked_tasks!r}"
            finally:
                streams.release_iterator_close.set()
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)

    asyncio.run(scenario())


def test_normal_eof_reports_sanitized_iterator_close_failure() -> None:
    """Verify normal EOF reports iterator cleanup errors without credentials."""

    async def scenario() -> None:
        """Run a news-only stream through normal EOF and iterator cleanup."""
        cleanup_error_message = "private stream credential"

        class NewsStream:
            """End normally while reporting a separate cleanup failure."""

            def __aiter__(self) -> AsyncIterator[NewsItem]:
                """Return this news stream as its iterator."""
                return self

            async def __anext__(self) -> NewsItem:
                """Report normal EOF without yielding an item."""
                raise StopAsyncIteration

            async def aclose(self) -> None:
                """Raise a secret-bearing cleanup error for sanitization checks."""
                raise OSError(cleanup_error_message)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return the news stream used to exercise normal EOF cleanup."""
            return NewsStream()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: httpx2.Response(204))) as client:
            await run(_config(), client, _logger(output), connect_news)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        close_failure = next(record for record in records if record["msg"] == "failed to close Alpaca news stream")
        assert close_failure["error"] == "OSError"
        assert close_failure["stream"] == "news"
        assert cleanup_error_message not in output.getvalue()

    asyncio.run(scenario())


def test_cancellation_reports_sanitized_pending_news_read_failure() -> None:
    """Verify pending news-read cleanup failures are logged without hiding cancellation."""

    async def scenario() -> None:
        """Cancel a news reader while its async generator is suspended."""
        read_waiting = asyncio.Event()
        iterator_cleaned = asyncio.Event()
        never_release = asyncio.Event()
        cleanup_error_message = "private stream credential"
        deliveries: list[httpx2.Request] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            """Record any unexpected webhook delivery."""
            deliveries.append(request)
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return a news generator that fails during cancellation cleanup."""

            async def events() -> AsyncGenerator[NewsItem]:
                """Wait for cancellation and raise a secret-bearing cleanup error."""
                try:
                    read_waiting.set()
                    await never_release.wait()
                    yield NewsItem(
                        symbols=("AAPL",),
                        author="Benzinga Newsdesk",
                        headline="unreachable",
                    )
                finally:
                    iterator_cleaned.set()
                    raise OSError(cleanup_error_message)

            return events()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            existing_tasks = asyncio.all_tasks()
            runtime = asyncio.create_task(run(_config(), client, _logger(output), connect_news))
            try:
                await asyncio.wait_for(read_waiting.wait(), timeout=1)
                runtime.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(runtime, timeout=1)

                assert iterator_cleaned.is_set()
                assert deliveries == []
                pending_tasks = _tasks_started_after(existing_tasks)
                assert not pending_tasks, f"unexpected tasks after cancellation: {pending_tasks!r}"
            finally:
                never_release.set()
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        cleanup_logs = [record for record in records if record["msg"] == "failed to close Alpaca news stream"]
        assert len(cleanup_logs) == 1
        assert cleanup_logs[0]["error"] == "OSError"
        assert cleanup_logs[0]["stream"] == "news"
        assert cleanup_error_message not in output.getvalue()

    asyncio.run(scenario())


def test_cancellation_during_normal_delivery_drain_cancels_http_request() -> None:
    """Verify cancellation during delivery draining cancels the HTTP request."""

    async def scenario() -> None:
        request_started = asyncio.Event()
        stream_exhausted = asyncio.Event()
        sender_cancelled = asyncio.Event()

        async def handler(_request: httpx2.Request) -> httpx2.Response:
            request_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                sender_cancelled.set()
                raise

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(
                    symbols=("AAPL",),
                    author="Benzinga Newsdesk",
                    headline="Apple launches a phone",
                )
                stream_exhausted.set()

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            runtime = asyncio.create_task(run(_config(), client, _logger(StringIO()), connect_news))
            try:
                await asyncio.wait_for(request_started.wait(), timeout=1)
                await asyncio.wait_for(stream_exhausted.wait(), timeout=1)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(runtime), timeout=0.1)
                runtime.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(runtime, timeout=1)
                assert sender_cancelled.is_set()
            finally:
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)

    asyncio.run(scenario())


def test_stream_cleanup_error_does_not_hide_established_stream_failure(caplog: pytest.LogCaptureFixture) -> None:
    """Verify cleanup errors do not hide stream failures or expose credentials."""

    async def scenario() -> None:
        """Verify the news read error remains primary through cleanup."""
        cleanup_error_message = "private stream credential"
        news_failure_message = "news stream stopped"

        class NewsStream:
            """Raise the primary read error and a separate close error."""

            def __aiter__(self) -> AsyncIterator[NewsItem]:
                """Return this news stream as its iterator."""
                return self

            async def __anext__(self) -> NewsItem:
                """Raise the established news-stream failure."""
                raise ConnectionError(news_failure_message)

            async def aclose(self) -> None:
                """Raise a secret-bearing cleanup error for sanitization checks."""
                raise OSError(cleanup_error_message)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return the news stream with independent read and close errors."""
            return NewsStream()

        logger = _logger(StringIO())
        logger.addHandler(caplog.handler)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: httpx2.Response(204))) as client:
            with pytest.raises(RuntimeError, match=f"alpaca news stream terminated: {news_failure_message}") as failure:
                await run(_config(), client, logger, connect_news)

        assert str(failure.value) == f"alpaca news stream terminated: {news_failure_message}"
        cleanup_logs = [
            record for record in caplog.records if record.getMessage() == "failed to close Alpaca news stream"
        ]
        assert len(cleanup_logs) == 1
        assert cleanup_logs[0].error == "OSError"
        assert cleanup_logs[0].stream == "news"
        assert cleanup_error_message not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("headline", "kind"),
    [
        ("Apple Q1 EPS ~$(0.10) miss $0.20 Estimate", "earnings"),
        ("Apple Q1 EPS $-0.10 miss $0.20 Estimate", "earnings"),
        (
            "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850",
            "analyst",
        ),
        ("Goldman Sachs Maintains Buy on Apple, Maintains Price Target at $223", "analyst"),
    ],
    ids=[
        "unparseable-approximate-earnings",
        "negative-earnings",
        "announced-analyst-target",
        "maintained-analyst-target",
    ],
)
def test_classified_news_without_payload_is_logged_and_not_delivered(headline: str, kind: str) -> None:
    """Verify classified news without a payload is logged and not delivered."""

    async def scenario() -> None:
        posts: list[str] = []
        output = StringIO()

        async def handler(request: httpx2.Request) -> httpx2.Response:
            posts.append(str(request.url))
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline=headline)

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        skipped = [record for record in records if record["msg"] == "skipping news item"]
        assert posts == []
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "no_payload"
        assert skipped[0]["kind"] == kind

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("disabled_categories", "expected_paths"),
    [
        (("earnings",), {"/analyst", "/news"}),
        (("analyst",), {"/earnings", "/news"}),
        (("news",), {"/earnings", "/analyst"}),
        (("earnings", "analyst", "news"), set()),
        ((), {"/earnings", "/analyst", "/news"}),
    ],
    ids=["earnings-disabled", "analyst-disabled", "news-disabled", "all-disabled", "defaults-enabled"],
)
def test_disabled_categories_are_skipped_without_fallback(
    disabled_categories: tuple[str, ...],
    expected_paths: set[str],
) -> None:
    """Verify disabled categories skip their own deliveries without rerouting."""

    async def scenario() -> None:
        requested_paths: list[str] = []
        output = StringIO()
        environ = {
            "ALPACA_API_KEY": _DUMMY_ALPACA_API_KEY,
            "ALPACA_API_SECRET": _DUMMY_ALPACA_API_SECRET,
            "DISCORD_EARNINGS_WEBHOOKS": "https://discord.test/earnings",
            "DISCORD_ANALYST_WEBHOOKS": "https://discord.test/analyst",
            "DISCORD_NEWS_WEBHOOKS": "https://discord.test/news",
        }
        for category in disabled_categories:
            environ[f"DISCORD_{category.upper()}_ENABLED"] = "false"
        config = load_config(environ)
        items = (
            NewsItem(
                symbols=("AAPL",),
                author="Benzinga Newsdesk",
                headline="Apple Q1 EPS $2.00 beat $1.80 Estimate",
            ),
            NewsItem(
                symbols=("AAPL",),
                author="Benzinga Newsdesk",
                headline="Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
            ),
            NewsItem(
                symbols=(" AAPL ",),
                author="Benzinga Newsdesk",
                headline="Apple &amp; launches a new iPhone",
                summary="Company announcement",
                url="https://example.test/news",
            ),
        )

        async def handler(request: httpx2.Request) -> httpx2.Response:
            """Record the category endpoint and accept the webhook request."""
            requested_paths.append(request.url.path)
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return the three real category examples as a news stream."""

            async def events() -> AsyncIterable[NewsItem]:
                """Yield one item for each runtime delivery category."""
                for item in items:
                    yield item

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(config, client, _logger(output), connect_news)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        skipped = [record for record in records if record["msg"] == "skipping news item"]
        sent = [record for record in records if record["msg"] == "sent Discord webhook"]
        skip_details = {
            "earnings": {
                "headline": "Apple Q1 EPS $2.00 beat $1.80 Estimate",
                "symbols": ["AAPL"],
            },
            "analyst": {
                "headline": "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
                "symbols": ["AAPL"],
            },
            "news": {"headline": "Apple & launches a new iPhone", "symbols": [" AAPL "]},
        }
        expected_skips = [
            {
                "reason": "category_disabled",
                "kind": category,
                **skip_details[category],
                "author": "Benzinga Newsdesk",
            }
            for category in ("earnings", "analyst", "news")
            if category in disabled_categories
        ]

        assert set(requested_paths) == expected_paths
        assert [record["kind"] for record in sent] == [
            category for category in ("earnings", "analyst", "news") if category not in disabled_categories
        ]
        assert [
            {field: record[field] for field in expected}
            for record, expected in zip(skipped, expected_skips, strict=True)
        ] == expected_skips
        assert not {record["kind"] for record in sent} & set(disabled_categories)

    asyncio.run(scenario())


def test_repeated_cancellation_cancels_delivery_worker() -> None:
    """Verify repeated cancellation stops the delivery worker and pending tasks."""

    async def scenario() -> None:
        handler = _RepeatedCancellationDiscordHandler()
        news_stream = _RepeatedCancellationNewsStream(handler.first_request_started)

        async def connect_news() -> AsyncIterable[NewsItem]:
            return news_stream

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            existing_tasks = asyncio.all_tasks()
            runtime = asyncio.create_task(run(_config(), client, _logger(StringIO()), connect_news))
            try:
                await asyncio.wait_for(handler.first_request_started.wait(), timeout=1)
                await asyncio.wait_for(news_stream.second_delivery_queued.wait(), timeout=1)
                runtime.cancel()
                await asyncio.wait_for(news_stream.stream_cancelled.wait(), timeout=1)
                if not runtime.done():
                    runtime.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(runtime, timeout=1)
                assert handler.delivered == []
                pending_tasks = _tasks_started_after(existing_tasks)
                assert not pending_tasks, f"pending tasks after cancellation: {pending_tasks!r}"
                assert handler.sender_cancelled.is_set()
            finally:
                handler.release_request.set()
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)
                await _cancel_tasks_started_after(existing_tasks)

        assert handler.sender_cancelled.is_set()

    asyncio.run(scenario())


def test_full_delivery_queue_fails_runtime_without_waiting() -> None:
    """Verify queue overflow closes the suspended news iterator and cancels delivery."""

    async def scenario() -> None:
        request_started = asyncio.Event()
        release_request = asyncio.Event()
        delivery_cancelled = asyncio.Event()
        iterator_closed = asyncio.Event()
        delivered: list[httpx2.Request] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            """Hold the active webhook until runtime cleanup cancels it."""
            request_started.set()
            try:
                await release_request.wait()
            except asyncio.CancelledError:
                delivery_cancelled.set()
                raise
            delivered.append(request)
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            """Return enough news to fill the bounded delivery queue."""

            async def events() -> AsyncIterable[NewsItem]:
                """Yield news until queue overflow closes this generator."""
                try:
                    yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")
                    await request_started.wait()
                    for _ in range(17):
                        yield NewsItem(
                            symbols=("AAPL",),
                            author="Benzinga Newsdesk",
                            headline="Apple launches a phone",
                        )
                finally:
                    iterator_closed.set()

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            existing_tasks = asyncio.all_tasks()
            runtime = asyncio.create_task(run(_config(), client, _logger(StringIO()), connect_news))
            try:
                with pytest.raises(RuntimeError, match="Discord delivery queue is full"):
                    await asyncio.wait_for(runtime, timeout=1)
                assert iterator_closed.is_set()
                assert delivery_cancelled.is_set()
                assert delivered == []
                pending_tasks = _tasks_started_after(existing_tasks)
                assert not pending_tasks, f"unexpected tasks after queue overflow: {pending_tasks!r}"
            finally:
                release_request.set()
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)
                await _cancel_tasks_started_after(existing_tasks)

    asyncio.run(scenario())


async def _send_websocket_json(send: ASGISend, payload: object) -> None:
    await send({"type": "websocket.send", "text": json.dumps(payload)})
