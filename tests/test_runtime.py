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
from stocknews.logging import JSONFormatter, configure_logging
from stocknews.models import Config, NewsItem, Trade
from stocknews.runtime import run

_STOCK_LOGO = "https://static.stocktitan.net/company-logo/%s.webp"
_TRANSPARENT_PNG = "https://major.io/transparent.png"
_EXPECTED_ALPACA_STREAM_NAMES = frozenset({"news", "stock"})
type ASGIReceive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type ASGISend = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class _RoutedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, routes: dict[str, httpx2.AsyncBaseTransport]) -> None:
        self._routes = routes

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        transport = self._routes.get(request.url.host)
        if transport is None:
            raise AssertionError(f"unexpected request host: {request.url.host}")
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        pass


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


class _StockConnectASGIApp:
    def __init__(self, reject_stock_auth: bool) -> None:
        self.reject_stock_auth = reject_stock_auth
        self.stock_auth_started = asyncio.Event()
        self.release_stock_auth = asyncio.Event()
        self.stock_subscribed = asyncio.Event()
        self.news_subscribed = asyncio.Event()
        self.news_disconnected = asyncio.Event()
        self.stock_disconnected = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        path = scope.get("path")
        assert isinstance(path, str)
        is_news = path.endswith("/news")
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            incoming = await receive()
            if incoming.get("type") == "websocket.disconnect":
                (self.news_disconnected if is_news else self.stock_disconnected).set()
                return
            text = incoming.get("text")
            assert isinstance(text, str)
            request = json.loads(text)
            if request["action"] == "auth":
                if not is_news:
                    self.stock_auth_started.set()
                    if self.reject_stock_auth:
                        await _send_websocket_json(
                            send,
                            {"T": "error", "code": 401, "msg": "invalid private-api-key and private-secret"},
                        )
                        continue
                    await self.release_stock_auth.wait()
                await _send_websocket_json(send, {"T": "success", "msg": "authenticated"})
                continue

            if is_news:
                await _send_websocket_json(send, {"T": "subscription", "news": ["*"]})
                await _send_websocket_json(
                    send,
                    {
                        "T": "n",
                        "symbols": ["AAPL"],
                        "author": "Benzinga Newsdesk",
                        "headline": "Apple launches a phone",
                    },
                )
                self.news_subscribed.set()
            else:
                await _send_websocket_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})
                self.stock_subscribed.set()


class _EstablishedStreamFailureASGIApp:
    def __init__(self, failed_stream: str, terminal_timing: str) -> None:
        self.failed_stream = failed_stream
        self.terminal_timing = terminal_timing
        self.subscribed_streams: set[str] = set()
        self.both_subscribed = asyncio.Event()
        self.report_terminal_error = asyncio.Event()
        self.disconnected_streams: set[str] = set()
        self.both_disconnected = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        path = scope.get("path")
        assert isinstance(path, str)
        stream_name = "news" if path.endswith("/news") else "stock"
        channel = "news" if stream_name == "news" else "trades"
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            incoming = await receive()
            if incoming.get("type") == "websocket.disconnect":
                self.disconnected_streams.add(stream_name)
                if self.disconnected_streams == _EXPECTED_ALPACA_STREAM_NAMES:
                    self.both_disconnected.set()
                return
            text = incoming.get("text")
            assert isinstance(text, str)
            request = json.loads(text)
            if request["action"] == "auth":
                await _send_websocket_json(send, {"T": "success", "msg": "authenticated"})
                continue
            subscriptions = ["*"] if channel == "news" else ["SPY", "QQQ"]
            subscription = {"T": "subscription", channel: subscriptions}
            if stream_name == self.failed_stream and self.terminal_timing == "before-consumer":
                await _send_websocket_json(
                    send,
                    [subscription, {"T": "error", "code": 403, "msg": "request rejected using private-api-key"}],
                )
            else:
                await _send_websocket_json(send, subscription)
            self.subscribed_streams.add(stream_name)
            if self.subscribed_streams == _EXPECTED_ALPACA_STREAM_NAMES:
                self.both_subscribed.set()
            if stream_name == self.failed_stream and self.terminal_timing == "late":
                await self.report_terminal_error.wait()
                await _send_websocket_json(
                    send,
                    {"T": "error", "code": 403, "msg": "request rejected using private-api-key"},
                )


class _CancellationASGIApp:
    def __init__(self) -> None:
        self.disconnected_streams: set[str] = set()
        self.all_disconnected = asyncio.Event()
        self.stock_subscribed = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        path = scope.get("path")
        assert isinstance(path, str)
        stream_name = "news" if path.endswith("/news") else "stock"
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            incoming = await receive()
            if incoming.get("type") == "websocket.disconnect":
                self.disconnected_streams.add(stream_name)
                if self.disconnected_streams == _EXPECTED_ALPACA_STREAM_NAMES:
                    self.all_disconnected.set()
                return
            text = incoming.get("text")
            assert isinstance(text, str)
            request = json.loads(text)
            if request["action"] == "auth":
                await _send_websocket_json(send, {"T": "success", "msg": "authenticated"})
                continue
            if stream_name == "stock":
                await _send_websocket_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})
                self.stock_subscribed.set()
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
        alpaca_api_key="key",
        alpaca_api_secret="secret",
        alpaca_news_stream_url="wss://news.test",
        alpaca_stock_stream_url="wss://stocks.test",
        discord_analyst_webhooks=("https://discord.test/analyst",),
        discord_earnings_webhooks=("https://discord.test/earnings",),
        discord_news_webhooks=("https://discord.test/news",),
        stock_logo=_STOCK_LOGO,
        transparent_png=_TRANSPARENT_PNG,
        blocked_phrases=("would be worth",),
    )


def _logger(output: StringIO) -> logging.Logger:
    logger = logging.Logger("stocknews-test", level=logging.INFO)
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
    assert isinstance(embeds, list) and embeds
    embed = embeds[0]
    assert isinstance(embed, dict)
    title = embed["title"]
    assert isinstance(title, str)
    return title


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

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(StringIO()), connect_news)

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
                        "title": "💚 AAPL: Apple $223.00",
                        "description": "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
                        "color": 0x4CAF50,
                        "image": {"url": _TRANSPARENT_PNG},
                        "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
                    }
                ]
            },
            "/news": {
                "embeds": [
                    {
                        "title": " AAPL : Apple &amp; launches a new iPhone",
                        "description": "Company announcement",
                        "url": "https://example.test/news",
                        "image": {"url": _TRANSPARENT_PNG},
                        "thumbnail": {"url": "https://static.stocktitan.net/company-logo/ aapl .webp"},
                    }
                ]
            },
        }

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
            alpaca_stock_stream_url="ws://stocks.alpaca.test/v2",
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
                application = asyncio.create_task(
                    run_application(config, client, _logger(StringIO()), stop_event, stock_starter=None)
                )
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
        assert _payload_title(discord_handler.received_payloads[0]) == " AAPL : Apple &amp; launches a phone"

    asyncio.run(scenario())


@pytest.mark.parametrize("reject_stock_auth", [False, True], ids=["delayed-auth", "initial-auth-failure"])
def test_real_adapters_keep_news_running_during_stock_connect(reject_stock_auth: bool) -> None:
    """Verify news continues while the stock stream connects or fails auth."""

    async def scenario() -> None:
        alpaca_app = _StockConnectASGIApp(reject_stock_auth)
        discord_delivered = asyncio.Event()
        stock_failure_logged = asyncio.Event()
        requests: list[dict[str, object]] = []
        logs = StringIO()
        logger = _logger(logs)
        logger.addHandler(_LogMessageEvent("failed to connect Alpaca stock stream", stock_failure_logged))

        async def discord_handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(json.loads(request.content))
            discord_delivered.set()
            return httpx2.Response(204)

        config = replace(
            _config(),
            alpaca_news_stream_url="ws://news.alpaca.test/v1beta1/news",
            alpaca_stock_stream_url="ws://stocks.alpaca.test/v2",
            discord_news_webhooks=("https://discord.test/api/webhooks/news/token",),
        )
        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(alpaca_app) as news_transport:
            async with ASGIWebSocketTransport(alpaca_app) as stock_transport:
                routes = {
                    "news.alpaca.test": news_transport,
                    "stocks.alpaca.test": stock_transport,
                    "discord.test": httpx2.MockTransport(discord_handler),
                }
                async with httpx2.AsyncClient(transport=_RoutedTransport(routes), timeout=10) as client:
                    application = asyncio.create_task(run_application(config, client, logger, stop_event))
                    try:
                        await asyncio.wait_for(alpaca_app.stock_auth_started.wait(), timeout=2)
                        await asyncio.wait_for(discord_delivered.wait(), timeout=2)
                        await asyncio.wait_for(alpaca_app.news_subscribed.wait(), timeout=2)
                        if reject_stock_auth:
                            await asyncio.wait_for(stock_failure_logged.wait(), timeout=2)
                        else:
                            assert not alpaca_app.stock_subscribed.is_set()
                            alpaca_app.release_stock_auth.set()
                            await asyncio.wait_for(alpaca_app.stock_subscribed.wait(), timeout=2)
                        application.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await asyncio.wait_for(application, timeout=2)
                        await asyncio.wait_for(alpaca_app.news_disconnected.wait(), timeout=2)
                        await asyncio.wait_for(alpaca_app.stock_disconnected.wait(), timeout=2)
                    finally:
                        if not application.done():
                            application.cancel()
                        await asyncio.gather(application, return_exceptions=True)

        assert len(requests) == 1
        assert "private-api-key" not in logs.getvalue()
        assert "private-secret" not in logs.getvalue()
        if reject_stock_auth:
            assert "failed to connect Alpaca stock stream" in logs.getvalue()
        else:
            assert alpaca_app.stock_subscribed.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failed_stream", "terminal_timing"),
    [("news", "late"), ("stock", "late"), ("news", "before-consumer")],
    ids=["news-late", "stock-late", "news-before-consumer"],
)
def test_established_real_alpaca_stream_errors_fail_the_application(failed_stream: str, terminal_timing: str) -> None:
    """Verify terminal errors from established streams fail the application."""

    async def scenario() -> None:
        alpaca_app = _EstablishedStreamFailureASGIApp(failed_stream, terminal_timing)
        logs = StringIO()

        async def unused_discord_handler(_request: httpx2.Request) -> httpx2.Response:
            raise AssertionError("terminal stream errors must not send Discord messages")

        config = replace(
            _config(),
            alpaca_news_stream_url="ws://news.alpaca.test/v1beta1/news",
            alpaca_stock_stream_url="ws://stocks.alpaca.test/v2",
        )
        async with ASGIWebSocketTransport(alpaca_app) as news_transport:
            async with ASGIWebSocketTransport(alpaca_app) as stock_transport:
                routes = {
                    "news.alpaca.test": news_transport,
                    "stocks.alpaca.test": stock_transport,
                    "discord.test": httpx2.MockTransport(unused_discord_handler),
                }
                async with httpx2.AsyncClient(transport=_RoutedTransport(routes), timeout=10) as client:
                    application = asyncio.create_task(run_application(config, client, _logger(logs), asyncio.Event()))
                    try:
                        await asyncio.wait_for(alpaca_app.both_subscribed.wait(), timeout=2)
                        if terminal_timing == "late":
                            alpaca_app.report_terminal_error.set()
                        runtime_name = "news" if failed_stream == "news" else "stock"
                        with pytest.raises(
                            RuntimeError,
                            match=f"alpaca {runtime_name} stream terminated",
                        ) as terminal:
                            await asyncio.wait_for(application, timeout=2)
                        assert "private-api-key" not in str(terminal.value)
                        await asyncio.wait_for(alpaca_app.both_disconnected.wait(), timeout=2)
                    finally:
                        if not application.done():
                            application.cancel()
                        await asyncio.gather(application, return_exceptions=True)

        assert alpaca_app.subscribed_streams == _EXPECTED_ALPACA_STREAM_NAMES
        assert alpaca_app.disconnected_streams == _EXPECTED_ALPACA_STREAM_NAMES

    asyncio.run(scenario())


def test_real_stream_cancellation_stops_adapters_and_cancels_delivery() -> None:
    """Verify cancellation closes both adapters and cancels delivery."""

    async def scenario() -> None:
        alpaca_app = _CancellationASGIApp()
        discord_handler = _CancellationDiscordHandler()
        skipped_later_item = asyncio.Event()
        logs = StringIO()
        logger = _logger(logs)
        logger.addHandler(_LogMessageEvent("skipping news item", skipped_later_item))

        config = replace(
            _config(),
            alpaca_news_stream_url="ws://news.alpaca.test/v1beta1/news",
            alpaca_stock_stream_url="ws://stocks.alpaca.test/v2",
            discord_news_webhooks=("https://discord.test/api/webhooks/news/private-token",),
        )
        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(alpaca_app) as news_transport:
            async with ASGIWebSocketTransport(alpaca_app) as stock_transport:
                routes = {
                    "news.alpaca.test": news_transport,
                    "stocks.alpaca.test": stock_transport,
                    "discord.test": httpx2.MockTransport(discord_handler),
                }
                async with httpx2.AsyncClient(transport=_RoutedTransport(routes), timeout=10) as client:
                    existing_tasks = asyncio.all_tasks()
                    application = asyncio.create_task(run_application(config, client, logger, stop_event))
                    try:
                        await asyncio.wait_for(discord_handler.first_request_started.wait(), timeout=2)
                        await asyncio.wait_for(skipped_later_item.wait(), timeout=2)
                        await asyncio.wait_for(alpaca_app.stock_subscribed.wait(), timeout=2)
                        stop_event.set()
                        application.cancel()
                        await asyncio.wait_for(alpaca_app.all_disconnected.wait(), timeout=2)
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

        assert alpaca_app.disconnected_streams == {"news", "stock"}
        assert discord_handler.requested_titles == []
        assert discord_handler.sender_cancelled.is_set()

    asyncio.run(scenario())


def test_stock_connection_failure_warns_while_news_continues() -> None:
    """Verify a stock connection failure is logged while news is delivered."""

    async def scenario() -> None:
        stock_started = asyncio.Event()
        output = StringIO()
        sent: list[dict[str, object]] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            sent.append(json.loads(request.content))
            return httpx2.Response(204)

        async def connect_stock() -> AsyncIterable[Trade]:
            stock_started.set()
            raise ConnectionError("stock stream unavailable")

        async def connect_news() -> AsyncIterable[NewsItem]:
            await stock_started.wait()

            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(
                    symbols=("MSFT",),
                    author="Benzinga Newsdesk",
                    headline="Microsoft announces a new Surface",
                )

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(output), connect_news, connect_stock)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert any(
            record["msg"] == "failed to connect Alpaca stock stream" and record["error"] == "stock stream unavailable"
            for record in records
        )
        assert _payload_title(sent[0]) == "MSFT: Microsoft announces a new Surface"

    asyncio.run(scenario())


def test_news_runs_while_stock_connection_is_pending() -> None:
    """Verify news runs while the stock connection is pending."""

    async def scenario() -> None:
        stock_started = asyncio.Event()
        stock_stopped = asyncio.Event()
        release_stock = asyncio.Event()
        sent: list[dict[str, object]] = []

        async def handler(request: httpx2.Request) -> httpx2.Response:
            sent.append(json.loads(request.content))
            return httpx2.Response(204)

        async def connect_stock() -> AsyncIterable[Trade]:
            stock_started.set()
            try:
                await release_stock.wait()
            finally:
                stock_stopped.set()

            async def events() -> AsyncIterable[Trade]:
                yield Trade(symbol="SPY", price=0, size=0)

            return events()

        async def connect_news() -> AsyncIterable[NewsItem]:
            await stock_started.wait()

            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            await run(_config(), client, _logger(StringIO()), connect_news, connect_stock)

        assert stock_stopped.is_set()
        assert _payload_title(sent[0]) == "AAPL: Apple launches a phone"

    asyncio.run(scenario())


def test_established_stock_stream_failure_is_fatal_and_trade_is_logged() -> None:
    """Verify an established stock stream failure is fatal and logs its trade."""

    async def scenario() -> None:
        output = StringIO()
        never = asyncio.Event()

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                await never.wait()
                yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="unreachable")

            return events()

        async def connect_stock() -> AsyncIterable[Trade]:
            async def events() -> AsyncIterable[Trade]:
                yield Trade(
                    symbol="SPY",
                    price=500.25,
                    size=100,
                    exchange="V",
                    timestamp="2026-09-21T14:30:00Z",
                    conditions=("@", "F"),
                    tape="C",
                )
                raise ConnectionError("stock stream stopped")

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(204))) as client:
            with pytest.raises(RuntimeError, match="alpaca stock stream terminated: stock stream stopped"):
                await run(_config(), client, _logger(output), connect_news, connect_stock)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        trade_log = next(record for record in records if record["msg"] == "stock trade")
        assert trade_log == {
            "time": trade_log["time"],
            "level": "INFO",
            "msg": "stock trade",
            "symbol": "SPY",
            "price": 500.25,
            "size": 100,
            "exchange": "V",
            "timestamp": "2026-09-21T14:30:00Z",
            "conditions": ["@", "F"],
            "tape": "C",
        }

    asyncio.run(scenario())


def test_stream_failure_closes_an_iterator_suspended_after_its_last_read() -> None:
    """Verify a stream failure closes an iterator suspended after its final item."""

    async def scenario() -> None:
        first_delivery_started = asyncio.Event()
        second_news_yielded = asyncio.Event()
        release_second_news = asyncio.Event()
        news_iterator_closed = asyncio.Event()
        news_iterators: list[AsyncGenerator[NewsItem]] = []

        async def handler(_request: httpx2.Request) -> httpx2.Response:
            first_delivery_started.set()
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncGenerator[NewsItem]:
                try:
                    yield NewsItem(
                        symbols=("AAPL",),
                        author="Benzinga Newsdesk",
                        headline="Apple launches a phone",
                    )
                    await first_delivery_started.wait()
                    second_news_yielded.set()
                    yield NewsItem(
                        symbols=("MSFT",),
                        author="Benzinga Newsdesk",
                        headline="Microsoft launches a tablet",
                    )
                    await release_second_news.wait()
                finally:
                    news_iterator_closed.set()

            stream = events()
            news_iterators.append(stream)
            return stream

        async def connect_stock() -> AsyncIterable[Trade]:
            async def events() -> AsyncIterable[Trade]:
                await second_news_yielded.wait()
                if release_second_news.is_set():
                    yield Trade(symbol="SPY", price=0, size=0)
                    return
                raise ConnectionError("stock stream stopped")

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            with pytest.raises(RuntimeError, match="alpaca stock stream terminated: stock stream stopped"):
                await run(_config(), client, _logger(StringIO()), connect_news, connect_stock)

        try:
            assert news_iterator_closed.is_set()
        finally:
            await news_iterators[0].aclose()

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
        assert failed_webhook not in log_output
        assert "secret-fail" not in log_output

    asyncio.run(scenario())


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
            raise RuntimeError(f"HTTP request failed for {quoted_webhook}")
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


def test_normal_stream_completion_drains_queued_discord_deliveries() -> None:
    """Verify normal stream completion waits for queued Discord deliveries."""

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
            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")
                yield NewsItem(symbols=("MSFT",), author="Benzinga Newsdesk", headline="Microsoft launches a tablet")
                stream_exhausted.set()

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            runtime = asyncio.create_task(run(_config(), client, _logger(StringIO()), connect_news))
            await asyncio.wait_for(request_started.wait(), timeout=1)
            await asyncio.wait_for(stream_exhausted.wait(), timeout=1)
            assert not runtime.done()
            release_request.set()
            await asyncio.wait_for(runtime, timeout=1)

        assert {_payload_title(payload) for payload in delivered} == {
            "AAPL: Apple launches a phone",
            "MSFT: Microsoft launches a tablet",
        }

    asyncio.run(scenario())


def test_stock_stream_eof_logs_trade_and_drains_queued_delivery() -> None:
    """Verify stock EOF logs its trade and drains queued news delivery."""

    async def scenario() -> None:
        expected_spy_trade_price = 500.25
        request_started = asyncio.Event()
        release_request = asyncio.Event()
        news_iterator_closed = asyncio.Event()
        delivered: list[dict[str, object]] = []
        output = StringIO()

        async def handler(request: httpx2.Request) -> httpx2.Response:
            request_started.set()
            await release_request.wait()
            delivered.append(json.loads(request.content))
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                try:
                    yield NewsItem(
                        symbols=("AAPL",),
                        author="Benzinga Newsdesk",
                        headline="Apple launches a phone",
                    )
                    await asyncio.Event().wait()
                finally:
                    news_iterator_closed.set()

            return events()

        async def connect_stock() -> AsyncIterable[Trade]:
            class StockStream:
                def __init__(self) -> None:
                    self._yielded = False

                def __aiter__(self) -> AsyncIterator[Trade]:
                    return self

                async def __anext__(self) -> Trade:
                    if self._yielded:
                        raise StopAsyncIteration
                    await request_started.wait()
                    self._yielded = True
                    return Trade(symbol="SPY", price=expected_spy_trade_price, size=100)

            return StockStream()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            runtime = asyncio.create_task(run(_config(), client, _logger(output), connect_news, connect_stock))
            try:
                await asyncio.wait_for(request_started.wait(), timeout=1)
                await asyncio.wait_for(news_iterator_closed.wait(), timeout=1)
                assert not runtime.done()
                release_request.set()
                await asyncio.wait_for(runtime, timeout=1)
            finally:
                release_request.set()
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)

        trade_log = next(json.loads(line) for line in output.getvalue().splitlines() if '"msg":"stock trade"' in line)
        assert trade_log["symbol"] == "SPY"
        assert trade_log["price"] == expected_spy_trade_price
        assert _payload_title(delivered[0]) == "AAPL: Apple launches a phone"

    asyncio.run(scenario())


def test_cancellation_during_iterator_close_cancels_delivery_before_close_finishes() -> None:
    """Verify cancellation stops delivery before iterator cleanup finishes."""

    async def scenario() -> None:
        request_started = asyncio.Event()
        iterator_close_started = asyncio.Event()
        release_iterator_close = asyncio.Event()
        sender_cancelled = asyncio.Event()
        second_request_started = asyncio.Event()
        requested_titles: list[str] = []

        class NewsStream:
            def __init__(self) -> None:
                self._yielded = False
                self._second_yielded = False

            def __aiter__(self) -> AsyncIterator[NewsItem]:
                return self

            async def __anext__(self) -> NewsItem:
                if self._yielded:
                    if self._second_yielded:
                        raise StopAsyncIteration
                    self._second_yielded = True
                    return NewsItem(
                        symbols=("MSFT",),
                        author="Benzinga Newsdesk",
                        headline="Microsoft launches a tablet",
                    )
                self._yielded = True
                return NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")

            async def aclose(self) -> None:
                iterator_close_started.set()
                await release_iterator_close.wait()

        async def handler(request: httpx2.Request) -> httpx2.Response:
            title = _payload_title(json.loads(request.content))
            requested_titles.append(title)
            if title == "MSFT: Microsoft launches a tablet":
                second_request_started.set()
                return httpx2.Response(204)
            request_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                sender_cancelled.set()
                raise

        async def connect_news() -> AsyncIterable[NewsItem]:
            return NewsStream()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            existing_tasks = asyncio.all_tasks()
            runtime = asyncio.create_task(run(_config(), client, _logger(StringIO()), connect_news))
            try:
                await asyncio.wait_for(request_started.wait(), timeout=1)
                await asyncio.wait_for(iterator_close_started.wait(), timeout=1)
                runtime.cancel()
                await asyncio.wait_for(sender_cancelled.wait(), timeout=1)
                assert not runtime.done()
                assert requested_titles == ["AAPL: Apple launches a phone"]
                assert not second_request_started.is_set()
                release_iterator_close.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(runtime, timeout=1)
                assert requested_titles == ["AAPL: Apple launches a phone"]
                assert not second_request_started.is_set()
                leaked_tasks = {
                    task
                    for task in asyncio.all_tasks()
                    if task not in existing_tasks and task is not asyncio.current_task()
                }
                assert not leaked_tasks, f"unexpected tasks before client cleanup: {leaked_tasks!r}"
            finally:
                release_iterator_close.set()
                if not runtime.done():
                    runtime.cancel()
                await asyncio.gather(runtime, return_exceptions=True)

    asyncio.run(scenario())


def test_normal_eof_reports_sanitized_iterator_close_failure() -> None:
    """Verify normal EOF reports iterator cleanup errors without credentials."""

    async def scenario() -> None:
        stock_connected = asyncio.Event()
        stock_closed = asyncio.Event()

        class NewsStream:
            def __aiter__(self) -> AsyncIterator[NewsItem]:
                return self

            async def __anext__(self) -> NewsItem:
                raise StopAsyncIteration

            async def aclose(self) -> None:
                raise OSError("private stream credential")

        class StockStream:
            def __aiter__(self) -> AsyncIterator[Trade]:
                return self

            async def __anext__(self) -> Trade:
                await asyncio.Event().wait()
                raise StopAsyncIteration

            async def aclose(self) -> None:
                stock_closed.set()

        async def connect_news() -> AsyncIterable[NewsItem]:
            await stock_connected.wait()
            return NewsStream()

        async def connect_stock() -> AsyncIterable[Trade]:
            stock_connected.set()
            return StockStream()

        output = StringIO()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: httpx2.Response(204))) as client:
            await run(_config(), client, _logger(output), connect_news, connect_stock)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        close_failure = next(record for record in records if record["msg"] == "failed to close Alpaca news stream")
        assert close_failure["error"] == "OSError"
        assert close_failure["stream"] == "news"
        assert "private stream credential" not in output.getvalue()
        assert stock_closed.is_set()

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
        news_iterator_closed = asyncio.Event()
        never = asyncio.Event()

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                try:
                    await never.wait()
                    yield NewsItem(
                        symbols=("AAPL",),
                        author="Benzinga Newsdesk",
                        headline="unreachable",
                    )
                finally:
                    news_iterator_closed.set()
                    raise OSError("private stream credential")

            return events()

        async def connect_stock() -> AsyncIterable[Trade]:
            async def events() -> AsyncIterable[Trade]:
                yield Trade(symbol="SPY", price=500.25, size=100)
                raise ConnectionError("stock stream stopped")

            return events()

        logger = _logger(StringIO())
        logger.addHandler(caplog.handler)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: httpx2.Response(204))) as client:
            with pytest.raises(RuntimeError, match="alpaca stock stream terminated: stock stream stopped"):
                await run(_config(), client, logger, connect_news, connect_stock)

        assert news_iterator_closed.is_set()
        cleanup_logs = [
            record for record in caplog.records if record.getMessage() == "failed to close Alpaca news stream"
        ]
        assert len(cleanup_logs) == 1
        assert cleanup_logs[0].error == "OSError"
        assert cleanup_logs[0].stream == "news"
        assert "private stream credential" not in caplog.text

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
    """Verify a full delivery queue fails without waiting for the active request."""

    async def scenario() -> None:
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        async def handler(_request: httpx2.Request) -> httpx2.Response:
            request_started.set()
            await release_request.wait()
            return httpx2.Response(204)

        async def connect_news() -> AsyncIterable[NewsItem]:
            async def events() -> AsyncIterable[NewsItem]:
                yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")
                await request_started.wait()
                for _ in range(17):
                    yield NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline="Apple launches a phone")

            return events()

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=10) as client:
            runtime = asyncio.create_task(run(_config(), client, _logger(StringIO()), connect_news))
            try:
                with pytest.raises(RuntimeError, match="Discord delivery queue is full"):
                    await asyncio.wait_for(runtime, timeout=1)
            finally:
                release_request.set()

    asyncio.run(scenario())


async def _send_websocket_json(send: ASGISend, payload: object) -> None:
    await send({"type": "websocket.send", "text": json.dumps(payload)})
