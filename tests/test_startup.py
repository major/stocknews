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
from stocknews.config import load_config
from stocknews.models import AlpacaSettings, NewsItem

if TYPE_CHECKING:
    from collections.abc import Iterator

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


class _ObservedNewsQueue(asyncio.Queue[NewsItem]):
    """Signal when the news reader starts waiting for an item."""

    def __init__(self, maxsize: int) -> None:
        """Initialize the bounded queue and its consumer-waiting event."""
        super().__init__(maxsize=maxsize)
        self.consumer_waiting = asyncio.Event()

    async def get(self) -> NewsItem:
        """Mark the consumer as waiting before reading the next news item."""
        if self.empty():
            self.consumer_waiting.set()
        return await super().get()


@dataclass
class _SigtermWebSocketState:
    """Hold events and connection tasks for the real SIGTERM news server."""

    stop_during_authentication: bool
    attempted_paths: list[str] = field(default_factory=list)
    subscribed: asyncio.Event = field(default_factory=asyncio.Event)
    authenticated: asyncio.Event = field(default_factory=asyncio.Event)
    disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    connection_tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def note_disconnected(self) -> None:
        """Record that the news WebSocket disconnected."""
        self.disconnected.set()


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
        state.note_disconnected()
        return True
    if opcode == WEBSOCKET_PING_FRAME_OPCODE:
        await _send_websocket_frame(writer, WEBSOCKET_PONG_FRAME_OPCODE, payload)
    return False


async def _authenticate_sigterm_news(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
) -> bool:
    """Acknowledge news authentication or wait for SIGTERM during auth.

    Args:
        reader: Stream reader for the WebSocket connection.
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback news server.

    Returns:
        Whether a close frame ended the connection while authentication was pending.
    """
    state.authenticated.set()
    if state.stop_during_authentication:
        while True:
            opcode, payload = await _receive_websocket_frame(reader)
            if await _handle_websocket_control_frame(writer, state, opcode, payload):
                return True
    await _send_websocket_json(writer, {"T": "success", "msg": "authenticated"})
    return False


async def _subscribe_sigterm_news(writer: asyncio.StreamWriter, state: _SigtermWebSocketState) -> None:
    """Send and record the news subscription response.

    Args:
        writer: Stream writer for the WebSocket connection.
        state: Shared state for the real-loopback news server.
    """
    await _send_websocket_json(writer, {"T": "subscription", "news": ["*"]})
    state.subscribed.set()


async def _handle_sigterm_news_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
    payload: bytes,
) -> bool:
    """Handle one news authentication or subscription request.

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
        return await _authenticate_sigterm_news(reader, writer, state)
    await _subscribe_sigterm_news(writer, state)
    return False


async def _serve_sigterm_news_session(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
) -> None:
    """Serve authentication and subscription messages on the news stream.

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
        if await _handle_sigterm_news_request(reader, writer, state, payload):
            return


async def _serve_sigterm_websocket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: _SigtermWebSocketState,
) -> None:
    """Upgrade and serve the configured news WebSocket connection.

    Args:
        reader: Stream reader for the new TCP connection.
        writer: Stream writer for the new TCP connection.
        state: Shared state for the real-loopback news server.
    """
    path = ""
    try:
        path = await _read_websocket_request_path(reader)
        state.attempted_paths.append(path)
        if path != "/v1beta1/news":
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        key = await _read_websocket_key(reader)
        await _complete_websocket_upgrade(writer, key)
        await _serve_sigterm_news_session(reader, writer, state)
    except asyncio.IncompleteReadError, ConnectionError:
        if path == "/v1beta1/news":
            state.note_disconnected()
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
    """Run the CLI against the real loopback news stream and request SIGTERM.

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
        assert state.attempted_paths == ["/v1beta1/news"]
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
        ("0123456789abcdef0123456789abcdef01234567", "0123456"),
        ("abc1234", "abc1234"),
        (None, "unknown"),
        ("", "unknown"),
    ],
    ids=["deployed-commit", "short-commit", "unset-commit", "empty-commit"],
)
@pytest.mark.parametrize(
    "build_date",
    ["2026-10-22T16:09:06Z", None, ""],
    ids=["provided-build-date", "unset-build-date", "empty-build-date"],
)
def test_startup_logs_commit_before_missing_configuration(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    git_sha: str | None,
    expected_commit: str,
    build_date: str | None,
) -> None:
    """Verify startup logs the commit before reporting missing configuration."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    if git_sha is None:
        monkeypatch.delenv("GIT_SHA", raising=False)
    else:
        monkeypatch.setenv("GIT_SHA", git_sha)
    if build_date is None:
        monkeypatch.delenv("BUILD_DATE", raising=False)
    else:
        monkeypatch.setenv("BUILD_DATE", build_date)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == EXPECTED_CONFIGURATION_ERROR_EXIT_CODE
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == EXPECTED_STARTUP_LOG_RECORD_COUNT
    assert records[0]["msg"] == "starting stocknews"
    assert records[0]["commit"] == expected_commit
    assert records[0]["build_date"] == (build_date or "unknown")
    assert records[0]["version"] == "dev"
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


def test_cancel_and_wait_settles_task_with_error_during_cancellation() -> None:
    """Verify cancellation cleanup returns after an ordinary task failure."""

    async def scenario() -> None:
        """Cancel and settle a task that raises while handling cancellation."""
        task_baseline = asyncio.all_tasks()
        task_started = asyncio.Event()
        failure = RuntimeError("failure while cleaning up")

        async def fail_during_cancellation() -> None:
            """Raise the saved failure when cancellation enters cleanup."""
            try:
                task_started.set()
                await asyncio.Event().wait()
            finally:
                raise failure

        task = asyncio.create_task(fail_during_cancellation())
        await task_started.wait()

        await _cancel_and_wait(task)

        assert task.done()
        assert task.exception() is failure
        pending_tasks = asyncio.all_tasks() - task_baseline
        assert not pending_tasks, f"pending tasks after cancellation cleanup: {pending_tasks!r}"

    asyncio.run(scenario())


class _StreamEventsEofScenario:
    """Coordinate queue and task timing for news-stream EOF cases."""

    def __init__(self, completion_case: str) -> None:
        """Create the bounded event queue and stream completion signals."""
        self.completion_case = completion_case
        self.events = _ObservedNewsQueue(maxsize=1)
        self.terminated = asyncio.get_running_loop().create_future()
        self.finish_stream = asyncio.Event()
        self.news_item = NewsItem(
            symbols=("AAPL",),
            author="Benzinga Newsdesk",
            headline="Apple launches a phone",
        )

    async def stream_task(self) -> None:
        """Keep the stream open until the test releases its completion."""
        if self.completion_case != "completed-with-buffered-news":
            await self.finish_stream.wait()

    def queue_final_news(self, task: asyncio.Task[None]) -> None:
        """Queue the final item after stream completion but before the reader resumes."""
        assert task.done()
        self.events.put_nowait(self.news_item)

    async def collect_news(self, task: asyncio.Task[None]) -> list[NewsItem]:
        """Collect news events until the stream task reports EOF."""
        stream = StreamHandle(events=self.events, task=task)
        return [event async for event in _stream_events(stream, self.terminated)]

    async def run(self) -> None:
        """Run the selected completion ordering and verify cleanup."""
        task_baseline = asyncio.all_tasks()
        if self.completion_case == "completed-with-buffered-news":
            self.events.put_nowait(self.news_item)

        task = asyncio.create_task(self.stream_task())
        if self.completion_case == "completed-with-buffered-news":
            await task
        consumer = asyncio.create_task(self.collect_news(task))
        try:
            if self.completion_case != "completed-with-buffered-news":
                await asyncio.wait_for(self.events.consumer_waiting.wait(), timeout=2)
                if self.completion_case == "final-news-races-completion":
                    # Register after the reader's completion callback to keep its queue get pending.
                    task.add_done_callback(self.queue_final_news)
                self.finish_stream.set()

            received = await asyncio.wait_for(consumer, timeout=2)
            await asyncio.wait_for(task, timeout=2)

            expected_news = [] if self.completion_case == "pending-with-empty-queue" else [self.news_item]
            assert self.events.maxsize == 1
            assert received == expected_news
            assert self.events.empty()
            assert not self.terminated.done()
            pending_tasks = asyncio.all_tasks() - task_baseline
            assert not pending_tasks, f"pending tasks after news EOF: {pending_tasks!r}"
        finally:
            self.finish_stream.set()
            for pending_task in (consumer, task):
                if not pending_task.done():
                    pending_task.cancel()
            await asyncio.gather(consumer, task, return_exceptions=True)


@pytest.mark.parametrize(
    "completion_case",
    ["completed-with-buffered-news", "pending-with-empty-queue", "final-news-races-completion"],
    ids=["precompleted-buffer", "eof-while-consumer-waits", "final-item-races-eof"],
)
def test_stream_events_delivers_buffered_news_before_eof(completion_case: str) -> None:
    """Verify queued news is delivered before EOF across stream-task races."""

    async def scenario() -> None:
        """Run the stream EOF case on an active event loop."""
        await _StreamEventsEofScenario(completion_case).run()

    asyncio.run(scenario())


def test_stream_events_propagates_preexisting_termination_before_queued_news() -> None:
    """Verify an existing stream error takes priority over queued news."""

    async def scenario() -> None:
        """Propagate a terminal stream error and clean up the backing task."""
        task_baseline = asyncio.all_tasks()
        events: asyncio.Queue[NewsItem] = asyncio.Queue(maxsize=1)
        news_item = NewsItem(
            symbols=("AAPL",),
            author="Benzinga Newsdesk",
            headline="Apple launches a phone",
        )
        events.put_nowait(news_item)
        task_started = asyncio.Event()

        async def wait_for_cancellation() -> None:
            """Keep the stream task pending until the test cancels it."""
            task_started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(wait_for_cancellation())
        await task_started.wait()
        terminated: asyncio.Future[AlpacaStreamError] = asyncio.get_running_loop().create_future()
        terminal_error = AlpacaStreamError("sanitized news stream failure")
        terminated.set_result(terminal_error)
        stream = _stream_events(StreamHandle(events=events, task=task), terminated)

        try:
            with pytest.raises(AlpacaStreamError) as error:
                await anext(stream)

            assert error.value is terminal_error
            assert events.get_nowait() is news_item
            assert events.empty()
        finally:
            await stream.aclose()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert task.done()
        assert task.cancelled()
        pending_tasks = asyncio.all_tasks() - task_baseline
        assert not pending_tasks, f"pending tasks after terminated stream: {pending_tasks!r}"

    asyncio.run(scenario())


def test_run_application_shuts_down_on_termination_immediately_after_subscription() -> None:
    """Verify the application starts only news and safely shuts down on termination."""

    async def scenario() -> None:
        """Exercise normal startup with a legacy stock URL and terminal news error."""
        attempted_paths: list[str] = []
        requests: list[dict[str, object]] = []
        disconnected = asyncio.Event()

        async def alpaca_app(scope, receive, send) -> None:
            """Record WebSocket requests and return a terminal news error."""
            attempted_paths.append(scope["path"])
            await receive()
            await send({"type": "websocket.accept"})

            if scope["path"] != "/v1beta1/news":
                await send({"type": "websocket.close", "code": 1008})
                return

            async def send_json(payload: object) -> None:
                """Send one JSON protocol message to the WebSocket client."""
                await send({"type": "websocket.send", "text": json.dumps(payload)})

            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    disconnected.set()
                    return

                request = json.loads(message["text"])
                requests.append(request)
                if request["action"] == "auth":
                    await send_json({"T": "success", "msg": "authenticated"})
                else:
                    await send_json(
                        [
                            {"T": "subscription", "news": ["*"]},
                            {"T": "error", "code": 403, "msg": f"rejected {DUMMY_ALPACA_API_SECRET}"},
                        ]
                    )

        config = load_config(
            {
                "ALPACA_API_KEY": DUMMY_ALPACA_API_KEY,
                "ALPACA_API_SECRET": DUMMY_ALPACA_API_SECRET,
                "ALPACA_NEWS_STREAM_URL": "ws://news.test/v1beta1/news",
                "ALPACA_STOCK_STREAM_URL": "wss://stocks.test/v2",
                "DISCORD_ANALYST_WEBHOOKS": "",
                "DISCORD_EARNINGS_WEBHOOKS": "",
                "DISCORD_NEWS_WEBHOOKS": "",
            }
        )
        stop_event = asyncio.Event()
        logger = logging.getLogger("startup-terminal-test")

        async with (
            ASGIWebSocketTransport(alpaca_app) as websocket_transport,
            httpx2.AsyncClient(
                mounts={"ws://news.test": websocket_transport, "wss://stocks.test": websocket_transport},
                timeout=10,
            ) as client,
        ):
            task_baseline = asyncio.all_tasks()
            with pytest.raises(RuntimeError, match="alpaca news stream terminated") as error:
                await run_application(config, client, logger, stop_event)

        pending_tasks = asyncio.all_tasks() - task_baseline
        current_task = asyncio.current_task()
        if current_task is not None:
            pending_tasks.discard(current_task)
        assert not pending_tasks, f"pending tasks after news termination: {pending_tasks!r}"
        assert attempted_paths == ["/v1beta1/news"]
        assert requests == [
            {"action": "auth", "key": DUMMY_ALPACA_API_KEY, "secret": DUMMY_ALPACA_API_SECRET},
            {"action": "subscribe", "news": ["*"]},
        ]
        assert config.alpaca_news_stream_url == "ws://news.test/v1beta1/news"
        assert DUMMY_ALPACA_API_SECRET not in str(error.value)
        assert DUMMY_ALPACA_API_KEY not in str(error.value)
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
    """Verify SIGTERM stops the news stream during auth or after subscription."""
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
