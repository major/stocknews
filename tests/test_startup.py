"""Behavioral tests for command startup validation."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from functools import partial
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport

from stocknews.__main__ import _cancel_and_wait, main, run_application
from stocknews.alpaca import AlpacaStreamError, StreamHandle
from stocknews.models import AlpacaSettings, Config, Trade

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

EXPECTED_CONFIGURATION_ERROR_EXIT_CODE = 2
EXPECTED_STARTUP_LOG_RECORD_COUNT = 2
EXPECTED_ALPACA_STREAM_COUNT = 2
MINIMUM_HTTP_REQUEST_LINE_PARTS = 2
WEBSOCKET_TEXT_FRAME_OPCODE = 0x1
WEBSOCKET_CLOSE_FRAME_OPCODE = 0x8
WEBSOCKET_PING_FRAME_OPCODE = 0x9
WEBSOCKET_PONG_FRAME_OPCODE = 0xA
WEBSOCKET_16_BIT_LENGTH_MARKER = 126
WEBSOCKET_64_BIT_LENGTH_MARKER = 127
DUMMY_ALPACA_CREDENTIALS = ("startup-test-key", "startup-test-secret")
DUMMY_ALPACA_API_KEY, DUMMY_ALPACA_API_SECRET = DUMMY_ALPACA_CREDENTIALS
DUMMY_FAILURE_CREDENTIALS = ("startup-failure-key", "startup-failure-secret")
DUMMY_FAILURE_API_KEY, DUMMY_FAILURE_API_SECRET = DUMMY_FAILURE_CREDENTIALS
DUMMY_REPR_CREDENTIALS = ("repr-test-key", "repr-test-secret")
DUMMY_REPR_API_KEY, DUMMY_REPR_API_SECRET = DUMMY_REPR_CREDENTIALS
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STARTUP_TEST_LOGGER_NAMES = (
    "startup-stream-test",
    "startup-stock-termination-test",
    "startup-terminal-test",
)


@pytest.fixture(autouse=True)
def isolate_startup_test_loggers() -> Iterator[None]:
    """Isolate dedicated startup loggers and restore their shared state."""
    loggers = [logging.getLogger(name) for name in STARTUP_TEST_LOGGER_NAMES]
    states = [
        (logger.handlers[:], logger.filters[:], logger.level, logger.propagate, logger.disabled) for logger in loggers
    ]
    for logger in loggers:
        logger.handlers.clear()
        logger.filters.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = False
        logger.disabled = False

    yield

    for logger, (handlers, filters, level, propagate, disabled) in zip(loggers, states, strict=True):
        logger.handlers[:] = handlers
        logger.filters[:] = filters
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


class _ObservedTradeQueue(asyncio.Queue[Trade]):
    """Signal when the stock reader starts waiting for a trade."""

    def __init__(self) -> None:
        """Initialize the queue and its consumer-waiting event."""
        super().__init__()
        self.consumer_waiting = asyncio.Event()

    async def get(self) -> Trade:
        """Mark the consumer as waiting before reading the next trade."""
        self.consumer_waiting.set()
        return await super().get()


@dataclass
class _StockEofTestState:
    """Hold state shared by the stock-EOF test collaborators."""

    stock_completion: str
    expect_trade: bool
    delivery_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_delivery: asyncio.Event = field(default_factory=asyncio.Event)
    news_disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    finish_stock: asyncio.Event = field(default_factory=asyncio.Event)
    trade_logged: asyncio.Event = field(default_factory=asyncio.Event)
    delivered: list[dict[str, object]] = field(default_factory=list)
    stock_queues: list[_ObservedTradeQueue] = field(default_factory=list)
    stock_tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def start_stock(
        self,
        _client,
        *,
        settings: AlpacaSettings,
        stop_event: asyncio.Event,
        on_terminated: Callable[[AlpacaStreamError], None] | None = None,
    ) -> StreamHandle[Trade]:
        """Start this scenario's stock task and return its observed trade queue.

        Args:
            _client: HTTP client supplied by the application.
            settings: Alpaca credentials and stream URLs supplied by the application.
            stop_event: Application stop event.
            on_terminated: Callback for reporting stream termination.

        Returns:
            A stock stream handle configured for this scenario's race case.
        """
        events = _ObservedTradeQueue()
        self.stock_queues.append(events)

        if self.stock_completion == "completed-with-queued-trade":
            await self.delivery_started.wait()
            events.put_nowait(Trade(symbol="SPY", price=500.25, size=100))
            task = asyncio.create_task(_completed_stock_stream())
            await task
        else:
            task = asyncio.create_task(_wait_for_stock_eof(self.finish_stock))

        self.stock_tasks.append(task)
        return StreamHandle(events=events, task=task)


class _TradeLogObserver(logging.Handler):
    """Signal when the application logs a stock trade."""

    def __init__(self, trade_logged: asyncio.Event) -> None:
        """Store the event used to observe a trade log."""
        super().__init__()
        self.trade_logged = trade_logged

    def emit(self, record: logging.LogRecord) -> None:
        """Set the event when the application emits a stock trade log."""
        if record.getMessage() == "stock trade":
            self.trade_logged.set()


async def _stock_eof_news_app(scope, receive, send, *, state: _StockEofTestState) -> None:
    """Serve the Alpaca news ASGI messages used by stock-EOF scenarios.

    Args:
        scope: ASGI connection scope.
        receive: ASGI receive callable.
        send: ASGI send callable.
        state: Events and values shared with the stock-EOF scenario.
    """
    assert scope["path"] == "/v1beta1/news"
    await receive()
    await send({"type": "websocket.accept"})

    while True:
        message = await receive()
        if message["type"] == "websocket.disconnect":
            state.news_disconnected.set()
            return

        request = json.loads(message["text"])
        if request["action"] == "auth":
            response = {"T": "success", "msg": "authenticated"}
            await send({"type": "websocket.send", "text": json.dumps(response)})
        else:
            subscription = {"T": "subscription", "news": ["*"]}
            await send({"type": "websocket.send", "text": json.dumps(subscription)})
            news_item = {
                "T": "n",
                "symbols": ["AAPL"],
                "author": "Benzinga Newsdesk",
                "headline": "Apple launches a phone",
            }
            await send({"type": "websocket.send", "text": json.dumps(news_item)})


async def _completed_stock_stream() -> None:
    """Return immediately for a stock task representing completed startup."""


async def _wait_for_stock_eof(finish_stock: asyncio.Event) -> None:
    """Keep a pending stock stream open until its test releases it."""
    await finish_stock.wait()


async def _complete_stock_eof_application(
    state: _StockEofTestState,
    application: asyncio.Task[None],
) -> None:
    """Release the stock-EOF scenario and wait for orderly application shutdown.

    Args:
        state: Events and collections shared with the stock-EOF scenario.
        application: The running application task.
    """
    try:
        await asyncio.wait_for(state.delivery_started.wait(), timeout=2)
        if state.stock_completion != "completed-with-queued-trade":
            events = state.stock_queues[0]
            await asyncio.wait_for(events.consumer_waiting.wait(), timeout=2)
            if state.stock_completion == "pending-with-queued-trade":
                # Queue a final trade after the reader's completion callback is registered.
                state.stock_tasks[0].add_done_callback(
                    lambda _task: events.put_nowait(Trade(symbol="SPY", price=500.25, size=100))
                )
            state.finish_stock.set()
            await asyncio.wait_for(state.stock_tasks[0], timeout=2)

        if state.expect_trade:
            await asyncio.wait_for(state.trade_logged.wait(), timeout=2)
        assert not application.done()
        state.release_delivery.set()
        await asyncio.wait_for(application, timeout=2)
        await asyncio.wait_for(state.news_disconnected.wait(), timeout=2)
    finally:
        state.release_delivery.set()
        state.finish_stock.set()
        if not application.done():
            application.cancel()
        await asyncio.gather(application, return_exceptions=True)


async def _run_stock_eof_scenario(stock_completion: str, expect_trade: bool) -> None:
    """Exercise queued-news draining for one stock-EOF race scenario.

    Args:
        stock_completion: The stock task completion timing to exercise.
        expect_trade: Whether a queued final trade should be logged.
    """
    state = _StockEofTestState(stock_completion, expect_trade)
    config = Config(
        alpaca_api_key=DUMMY_ALPACA_API_KEY,
        alpaca_api_secret=DUMMY_ALPACA_API_SECRET,
        alpaca_news_stream_url="ws://news.test/v1beta1/news",
        alpaca_stock_stream_url="wss://stocks.test/v2",
        discord_analyst_webhooks=(),
        discord_earnings_webhooks=(),
        discord_news_webhooks=("https://discord.test/webhook/private-token",),
        stock_logo="https://example.test/%s.webp",
        transparent_png="https://example.test/transparent.png",
        blocked_phrases=(),
    )
    output = StringIO()
    logger = logging.getLogger("startup-stream-test")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler(output))
    logger.addHandler(_TradeLogObserver(state.trade_logged))
    stop_event = asyncio.Event()

    async with ASGIWebSocketTransport(partial(_stock_eof_news_app, state=state)) as websocket_transport:
        mounts = {
            "ws://news.test": websocket_transport,
            "https://discord.test": httpx2.MockTransport(partial(_stock_eof_discord_handler, state=state)),
        }
        async with httpx2.AsyncClient(mounts=mounts, timeout=10) as client:
            application = asyncio.create_task(
                run_application(
                    config,
                    client,
                    logger,
                    stop_event,
                    stock_starter=state.start_stock,
                )
            )
            await _complete_stock_eof_application(state, application)

    assert stop_event.is_set()
    assert len(state.delivered) == 1
    assert state.delivered[0]["embeds"][0]["title"] == "AAPL: Apple launches a phone"
    assert ("stock trade" in output.getvalue()) is expect_trade


async def _stock_eof_discord_handler(
    request: httpx2.Request,
    *,
    state: _StockEofTestState,
) -> httpx2.Response:
    """Hold Discord delivery until the stock-EOF scenario releases it.

    Args:
        request: The HTTP request made by the application.
        state: Events and collections shared with the stock-EOF scenario.

    Returns:
        A successful Discord response after recording the embed payload.
    """
    state.delivery_started.set()
    await state.release_delivery.wait()
    state.delivered.append(json.loads(request.content))
    return httpx2.Response(204)


@dataclass
class _TerminatedStockTestState:
    """Hold state shared by the terminated-stock test collaborators."""

    failure_message: str = "Alpaca trades stream connection failed: WebSocketNetworkError"
    news_subscribed: asyncio.Event = field(default_factory=asyncio.Event)
    news_disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    news_app_task: asyncio.Task[None] | None = None
    stock_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    webhook_requests: list[httpx2.Request] = field(default_factory=list)
    stock_failure: AlpacaStreamError = field(init=False)

    def __post_init__(self) -> None:
        """Create the stock stream failure reported to the application."""
        self.stock_failure = AlpacaStreamError(self.failure_message)

    async def start_stock(
        self,
        _client,
        *,
        settings: AlpacaSettings,
        stop_event: asyncio.Event,
        on_terminated: Callable[[AlpacaStreamError], None] | None = None,
    ) -> StreamHandle[Trade]:
        """Report termination after this scenario subscribes to news.

        Args:
            _client: HTTP client supplied by the application.
            settings: Alpaca credentials and stream URLs supplied by the application.
            stop_event: Application stop event.
            on_terminated: Callback for reporting stream termination.

        Returns:
            A stock stream handle whose task has already completed.
        """
        await self.news_subscribed.wait()
        task = asyncio.create_task(_completed_stock_stream())
        await task
        assert not self.news_disconnected.is_set()
        on_terminated(self.stock_failure)
        self.stock_tasks.append(task)
        return StreamHandle(events=asyncio.Queue(), task=task)


async def _terminated_stock_news_app(scope, receive, send, *, state: _TerminatedStockTestState) -> None:
    """Serve news ASGI messages while the stock stream terminates.

    Args:
        scope: ASGI connection scope.
        receive: ASGI receive callable.
        send: ASGI send callable.
        state: Events and task references shared with the termination scenario.
    """
    state.news_app_task = asyncio.current_task()
    assert scope["path"] == "/v1beta1/news"
    await receive()
    await send({"type": "websocket.accept"})

    while True:
        message = await receive()
        if message["type"] == "websocket.disconnect":
            state.news_disconnected.set()
            return

        request = json.loads(message["text"])
        if request["action"] == "auth":
            response = {"T": "success", "msg": "authenticated"}
            await send({"type": "websocket.send", "text": json.dumps(response)})
        else:
            subscription = {"T": "subscription", "news": ["*"]}
            await send({"type": "websocket.send", "text": json.dumps(subscription)})
            state.news_subscribed.set()


async def _record_terminated_stock_webhook_request(
    request: httpx2.Request,
    state: _TerminatedStockTestState,
) -> httpx2.Response:
    """Record an unexpected webhook request from a terminated-stock scenario.

    Args:
        request: The HTTP request made by the application.
        state: Collections shared with the termination scenario.

    Returns:
        A successful Discord response.
    """
    state.webhook_requests.append(request)
    return httpx2.Response(204)


async def _run_terminated_stock_scenario() -> None:
    """Verify a terminated stock handle closes the active news stream."""
    state = _TerminatedStockTestState()
    config = Config(
        alpaca_api_key=DUMMY_ALPACA_API_KEY,
        alpaca_api_secret=DUMMY_ALPACA_API_SECRET,
        alpaca_news_stream_url="ws://news.test/v1beta1/news",
        alpaca_stock_stream_url="wss://stocks.test/v2",
        discord_analyst_webhooks=(),
        discord_earnings_webhooks=(),
        discord_news_webhooks=("https://discord.test/webhook/startup-test-token",),
        stock_logo="https://example.test/%s.webp",
        transparent_png="https://example.test/transparent.png",
        blocked_phrases=(),
    )
    stop_event = asyncio.Event()
    logger = logging.getLogger("startup-stock-termination-test")

    async with ASGIWebSocketTransport(partial(_terminated_stock_news_app, state=state)) as websocket_transport:
        mounts = {
            "ws://news.test": websocket_transport,
            "https://discord.test": httpx2.MockTransport(
                partial(_record_terminated_stock_webhook_request, state=state)
            ),
        }
        async with httpx2.AsyncClient(mounts=mounts, timeout=10) as client:
            # Context-owned tasks belong to the baseline. The ASGI server task
            # created for this request is awaited before checking for new leaks.
            tasks_before_application = asyncio.all_tasks()
            with pytest.raises(RuntimeError, match="alpaca stock stream terminated") as error:
                await asyncio.wait_for(
                    run_application(
                        config,
                        client,
                        logger,
                        stop_event,
                        stock_starter=state.start_stock,
                    ),
                    timeout=2,
                )

            assert str(error.value) == f"alpaca stock stream terminated: {state.failure_message}"
            assert DUMMY_ALPACA_API_SECRET not in str(error.value)
            assert stop_event.is_set()
            assert len(state.stock_tasks) == 1
            assert state.stock_tasks[0].done()
            await asyncio.wait_for(state.news_disconnected.wait(), timeout=2)
            assert state.news_app_task is not None
            await asyncio.wait_for(state.news_app_task, timeout=2)

            new_pending_tasks = asyncio.all_tasks() - tasks_before_application
            current_task = asyncio.current_task()
            if current_task is not None:
                new_pending_tasks.discard(current_task)
            assert not new_pending_tasks, f"pending tasks after stock termination: {new_pending_tasks!r}"
            assert state.webhook_requests == []


@dataclass
class _SigtermWebSocketState:
    """Hold events and connection tasks for the real SIGTERM WebSocket server."""

    stop_during_authentication: bool
    subscribed_streams: set[str] = field(default_factory=set)
    authentication_streams: set[str] = field(default_factory=set)
    disconnected_streams: set[str] = field(default_factory=set)
    both_subscribed: asyncio.Event = field(default_factory=asyncio.Event)
    both_authenticated: asyncio.Event = field(default_factory=asyncio.Event)
    both_disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    connection_tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def note_disconnected(self, stream_name: str) -> None:
        """Record a disconnected stream and signal when both streams close.

        Args:
            stream_name: The Alpaca stream that disconnected.
        """
        self.disconnected_streams.add(stream_name)
        if len(self.disconnected_streams) == EXPECTED_ALPACA_STREAM_COUNT:
            self.both_disconnected.set()


async def _send_websocket_frame(
    writer: asyncio.StreamWriter,
    opcode: int,
    payload: bytes = b"",
) -> None:
    """Encode and send one unmasked server-to-client WebSocket frame.

    Args:
        writer: Stream writer for the WebSocket connection.
        opcode: RFC WebSocket frame opcode.
        payload: Frame payload bytes.
    """
    first = 0x80 | opcode
    length = len(payload)
    if length < WEBSOCKET_16_BIT_LENGTH_MARKER:
        header = bytes((first, length))
    elif length < 2**16:
        header = bytes((first, WEBSOCKET_16_BIT_LENGTH_MARKER)) + length.to_bytes(2, "big")
    else:
        header = bytes((first, WEBSOCKET_64_BIT_LENGTH_MARKER)) + length.to_bytes(8, "big")
    writer.write(header + payload)
    await writer.drain()


async def _send_websocket_json(writer: asyncio.StreamWriter, payload: object) -> None:
    """Encode JSON as a WebSocket text frame.

    Args:
        writer: Stream writer for the WebSocket connection.
        payload: JSON-compatible message to send.
    """
    await _send_websocket_frame(writer, WEBSOCKET_TEXT_FRAME_OPCODE, json.dumps(payload).encode())


async def _receive_websocket_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read and decode one client-to-server WebSocket frame.

    Args:
        reader: Stream reader for the WebSocket connection.

    Returns:
        The frame opcode and decoded payload bytes.
    """
    first, second = await reader.readexactly(2)
    length = second & 0x7F
    if length == WEBSOCKET_16_BIT_LENGTH_MARKER:
        length = int.from_bytes(await reader.readexactly(2), "big")
    elif length == WEBSOCKET_64_BIT_LENGTH_MARKER:
        length = int.from_bytes(await reader.readexactly(8), "big")
    mask = await reader.readexactly(4) if second & 0x80 else b""
    payload = await reader.readexactly(length)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return first & 0x0F, payload


async def _read_websocket_request_path(reader: asyncio.StreamReader) -> str:
    """Read the request path from an incoming HTTP upgrade request.

    Args:
        reader: Stream reader for the new TCP connection.

    Returns:
        The request path, after validating the request-line fields.
    """
    request_line = await reader.readline()
    parts = request_line.decode().split()
    assert len(parts) >= MINIMUM_HTTP_REQUEST_LINE_PARTS
    return parts[1]


async def _read_websocket_key(reader: asyncio.StreamReader) -> str:
    """Read the WebSocket key from the HTTP upgrade headers.

    Args:
        reader: Stream reader positioned after the request line.

    Returns:
        The ``Sec-WebSocket-Key`` header value.
    """
    headers: dict[str, str] = {}
    while line := await reader.readline():
        if line == b"\r\n":
            break
        name, value = line.decode().split(":", maxsplit=1)
        headers[name.casefold()] = value.strip()
    return headers["sec-websocket-key"]


async def _complete_websocket_upgrade(writer: asyncio.StreamWriter, key: str) -> None:
    """Write the RFC 6455 HTTP upgrade response.

    Args:
        writer: Stream writer for the new WebSocket connection.
        key: Client-provided WebSocket handshake key.
    """
    # RFC 6455 requires SHA-1 for the upgrade handshake, not for security.
    digest = hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(),
        usedforsecurity=False,
    ).digest()
    accept = base64.b64encode(digest).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    writer.write(response.encode())
    await writer.drain()


async def _handle_websocket_control_frame(
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
    stream_name: str,
    opcode: int,
    payload: bytes,
) -> bool:
    """Acknowledge close and ping frames, returning whether the peer closed.

    Args:
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback stream server.
        stream_name: Name of the Alpaca stream on this connection.
        opcode: Received WebSocket frame opcode.
        payload: Received WebSocket frame payload.

    Returns:
        Whether a close frame ended the connection.
    """
    if opcode == WEBSOCKET_CLOSE_FRAME_OPCODE:
        await _send_websocket_frame(writer, WEBSOCKET_CLOSE_FRAME_OPCODE, payload[:125])
        state.note_disconnected(stream_name)
        return True
    if opcode == WEBSOCKET_PING_FRAME_OPCODE:
        await _send_websocket_frame(writer, WEBSOCKET_PONG_FRAME_OPCODE, payload)
    return False


async def _authenticate_sigterm_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
    stream_name: str,
) -> bool:
    """Acknowledge stream authentication or wait for SIGTERM during news auth.

    Args:
        reader: Stream reader for the WebSocket connection.
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback stream server.
        stream_name: Name of the Alpaca stream on this connection.

    Returns:
        Whether a close frame ended the connection while news auth was pending.
    """
    state.authentication_streams.add(stream_name)
    if len(state.authentication_streams) == EXPECTED_ALPACA_STREAM_COUNT:
        state.both_authenticated.set()
    if state.stop_during_authentication and stream_name == "news":
        while True:
            opcode, payload = await _receive_websocket_frame(reader)
            if await _handle_websocket_control_frame(writer, state, stream_name, opcode, payload):
                return True
    await _send_websocket_json(writer, {"T": "success", "msg": "authenticated"})
    return False


async def _subscribe_sigterm_stream(
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
    stream_name: str,
) -> None:
    """Send the stream-specific subscription response and record subscription.

    Args:
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback stream server.
        stream_name: Name of the Alpaca stream on this connection.
    """
    if stream_name == "news":
        await _send_websocket_json(writer, {"T": "subscription", "news": ["*"]})
    else:
        await _send_websocket_json(writer, {"T": "subscription", "trades": ["SPY", "QQQ"]})
    state.subscribed_streams.add(stream_name)
    if len(state.subscribed_streams) == EXPECTED_ALPACA_STREAM_COUNT:
        state.both_subscribed.set()


async def _handle_sigterm_stream_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
    stream_name: str,
    payload: bytes,
) -> bool:
    """Handle one Alpaca authentication or subscription request.

    Args:
        reader: Stream reader for the WebSocket connection.
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback stream server.
        stream_name: Name of the Alpaca stream on this connection.
        payload: JSON request bytes from the WebSocket text frame.

    Returns:
        Whether a close frame ended the connection during authentication.
    """
    request = json.loads(payload)
    if request["action"] == "auth":
        return await _authenticate_sigterm_stream(reader, writer, state, stream_name)
    await _subscribe_sigterm_stream(writer, state, stream_name)
    return False


async def _serve_sigterm_websocket_session(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
    stream_name: str,
) -> None:
    """Serve authentication and subscription messages on an Alpaca stream.

    Args:
        reader: Stream reader for the WebSocket connection.
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback stream server.
        stream_name: Name of the Alpaca stream on this connection.
    """
    while True:
        opcode, payload = await _receive_websocket_frame(reader)
        if await _handle_websocket_control_frame(writer, state, stream_name, opcode, payload):
            return
        if opcode != WEBSOCKET_TEXT_FRAME_OPCODE:
            continue
        if await _handle_sigterm_stream_request(reader, writer, state, stream_name, payload):
            return


async def _serve_sigterm_websocket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
) -> None:
    """Upgrade and serve one real-loopback Alpaca WebSocket connection.

    Args:
        reader: Stream reader for the new TCP connection.
        writer: Stream writer for the new TCP connection.
        state: Shared state for the real-loopback stream server.
    """
    path = ""
    try:
        path = await _read_websocket_request_path(reader)
        key = await _read_websocket_key(reader)
        await _complete_websocket_upgrade(writer, key)
        stream_name = "news" if path.endswith("/news") else "stock"
        await _serve_sigterm_websocket_session(reader, writer, state, stream_name)
    except asyncio.IncompleteReadError, ConnectionError:
        if path:
            state.note_disconnected("news" if path.endswith("/news") else "stock")
    finally:
        writer.close()
        try:  # noqa: SIM105 - keep exception groups intact while suppressing direct errors
            await writer.wait_closed()
        except ConnectionError:
            pass


def _accept_sigterm_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    state: _SigtermWebSocketState,
) -> None:
    """Create and track a task for one incoming SIGTERM-test connection.

    Args:
        reader: Stream reader for the new TCP connection.
        writer: Stream writer for the new TCP connection.
        state: Shared state for the real-loopback stream server.
    """
    task = asyncio.create_task(_serve_sigterm_websocket(reader, writer, state))
    state.connection_tasks.add(task)
    task.add_done_callback(state.connection_tasks.discard)


async def _run_sigterm_subprocess_scenario(stop_during_authentication: bool) -> None:
    """Run the CLI against real loopback Alpaca streams and request SIGTERM.

    Args:
        stop_during_authentication: Whether to stop before news authentication completes.
    """
    state = _SigtermWebSocketState(stop_during_authentication)
    server = await asyncio.start_server(
        partial(_accept_sigterm_connection, state=state),
        "127.0.0.1",
        0,
    )
    process: asyncio.subprocess.Process | None = None
    try:
        assert server.sockets
        port = server.sockets[0].getsockname()[1]
        environment = os.environ.copy()
        environment.update(
            {
                "ALPACA_API_KEY": DUMMY_ALPACA_API_KEY,
                "ALPACA_API_SECRET": DUMMY_ALPACA_API_SECRET,
                "ALPACA_NEWS_STREAM_URL": f"ws://127.0.0.1:{port}/v1beta1/news",
                "ALPACA_STOCK_STREAM_URL": f"ws://127.0.0.1:{port}/v2",
                "DISCORD_ANALYST_WEBHOOKS": "",
                "DISCORD_EARNINGS_WEBHOOKS": "",
                "DISCORD_NEWS_WEBHOOKS": "",
                "PYTHONPATH": os.pathsep.join((str(REPOSITORY_ROOT / "src"), environment.get("PYTHONPATH", ""))),
            }
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "stocknews",
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if stop_during_authentication:
            await asyncio.wait_for(state.both_authenticated.wait(), timeout=5)
        else:
            await asyncio.wait_for(state.both_subscribed.wait(), timeout=5)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        await asyncio.wait_for(state.both_disconnected.wait(), timeout=2)
        assert process.returncode == 0, stderr.decode()
        logs = stdout.decode()
        records = [json.loads(line) for line in logs.splitlines()]
        assert any(record["msg"] == "starting stocknews" for record in records)
        assert any(record["msg"] == "shutdown requested" for record in records)
        assert DUMMY_ALPACA_API_KEY not in logs
        assert DUMMY_ALPACA_API_SECRET not in logs
        if stop_during_authentication:
            assert "news" not in state.subscribed_streams
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        server.close()
        await server.wait_closed()
        if state.connection_tasks:
            await asyncio.gather(*state.connection_tasks, return_exceptions=True)


def test_alpaca_settings_repr_omits_credentials() -> None:
    """Verify Alpaca settings representations do not expose credentials."""
    settings = AlpacaSettings(
        api_key=DUMMY_REPR_API_KEY,
        api_secret=DUMMY_REPR_API_SECRET,
        news_stream_url="ws://news.test/v1beta1/news",
        stock_stream_url="wss://stocks.test/v2",
    )

    representation = repr(settings)

    assert DUMMY_REPR_API_KEY not in representation
    assert DUMMY_REPR_API_SECRET not in representation
    assert "ws://news.test/v1beta1/news" in representation
    assert "wss://stocks.test/v2" in representation


@pytest.mark.parametrize(
    ("git_sha", "expected_commit"),
    [
        ("0123456789abcdef0123456789abcdef01234567", "0123456789abcdef0123456789abcdef01234567"),
        (None, "unknown"),
        ("", "unknown"),
    ],
    ids=["deployed-commit", "unset-commit", "empty-commit"],
)
def test_startup_logs_commit_before_missing_configuration(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    git_sha: str | None,
    expected_commit: str,
) -> None:
    """Verify startup logs the commit before reporting missing configuration."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    if git_sha is None:
        monkeypatch.delenv("GIT_SHA", raising=False)
    else:
        monkeypatch.setenv("GIT_SHA", git_sha)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == EXPECTED_CONFIGURATION_ERROR_EXIT_CODE
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == EXPECTED_STARTUP_LOG_RECORD_COUNT
    assert records[0]["msg"] == "starting stocknews"
    assert records[0]["commit"] == expected_commit
    assert records[1]["level"] == "ERROR"
    assert records[1]["msg"] == "invalid configuration"
    assert records[1]["error"] == "ALPACA_API_KEY is required"


def test_cli_logs_sanitized_failure_without_secret_bearing_exception_chain(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify startup logs omit secrets and tracebacks from raw exception chains."""

    def fail_to_run(coroutine) -> None:
        """Raise a sanitized runner failure after closing its supplied coroutine."""
        coroutine.close()
        cause = RuntimeError(DUMMY_FAILURE_API_SECRET)
        error_message = "sanitized startup failure"
        raise RuntimeError(error_message) from cause

    monkeypatch.setattr("stocknews.__main__.asyncio.run", fail_to_run)

    with caplog.at_level(logging.ERROR, logger="stocknews"), pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 1
    failure_record = next(record for record in caplog.records if record.name == "stocknews")
    assert failure_record.getMessage() == "stocknews failed"
    assert failure_record.error == "sanitized startup failure"
    assert DUMMY_FAILURE_API_SECRET not in caplog.text
    assert "Traceback" not in caplog.text


def test_cancel_and_wait_preserves_mixed_base_exception_group() -> None:
    """Verify cancellation waits preserve all children of a mixed exception group."""

    async def scenario() -> None:
        ordinary_error = ValueError("startup task failed")
        mixed_errors: list[BaseExceptionGroup] = []
        task_started = asyncio.Event()

        async def fail_with_mixed_group_when_cancelled() -> None:
            task_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as cancellation_error:
                mixed_error = BaseExceptionGroup(
                    "mixed startup failure",
                    [ordinary_error, cancellation_error],
                )
                mixed_errors.append(mixed_error)
                raise mixed_error from cancellation_error

        task = asyncio.create_task(fail_with_mixed_group_when_cancelled())
        await task_started.wait()

        with pytest.raises(BaseExceptionGroup) as error:
            await _cancel_and_wait(task)

        assert len(mixed_errors) == 1
        assert error.value is mixed_errors[0]
        assert error.value.exceptions[0] is ordinary_error
        assert isinstance(error.value.exceptions[1], asyncio.CancelledError)
        assert task.exception() is mixed_errors[0]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("stock_completion", "expect_trade"),
    [
        ("completed-with-queued-trade", True),
        ("pending-empty", False),
        ("pending-with-queued-trade", True),
    ],
    ids=["precompleted-buffer", "normal-eof-while-reading", "queued-event-races-eof"],
)
def test_run_application_drains_queued_news_when_stock_stream_reaches_eof(
    stock_completion: str,
    expect_trade: bool,
) -> None:
    """Verify stock EOF drains queued news and any final trade."""
    asyncio.run(_run_stock_eof_scenario(stock_completion, expect_trade))


def test_run_application_shuts_down_on_termination_immediately_after_subscription() -> None:
    """Verify immediate stream termination shuts down without exposing credentials."""

    async def scenario() -> None:
        disconnected = asyncio.Event()

        async def alpaca_app(scope, receive, send) -> None:
            assert scope["path"] == "/v1beta1/news"
            await receive()
            await send({"type": "websocket.accept"})

            async def send_json(payload: object) -> None:
                await send({"type": "websocket.send", "text": json.dumps(payload)})

            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    disconnected.set()
                    return

                request = json.loads(message["text"])
                if request["action"] == "auth":
                    await send_json({"T": "success", "msg": "authenticated"})
                else:
                    await send_json(
                        [
                            {"T": "subscription", "news": ["*"]},
                            {"T": "error", "code": 403, "msg": f"rejected {DUMMY_ALPACA_API_SECRET}"},
                        ]
                    )

        config = Config(
            alpaca_api_key=DUMMY_ALPACA_API_KEY,
            alpaca_api_secret=DUMMY_ALPACA_API_SECRET,
            alpaca_news_stream_url="ws://news.test/v1beta1/news",
            alpaca_stock_stream_url="wss://stocks.test/v2",
            discord_analyst_webhooks=(),
            discord_earnings_webhooks=(),
            discord_news_webhooks=(),
            stock_logo="https://example.test/%s.webp",
            transparent_png="https://example.test/transparent.png",
            blocked_phrases=(),
        )
        stop_event = asyncio.Event()
        logger = logging.getLogger("startup-terminal-test")

        async with (
            ASGIWebSocketTransport(alpaca_app) as websocket_transport,
            httpx2.AsyncClient(mounts={"ws://news.test": websocket_transport}, timeout=10) as client,
        ):
            with pytest.raises(RuntimeError, match="alpaca news stream terminated") as error:
                await run_application(config, client, logger, stop_event, stock_starter=None)

        assert DUMMY_ALPACA_API_SECRET not in str(error.value)
        assert stop_event.is_set()
        assert disconnected.is_set()

    asyncio.run(scenario())


def test_run_application_fails_on_terminated_stock_handle_while_news_is_open() -> None:
    """Verify a terminated stock handle closes the active news stream."""
    asyncio.run(_run_terminated_stock_scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    "stop_during_authentication",
    [False, True],
    ids=["after-subscription", "during-authentication"],
)
def test_sigterm_stops_real_news_and_trade_streams_cleanly(stop_during_authentication: bool) -> None:
    """Verify SIGTERM stops both streams during authentication or after subscription."""
    asyncio.run(_run_sigterm_subprocess_scenario(stop_during_authentication))


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_cli_reports_initial_alpaca_connection_failure_with_exit_one() -> None:
    """Verify an initial Alpaca connection failure exits with status one."""

    async def scenario() -> None:
        connection_tasks: set[asyncio.Task[None]] = set()

        async def reject_upgrade(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        def accept_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.create_task(reject_upgrade(reader, writer))
            connection_tasks.add(task)
            task.add_done_callback(connection_tasks.discard)

        server = await asyncio.start_server(accept_connection, "127.0.0.1", 0)
        process: asyncio.subprocess.Process | None = None
        try:
            assert server.sockets
            port = server.sockets[0].getsockname()[1]
            environment = os.environ.copy()
            environment.update(
                {
                    "ALPACA_API_KEY": DUMMY_FAILURE_API_KEY,
                    "ALPACA_API_SECRET": DUMMY_FAILURE_API_SECRET,
                    "ALPACA_NEWS_STREAM_URL": f"ws://127.0.0.1:{port}/v1beta1/news",
                    "ALPACA_STOCK_STREAM_URL": f"ws://127.0.0.1:{port}/v2",
                    "DISCORD_ANALYST_WEBHOOKS": "",
                    "DISCORD_EARNINGS_WEBHOOKS": "",
                    "DISCORD_NEWS_WEBHOOKS": "",
                    "PYTHONPATH": os.pathsep.join((str(REPOSITORY_ROOT / "src"), environment.get("PYTHONPATH", ""))),
                }
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "stocknews",
                cwd=REPOSITORY_ROOT,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            assert process.returncode == 1
            logs = stdout.decode()
            records = [json.loads(line) for line in logs.splitlines()]
            failure = next(record for record in records if record["msg"] == "stocknews failed")
            assert "connect Alpaca news stream" in failure["error"]
            assert DUMMY_FAILURE_API_KEY not in logs
            assert DUMMY_FAILURE_API_SECRET not in logs
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            server.close()
            await server.wait_closed()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)

    asyncio.run(scenario())
