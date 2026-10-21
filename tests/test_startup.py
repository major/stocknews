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
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport

from stocknews.__main__ import _cancel_and_wait, _stream_events, main, run_application
from stocknews.alpaca import AlpacaStreamError, StreamHandle
from stocknews.models import AlpacaSettings, Config, NewsItem

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Iterator

EXPECTED_CONFIGURATION_ERROR_EXIT_CODE = 2
EXPECTED_STARTUP_LOG_RECORD_COUNT = 2
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
STARTUP_TEST_LOGGER_NAMES = ("startup-terminal-test",)


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


@dataclass
class _SigtermWebSocketState:
    """Hold events and connection tasks for the real SIGTERM news WebSocket server."""

    stop_during_authentication: bool
    authenticated: asyncio.Event = field(default_factory=asyncio.Event)
    subscribed: asyncio.Event = field(default_factory=asyncio.Event)
    disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    connection_tasks: set[asyncio.Task[None]] = field(default_factory=set)


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
    opcode: int,
    payload: bytes,
) -> bool:
    """Acknowledge close and ping frames, returning whether the peer closed.

    Args:
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback news server.
        opcode: Received WebSocket frame opcode.
        payload: Received WebSocket frame payload.

    Returns:
        Whether a close frame ended the connection.
    """
    if opcode == WEBSOCKET_CLOSE_FRAME_OPCODE:
        await _send_websocket_frame(writer, WEBSOCKET_CLOSE_FRAME_OPCODE, payload[:125])
        state.disconnected.set()
        return True
    if opcode == WEBSOCKET_PING_FRAME_OPCODE:
        await _send_websocket_frame(writer, WEBSOCKET_PONG_FRAME_OPCODE, payload)
    return False


async def _authenticate_sigterm_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
) -> bool:
    """Acknowledge news authentication or wait for SIGTERM during authentication.

    Args:
        reader: Stream reader for the WebSocket connection.
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback news server.

    Returns:
        Whether a close frame ended the connection while news auth was pending.
    """
    state.authenticated.set()
    if state.stop_during_authentication:
        while True:
            opcode, payload = await _receive_websocket_frame(reader)
            if await _handle_websocket_control_frame(writer, state, opcode, payload):
                return True
    await _send_websocket_json(writer, {"T": "success", "msg": "authenticated"})
    return False


async def _subscribe_sigterm_stream(writer: asyncio.StreamWriter, state: _SigtermWebSocketState) -> None:
    """Send the news subscription response and record the subscription.

    Args:
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback news server.
    """
    await _send_websocket_json(writer, {"T": "subscription", "news": ["*"]})
    state.subscribed.set()


async def _handle_sigterm_stream_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
    payload: bytes,
) -> bool:
    """Handle one Alpaca news authentication or subscription request.

    Args:
        reader: Stream reader for the WebSocket connection.
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback news server.
        payload: JSON request bytes from the WebSocket text frame.

    Returns:
        Whether a close frame ended the connection during authentication.
    """
    request = json.loads(payload)
    if request["action"] == "auth":
        return await _authenticate_sigterm_stream(reader, writer, state)
    await _subscribe_sigterm_stream(writer, state)
    return False


async def _serve_sigterm_websocket_session(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
) -> None:
    """Serve authentication and subscription messages on the Alpaca news stream.

    Args:
        reader: Stream reader for the WebSocket connection.
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback news server.
    """
    while True:
        opcode, payload = await _receive_websocket_frame(reader)
        if await _handle_websocket_control_frame(writer, state, opcode, payload):
            return
        if opcode != WEBSOCKET_TEXT_FRAME_OPCODE:
            continue
        if await _handle_sigterm_stream_request(reader, writer, state, payload):
            return


async def _serve_sigterm_websocket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
) -> None:
    """Upgrade and serve one real-loopback Alpaca news WebSocket connection.

    Args:
        reader: Stream reader for the new TCP connection.
        writer: Stream writer for the new TCP connection.
        state: Shared state for the real-loopback news server.
    """
    try:
        path = await _read_websocket_request_path(reader)
        assert path.endswith("/v1beta1/news")
        key = await _read_websocket_key(reader)
        await _complete_websocket_upgrade(writer, key)
        await _serve_sigterm_websocket_session(reader, writer, state)
    except asyncio.IncompleteReadError, ConnectionError:
        state.disconnected.set()
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
    """Run the CLI against a real loopback Alpaca news stream and request SIGTERM.

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
            await asyncio.wait_for(state.authenticated.wait(), timeout=5)
        else:
            await asyncio.wait_for(state.subscribed.wait(), timeout=5)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        await asyncio.wait_for(state.disconnected.wait(), timeout=2)
        assert process.returncode == 0, stderr.decode()
        logs = stdout.decode()
        records = [json.loads(line) for line in logs.splitlines()]
        assert any(record["msg"] == "starting stocknews" for record in records)
        assert any(record["msg"] == "shutdown requested" for record in records)
        assert DUMMY_ALPACA_API_KEY not in logs
        assert DUMMY_ALPACA_API_SECRET not in logs
        if stop_during_authentication:
            assert not state.subscribed.is_set()
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
    )

    representation = repr(settings)

    assert DUMMY_REPR_API_KEY not in representation
    assert DUMMY_REPR_API_SECRET not in representation
    assert "ws://news.test/v1beta1/news" in representation


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


class _ObservedNewsQueue(asyncio.Queue[NewsItem]):
    """Signal when the news reader starts waiting for an item."""

    def __init__(self) -> None:
        """Initialize the queue and its consumer-waiting event."""
        super().__init__()
        self.consumer_waiting = asyncio.Event()

    async def get(self) -> NewsItem:
        """Mark the consumer as waiting before reading the next news item."""
        self.consumer_waiting.set()
        return await super().get()


def _news_item(headline: str) -> NewsItem:
    """Build a Benzinga-style news item with the given headline.

    Args:
        headline: Headline text for the news item.

    Returns:
        A news item for the stream reader tests.
    """
    return NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline=headline)


async def _completed_news_producer() -> None:
    """Return immediately for a news task representing a finished producer."""


async def _wait_for_news_producer_eof(finish_producer: asyncio.Event) -> None:
    """Keep a pending news producer open until its test releases it.

    Args:
        finish_producer: Event that completes the producer task.
    """
    await finish_producer.wait()


async def _collect_stream_events(
    handle: StreamHandle[NewsItem],
    terminated: asyncio.Future[AlpacaStreamError],
) -> list[NewsItem]:
    """Collect every item yielded by the news stream reader.

    Args:
        handle: News stream handle whose queue and task are read.
        terminated: Future that reports stream termination.

    Returns:
        Every news item yielded before the reader ends.
    """
    events: AsyncIterable[NewsItem] = _stream_events(handle, terminated)
    return [item async for item in events]


async def _run_news_stream_eof_scenario(producer_timing: str, buffered_headlines: tuple[str, ...]) -> list[NewsItem]:
    """Run the news stream reader against one producer-completion timing.

    Args:
        producer_timing: When the producer completes relative to the reader.
        buffered_headlines: Headlines queued before the reader starts.

    Returns:
        Every news item the reader yields before it ends.
    """
    terminated: asyncio.Future[AlpacaStreamError] = asyncio.get_running_loop().create_future()
    events = _ObservedNewsQueue()
    for headline in buffered_headlines:
        events.put_nowait(_news_item(headline))

    if producer_timing == "completed-with-buffered-events":
        task = asyncio.create_task(_completed_news_producer())
        await task
        handle = StreamHandle(events=events, task=task)
        return await asyncio.wait_for(_collect_stream_events(handle, terminated), timeout=2)

    finish_producer = asyncio.Event()
    task = asyncio.create_task(_wait_for_news_producer_eof(finish_producer))
    handle = StreamHandle(events=events, task=task)
    consumer = asyncio.create_task(_collect_stream_events(handle, terminated))
    try:
        await asyncio.wait_for(events.consumer_waiting.wait(), timeout=2)
        if producer_timing == "pending-with-queued-event":
            # Queue a final item after the reader's completion callback is registered.
            task.add_done_callback(lambda _task: events.put_nowait(_news_item("Final headline")))
        finish_producer.set()
        return await asyncio.wait_for(consumer, timeout=2)
    finally:
        finish_producer.set()
        consumer.cancel()
        await asyncio.gather(consumer, task, return_exceptions=True)


@pytest.mark.parametrize(
    ("producer_timing", "buffered_headlines", "expected_headlines"),
    [
        (
            "completed-with-buffered-events",
            ("First headline", "Second headline"),
            ("First headline", "Second headline"),
        ),
        ("pending-empty", (), ()),
        ("pending-with-queued-event", (), ("Final headline",)),
    ],
    ids=["precompleted-buffer", "normal-eof-while-reading", "queued-event-races-eof"],
)
def test_stream_events_yields_news_until_producer_completes(
    producer_timing: str,
    buffered_headlines: tuple[str, ...],
    expected_headlines: tuple[str, ...],
) -> None:
    """Verify the news reader yields buffered and final items before it ends."""
    received = asyncio.run(_run_news_stream_eof_scenario(producer_timing, buffered_headlines))

    assert [item.headline for item in received] == list(expected_headlines)


def test_stream_events_raises_termination_seen_before_next_read() -> None:
    """Verify a termination recorded before the next read raises instead of yielding queued news."""

    async def scenario() -> None:
        terminated: asyncio.Future[AlpacaStreamError] = asyncio.get_running_loop().create_future()
        terminated.set_result(AlpacaStreamError("Alpaca news stream failed"))
        events: asyncio.Queue[NewsItem] = asyncio.Queue()
        events.put_nowait(_news_item("Queued headline"))
        task = asyncio.create_task(_completed_news_producer())
        await task
        handle = StreamHandle(events=events, task=task)

        with pytest.raises(AlpacaStreamError, match="Alpaca news stream failed"):
            await asyncio.wait_for(_collect_stream_events(handle, terminated), timeout=2)
        assert events.qsize() == 1

    asyncio.run(scenario())


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
                await run_application(config, client, logger, stop_event)

        assert DUMMY_ALPACA_API_SECRET not in str(error.value)
        assert stop_event.is_set()
        assert disconnected.is_set()

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    "stop_during_authentication",
    [False, True],
    ids=["after-subscription", "during-authentication"],
)
def test_sigterm_stops_real_news_stream_cleanly(stop_during_authentication: bool) -> None:
    """Verify SIGTERM stops the news stream during authentication or after subscription."""
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
