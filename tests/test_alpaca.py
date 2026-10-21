"""Behavioral tests for Alpaca's WebSocket adapters."""

import asyncio
import json
import traceback
from collections.abc import Awaitable, Callable
from contextlib import suppress
from http import HTTPStatus
from typing import Self, cast

import httpcore2
import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport
from wsproto import ConnectionType, WSConnection
from wsproto.connection import ConnectionState
from wsproto.events import AcceptConnection, CloseConnection, Ping, TextMessage
from wsproto.frame_protocol import Opcode

from stocknews import alpaca
from stocknews.models import AlpacaSettings, NewsItem

EXPECTED_EVENT_QUEUE_CAPACITY = 256

# Fake credentials used by stream fixtures that do not inspect authentication.
TEST_API_KEY = "key"
TEST_API_CREDENTIAL = "secret"

# Fake credentials used when tests inspect authentication requests.
NEWS_TEST_API_KEY = "news-key"
NEWS_TEST_API_CREDENTIAL = "news-secret"

# Fake credentials deliberately included in failures to verify redaction.
REDACTION_TEST_API_KEY = "private-key"
REDACTION_TEST_API_CREDENTIAL = "private-secret"
AUTH_FAILURE_API_KEY = "demo-key"
AUTH_FAILURE_API_CREDENTIAL = "demo-secret"


def test_alpaca_settings_repr_excludes_credentials() -> None:
    """Keep credentials private while showing configured stream URLs."""
    settings = AlpacaSettings(
        api_key=REDACTION_TEST_API_KEY,
        api_secret=REDACTION_TEST_API_CREDENTIAL,
        news_stream_url="ws://news.test/v1beta1/news",
    )

    representation = repr(settings)

    assert REDACTION_TEST_API_KEY not in representation
    assert REDACTION_TEST_API_CREDENTIAL not in representation
    assert settings.news_stream_url in representation


@pytest.mark.parametrize("wrapped_subscription", [False, True], ids=["flat-ack", "wrapped-ack"])
def test_news_stream_authenticates_subscribes_and_decodes_events(wrapped_subscription: bool) -> None:
    """Authenticate to the news stream, subscribe, and decode the delivered article."""

    async def scenario() -> None:
        requests: list[dict[str, object]] = []

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            assert scope["path"] == "/v1beta1/news"
            await receive()
            await send({"type": "websocket.accept"})
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    return
                message = json.loads(incoming["text"])
                requests.append(message)
                if message["action"] == "auth":
                    await _send_json(send, [{"T": "success", "msg": "authenticated"}])
                else:
                    subscription: dict[str, object] = {"T": "subscription"}
                    if wrapped_subscription:
                        subscription["streams"] = {"news": ["*"]}
                    else:
                        subscription["news"] = ["*"]
                    await _send_json(send, [subscription])
                    await _send_json(
                        send,
                        [
                            {"T": "success", "msg": "connected"},
                            {
                                "T": "n",
                                "symbols": ["AAPL", "MSFT"],
                                "author": "Benzinga Newsdesk",
                                "headline": "A headline",
                                "summary": "A summary",
                                "url": "https://example.test/news",
                            },
                        ],
                    )

        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=NEWS_TEST_API_KEY,
                    api_secret=NEWS_TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=stop_event,
            )
            item = await asyncio.wait_for(stream.events.get(), timeout=2)
            assert stream.events.maxsize == EXPECTED_EVENT_QUEUE_CAPACITY
            assert item == NewsItem(
                symbols=("AAPL", "MSFT"),
                author="Benzinga Newsdesk",
                headline="A headline",
                summary="A summary",
                url="https://example.test/news",
            )
            assert requests == [
                {"action": "auth", "key": NEWS_TEST_API_KEY, "secret": NEWS_TEST_API_CREDENTIAL},
                {"action": "subscribe", "news": ["*"]},
            ]
            stop_event.set()
            await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response", "diagnostic"),
    [
        (
            {
                "T": "error",
                "code": 401,
                "msg": f"invalid {AUTH_FAILURE_API_KEY} and {AUTH_FAILURE_API_CREDENTIAL}",
            },
            "401",
        ),
        ({"T": "error", "msg": None, "code": True}, "server rejected the request"),
    ],
    ids=["provider-error-redacts-credentials", "missing-error-details-use-safe-fallback"],
)
def test_initial_news_auth_failure_is_raised_without_exposing_credentials(
    response: dict[str, object], diagnostic: str
) -> None:
    """Keep credentials out of errors and tracebacks when initial news authentication fails.

    Args:
        response: Authentication response sent by the provider.
        diagnostic: Expected safe detail in the resulting error.
    """

    async def scenario() -> None:
        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            await receive()
            await send({"type": "websocket.accept"})
            await receive()
            await _send_json(send, response)

        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(alpaca.AlpacaStreamError) as error:
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=AUTH_FAILURE_API_KEY,
                        api_secret=AUTH_FAILURE_API_CREDENTIAL,
                        news_stream_url="ws://alpaca.test/v1beta1/news",
                    ),
                    stop_event=stop_event,
                )
        assert "authentication failed" in str(error.value)
        assert diagnostic in str(error.value)
        assert AUTH_FAILURE_API_KEY not in str(error.value)
        assert AUTH_FAILURE_API_CREDENTIAL not in str(error.value)
        formatted = "".join(traceback.format_exception(error.value))
        assert AUTH_FAILURE_API_KEY not in formatted
        assert AUTH_FAILURE_API_CREDENTIAL not in formatted

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(f"{REDACTION_TEST_API_KEY} {REDACTION_TEST_API_CREDENTIAL}"),
        httpx2.ConnectTimeout(f"{REDACTION_TEST_API_KEY} {REDACTION_TEST_API_CREDENTIAL}"),
    ],
    ids=["connection-refused", "connection-timeout"],
)
def test_initial_transport_failure_is_clear_and_does_not_leak_tasks_or_credentials(
    failure: httpx2.TransportError,
) -> None:
    """Report initial transport failures without leaking credentials or leaving tasks behind.

    Args:
        failure: Transport error raised before the WebSocket handshake.
    """

    async def scenario() -> None:
        async def unreachable_app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            message = "the failing transport must reject the connection before the ASGI app runs"
            raise AssertionError(message)

        class FailingTransport(ASGIWebSocketTransport):
            async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
                raise failure

        async with FailingTransport(unreachable_app) as transport, httpx2.AsyncClient(transport=transport) as client:
            # Include tasks that belong to the already-open transport and client.
            # Any pending task created by the Alpaca startup attempt is owned by it.
            tasks_before_startup = asyncio.all_tasks()
            with pytest.raises(alpaca.AlpacaStreamError, match="initial connection failed") as error:
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=REDACTION_TEST_API_KEY,
                        api_secret=REDACTION_TEST_API_CREDENTIAL,
                        news_stream_url="ws://alpaca.test/v1beta1/news",
                    ),
                    stop_event=asyncio.Event(),
                )
            new_pending_tasks = asyncio.all_tasks() - tasks_before_startup
            current_task = asyncio.current_task()
            if current_task is not None:
                new_pending_tasks.discard(current_task)
            assert not new_pending_tasks, f"pending tasks after connection failure: {new_pending_tasks!r}"

        assert "network transport error" in str(error.value)
        assert REDACTION_TEST_API_KEY not in str(error.value)
        assert REDACTION_TEST_API_CREDENTIAL not in str(error.value)
        formatted = "".join(traceback.format_exception(error.value))
        assert REDACTION_TEST_API_KEY not in formatted
        assert REDACTION_TEST_API_CREDENTIAL not in formatted

    asyncio.run(scenario())


def test_initial_authentication_timeout_is_reported_without_leaking_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report an authentication timeout without leaving startup tasks behind."""

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "ACK_TIMEOUT_SECONDS", 0)

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            await receive()
            await send({"type": "websocket.accept"})
            while True:
                if (await receive())["type"] == "websocket.disconnect":
                    return

        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            # The open HTTPX2 and ASGI transport contexts are part of the baseline.
            # Startup must leave no new task behind, including the unnamed stop watcher.
            tasks_before_startup = asyncio.all_tasks()
            with pytest.raises(alpaca.AlpacaStreamError, match="timed out waiting for authentication"):
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url="ws://alpaca.test/v1beta1/news",
                    ),
                    stop_event=asyncio.Event(),
                )
            new_pending_tasks = asyncio.all_tasks() - tasks_before_startup
            current_task = asyncio.current_task()
            if current_task is not None:
                new_pending_tasks.discard(current_task)
            assert not new_pending_tasks, f"pending tasks after authentication timeout: {new_pending_tasks!r}"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response", "diagnostic"),
    [("{", "invalid JSON"), ('"unexpected scalar"', "invalid shape")],
    ids=["malformed-json", "invalid-message-shape"],
)
def test_invalid_handshake_messages_fail_startup(response: str, diagnostic: str) -> None:
    """Reject malformed or unsupported messages received during the handshake.

    Args:
        response: Invalid handshake message sent by the provider.
        diagnostic: Expected explanation in the startup error.
    """

    async def scenario() -> None:
        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            await receive()
            await send({"type": "websocket.accept"})
            await receive()
            await send({"type": "websocket.send", "text": response})

        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(alpaca.AlpacaStreamError, match=diagnostic):
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url="ws://alpaca.test/v1beta1/news",
                    ),
                    stop_event=asyncio.Event(),
                )

    asyncio.run(scenario())


def test_malformed_established_message_terminates_the_stream() -> None:
    """Report malformed JSON after subscription as a terminal stream error."""

    async def scenario() -> None:
        connection_count = 0

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            nonlocal connection_count
            connection_count += 1
            await receive()
            await send({"type": "websocket.accept"})
            await receive()
            await _send_json(send, {"T": "success", "msg": "authenticated"})
            await receive()
            await _send_json(send, {"T": "subscription", "news": ["*"]})
            await send({"type": "websocket.send", "text": "{"})
            await receive()

        terminal: list[alpaca.AlpacaStreamError] = []
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=TEST_API_KEY,
                    api_secret=TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=asyncio.Event(),
                on_terminated=terminal.append,
            )
            await asyncio.wait_for(stream.task, timeout=2)

        assert connection_count == 1
        assert len(terminal) == 1
        assert "invalid JSON" in str(terminal[0])

    asyncio.run(scenario())


def test_unexpected_binary_message_terminates_the_stream() -> None:
    """Report binary data on the news stream as a terminal stream error."""

    async def scenario() -> None:
        connection_count = 0

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            nonlocal connection_count
            connection_count += 1
            await receive()
            await send({"type": "websocket.accept"})
            await receive()
            await _send_json(send, {"T": "success", "msg": "authenticated"})
            await receive()
            await _send_json(send, {"T": "subscription", "news": ["*"]})
            await send({"type": "websocket.send", "bytes": b"\xff"})
            await receive()

        terminal: list[alpaca.AlpacaStreamError] = []
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=TEST_API_KEY,
                    api_secret=TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=asyncio.Event(),
                on_terminated=terminal.append,
            )
            await asyncio.wait_for(stream.task, timeout=2)

        assert connection_count == 1
        assert len(terminal) == 1
        assert isinstance(terminal[0], alpaca.AlpacaStreamError)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [("author", 17, "author"), ("symbols", "AAPL", "symbols")],
    ids=["author-type", "symbols-type"],
)
def test_invalid_news_field_terminates_established_stream(field: str, value: object, diagnostic: str) -> None:
    """Terminate the news stream when an article field has an invalid value.

    Args:
        field: News field to populate with an invalid value.
        value: Invalid field value sent by the provider.
        diagnostic: Expected field name in the terminal error.
    """

    async def scenario() -> None:
        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            await receive()
            await send({"type": "websocket.accept"})
            await receive()
            await _send_json(send, {"T": "success", "msg": "authenticated"})
            await receive()
            await _send_json(send, {"T": "subscription", "news": ["*"]})
            article: dict[str, object] = {
                "T": "n",
                "symbols": ["AAPL"],
                "author": "Desk",
                "headline": "News",
            }
            article[field] = value
            await _send_json(send, article)

        terminal: list[alpaca.AlpacaStreamError] = []
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=TEST_API_KEY,
                    api_secret=TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=asyncio.Event(),
                on_terminated=terminal.append,
            )
            await asyncio.wait_for(stream.task, timeout=2)

        assert len(terminal) == 1
        assert diagnostic in str(terminal[0])

    asyncio.run(scenario())


def test_already_set_stop_event_does_not_open_connection_or_leak_tasks() -> None:
    """Skip connection setup and avoid task leaks when shutdown is already requested."""

    async def scenario() -> None:
        connection_count = 0

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            nonlocal connection_count
            connection_count += 1

        stop_event = asyncio.Event()
        stop_event.set()
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            before = asyncio.all_tasks()
            with pytest.raises(alpaca.AlpacaStreamError, match="stopped before subscription"):
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url="ws://alpaca.test/v1beta1/news",
                    ),
                    stop_event=stop_event,
                )
            assert connection_count == 0
            assert not [task for task in asyncio.all_tasks() - before if not task.done()]

    asyncio.run(scenario())


async def _send_json(send: Callable[..., Awaitable[None]], payload: object) -> None:
    await send({"type": "websocket.send", "text": json.dumps(payload)})


_LoopbackHandler = Callable[
    [int, str, asyncio.StreamReader, asyncio.StreamWriter, list[tuple[bytes, bytes]]],
    Awaitable[None],
]


class _LoopbackServer:
    def __init__(self, handler: _LoopbackHandler) -> None:
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.connection_count = 0
        self.paths: list[str] = []
        self.url = ""

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        address = self._server.sockets[0].getsockname()
        self.url = f"ws://127.0.0.1:{address[1]}"
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
        for writer in self._writers:
            writer.close()
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        errors = [result for result in results if isinstance(result, Exception) and not isinstance(result, OSError)]
        if errors:
            message = "loopback WebSocket server handler failed"
            raise ExceptionGroup(message, errors)

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        self._writers.add(writer)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            lines = request.decode("latin-1").split("\r\n")
            path = lines[0].split()[1]
            headers = []
            for line in lines[1:]:
                if line:
                    name, value = line.split(":", 1)
                    headers.append((name.lower().encode(), value.strip().encode()))
            self.connection_count += 1
            self.paths.append(path)
            await self._handler(self.connection_count, path, reader, writer, headers)
        except ConnectionError, OSError, asyncio.IncompleteReadError:
            return
        finally:
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()


async def _accept_websocket(
    path: str,
    headers: list[tuple[bytes, bytes]],
    writer: asyncio.StreamWriter,
) -> WSConnection:
    websocket = WSConnection(ConnectionType.SERVER)
    websocket.initiate_upgrade_connection(headers, path)
    writer.write(websocket.send(AcceptConnection()))
    await writer.drain()
    return websocket


async def _send_ws_json(writer: asyncio.StreamWriter, websocket: WSConnection, payload: object) -> None:
    writer.write(websocket.send(TextMessage(json.dumps(payload))))
    await writer.drain()


async def _next_ws_events(reader: asyncio.StreamReader, websocket: WSConnection) -> list[object] | None:
    while data := await reader.read(65536):
        websocket.receive_data(data)
        events = list(websocket.events())
        if events:
            return events
    return None


async def _next_client_message(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    websocket: WSConnection,
) -> dict[str, object] | None:
    while events := await _next_ws_events(reader, websocket):
        for event in events:
            if isinstance(event, TextMessage):
                return json.loads(event.data)
            if isinstance(event, Ping):
                writer.write(websocket.send(event.response()))
                await writer.drain()
            if isinstance(event, CloseConnection):
                if websocket.state is not ConnectionState.CLOSED:
                    writer.write(websocket.send(event.response()))
                    await writer.drain()
                return None
    return None


async def _reject_upgrade(writer: asyncio.StreamWriter, status_code: int) -> None:
    reason = {401: "Unauthorized", 503: "Service Unavailable"}.get(status_code, "Rejected")
    writer.write(f"HTTP/1.1 {status_code} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
    await writer.drain()


async def _wait_for_full_queue(queue: asyncio.Queue[object], monkeypatch: pytest.MonkeyPatch) -> None:
    became_full = asyncio.Event()
    if queue.full():
        became_full.set()

    put_nowait = queue.put_nowait

    def observe_put(item: object) -> None:
        put_nowait(item)
        if queue.full():
            became_full.set()

    monkeypatch.setattr(queue, "put_nowait", observe_put)
    async with asyncio.timeout(2):
        await became_full.wait()


class _PingFailureStream:
    def __init__(
        self,
        stream: object,
        failure: Exception | None,
        connection_number: int,
        failures: asyncio.Queue[int],
    ) -> None:
        self._stream = stream
        self._failure = failure
        self._connection_number = connection_number
        self._failures = failures

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:  # noqa: ASYNC109
        """Match the transport stream interface, including its timeout keyword."""
        return await self._stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> bytes:  # noqa: ASYNC109
        """Match the transport stream interface, including its timeout keyword."""
        if buffer and buffer[0] & 0x0F == Opcode.PING:
            if self._failure is not None:
                failure, self._failure = self._failure, None
                self._failures.put_nowait(self._connection_number)
                raise failure
            return buffer
        await self._stream.write(buffer, timeout)
        return buffer

    async def aclose(self) -> None:
        await self._stream.aclose()


class _PingFailureTransport(ASGIWebSocketTransport):
    def __init__(self, app: Callable[..., Awaitable[None]], failures: dict[int, Exception]) -> None:
        super().__init__(app)
        self._failures = failures
        self.connection_count = 0
        self.ping_failures: asyncio.Queue[int] = asyncio.Queue()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await super().handle_async_request(request)
        if response.status_code == HTTPStatus.SWITCHING_PROTOCOLS:
            self.connection_count += 1
            response.extensions["network_stream"] = _PingFailureStream(
                response.extensions["network_stream"],
                self._failures.get(self.connection_count),
                self.connection_count,
                self.ping_failures,
            )
        return response


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_large_news_batch_keeps_event_after_subscription_ack() -> None:
    """Deliver a large news article sent in the subscription acknowledgment batch."""

    async def scenario() -> None:
        requests: list[dict[str, object]] = []
        summary = "A" * 70_100

        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            websocket = await _accept_websocket(path, headers, writer)
            auth = await _next_client_message(reader, writer, websocket)
            assert auth is not None
            requests.append(auth)
            await _send_ws_json(writer, websocket, {"T": "success", "msg": "authenticated"})
            subscription = await _next_client_message(reader, writer, websocket)
            assert subscription is not None
            requests.append(subscription)
            await _send_ws_json(
                writer,
                websocket,
                [
                    {"T": "subscription", "news": ["*"]},
                    {"T": "n", "symbols": ["AAPL"], "author": "Desk", "headline": "Large", "summary": summary},
                ],
            )
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=NEWS_TEST_API_KEY,
                        api_secret=NEWS_TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/v1beta1/news",
                    ),
                    stop_event=stop_event,
                )
                item = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert item.summary == summary
                assert item.symbols == ("AAPL",)
                assert requests == [
                    {"action": "auth", "key": NEWS_TEST_API_KEY, "secret": NEWS_TEST_API_CREDENTIAL},
                    {"action": "subscribe", "news": ["*"]},
                ]
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize("status_code", [401, 503])
def test_loopback_initial_upgrade_failure_remains_a_startup_failure(status_code: int) -> None:
    """Treat an initial WebSocket upgrade rejection as a startup error.

    Args:
        status_code: HTTP status returned for the rejected upgrade.
    """

    async def scenario() -> None:
        async def handler(
            _: int,
            __: str,
            ___: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            ____: list[tuple[bytes, bytes]],
        ) -> None:
            await _reject_upgrade(writer, status_code)

        async with _LoopbackServer(handler) as server:
            async with httpx2.AsyncClient() as client:
                with pytest.raises(alpaca.AlpacaStreamError, match=f"HTTP {status_code}"):
                    await alpaca.start_news_stream(
                        client,
                        settings=AlpacaSettings(
                            api_key=TEST_API_KEY,
                            api_secret=TEST_API_CREDENTIAL,
                            news_stream_url=f"{server.url}/news",
                        ),
                        stop_event=asyncio.Event(),
                    )
            assert server.connection_count == 1

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_acknowledgement_timeout_covers_the_entire_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply one timeout across the full authentication acknowledgment stage."""

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "ACK_TIMEOUT_SECONDS", 0.6)
        heartbeat_count = 0
        heartbeat_send_attempt_count = 10

        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            nonlocal heartbeat_count
            websocket = await _accept_websocket(path, headers, writer)
            await _next_client_message(reader, writer, websocket)
            for _ in range(heartbeat_send_attempt_count):
                tick = asyncio.Event()
                asyncio.get_running_loop().call_later(0.16, tick.set)
                await tick.wait()
                await _send_ws_json(writer, websocket, {"T": "success", "msg": "still connecting"})
                heartbeat_count += 1
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server, httpx2.AsyncClient() as client:
            with pytest.raises(alpaca.AlpacaStreamError, match="timed out waiting for authentication"):
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/news",
                    ),
                    stop_event=asyncio.Event(),
                )

        assert 1 <= heartbeat_count < heartbeat_send_attempt_count

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_keepalive_disconnect_reauthenticates_and_resubscribes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconnect after keepalive failure, reauthenticate, and resubscribe."""

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        ping_seen = asyncio.Event()
        requests: list[dict[str, object]] = []
        recovered_connection_number = 2

        async def handler(
            number: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            websocket = await _accept_websocket(path, headers, writer)
            subscribed = False
            while events := await _next_ws_events(reader, websocket):
                for event in events:
                    if isinstance(event, Ping):
                        if number == 1 and subscribed:
                            ping_seen.set()
                            writer.transport.abort()
                            return
                        writer.write(websocket.send(event.response()))
                        await writer.drain()
                    elif isinstance(event, TextMessage):
                        request = json.loads(event.data)
                        requests.append(request)
                        if request["action"] == "auth":
                            await _send_ws_json(writer, websocket, {"T": "success", "msg": "authenticated"})
                        else:
                            await _send_ws_json(writer, websocket, {"T": "subscription", "news": ["*"]})
                            subscribed = True
                            if number == recovered_connection_number:
                                await _send_ws_json(
                                    writer,
                                    websocket,
                                    {
                                        "T": "n",
                                        "symbols": ["AAPL"],
                                        "author": "Desk",
                                        "headline": "After reconnect",
                                    },
                                )

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/news",
                    ),
                    stop_event=stop_event,
                )
                await asyncio.wait_for(ping_seen.wait(), timeout=2)
                item = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert item.symbols == ("AAPL",)
                assert [request["action"] for request in requests] == ["auth", "subscribe", "auth", "subscribe"]
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


def test_httpx2_mixed_ping_exception_group_terminates_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminate on mixed ping failures without exposing secrets in exception output."""

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_INTERVAL_SECONDS", 0.5)
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        connection_count = 0

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            nonlocal connection_count
            connection_count += 1
            await receive()
            await send({"type": "websocket.accept"})
            await receive()
            await _send_json(send, {"T": "success", "msg": "authenticated"})
            await receive()
            await _send_json(send, {"T": "subscription", "news": ["*"]})
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    return

        mixed_error = ExceptionGroup(
            f"ping failed while handling {REDACTION_TEST_API_CREDENTIAL}",
            [
                httpcore2.WriteTimeout(REDACTION_TEST_API_CREDENTIAL),
                ValueError(REDACTION_TEST_API_KEY),
            ],
        )
        transport = _PingFailureTransport(app, {1: mixed_error})
        terminal: list[alpaca.AlpacaStreamError] = []
        async with transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=REDACTION_TEST_API_KEY,
                    api_secret=REDACTION_TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=asyncio.Event(),
                on_terminated=terminal.append,
            )
            assert await asyncio.wait_for(transport.ping_failures.get(), timeout=2) == 1
            await asyncio.wait_for(stream.task, timeout=2)

        assert len(terminal) == 1
        assert "ExceptionGroup" in str(terminal[0])
        assert terminal[0].__cause__ is None
        assert terminal[0].__context__ is None
        formatted = "".join(traceback.format_exception(terminal[0]))
        assert REDACTION_TEST_API_KEY not in formatted
        assert REDACTION_TEST_API_CREDENTIAL not in formatted
        assert connection_count == 1

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_oversized_message_is_terminal_with_1009_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Terminate oversized news messages with a WebSocket 1009 diagnostic."""

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "MAX_MESSAGE_SIZE_BYTES", 1024)

        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            websocket = await _accept_websocket(path, headers, writer)
            await _next_client_message(reader, writer, websocket)
            await _send_ws_json(writer, websocket, {"T": "success", "msg": "authenticated"})
            await _next_client_message(reader, writer, websocket)
            await _send_ws_json(writer, websocket, {"T": "subscription", "news": ["*"]})
            await _send_ws_json(writer, websocket, {"T": "n", "headline": "A" * 2_000})
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server:
            terminal: list[alpaca.AlpacaStreamError] = []
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/news",
                    ),
                    stop_event=asyncio.Event(),
                    on_terminated=terminal.append,
                )
                await asyncio.wait_for(stream.task, timeout=2)

            assert len(terminal) == 1
            assert "1009" in str(terminal[0])
            assert "configured maximum size" in str(terminal[0])
            assert server.connection_count == 1

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_stop_cancels_during_auth_and_closes_socket() -> None:
    """Cancel startup during authentication and close the pending socket."""

    async def scenario() -> None:
        auth_received = asyncio.Event()
        socket_closed = asyncio.Event()
        handler_task = None

        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            nonlocal handler_task
            handler_task = asyncio.current_task()
            websocket = await _accept_websocket(path, headers, writer)
            while events := await _next_ws_events(reader, websocket):
                for event in events:
                    if isinstance(event, TextMessage):
                        auth_received.set()
                    if isinstance(event, CloseConnection):
                        socket_closed.set()
                        writer.write(websocket.send(event.response()))
                        await writer.drain()
                        return

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            async with httpx2.AsyncClient() as client:
                tasks_before_startup = asyncio.all_tasks()
                startup = asyncio.create_task(
                    alpaca.start_news_stream(
                        client,
                        settings=AlpacaSettings(
                            api_key=TEST_API_KEY,
                            api_secret=TEST_API_CREDENTIAL,
                            news_stream_url=f"{server.url}/news",
                        ),
                        stop_event=stop_event,
                    )
                )
                await asyncio.wait_for(auth_received.wait(), timeout=2)
                stop_event.set()
                with pytest.raises(alpaca.AlpacaStreamError, match="stopped before subscription"):
                    await asyncio.wait_for(startup, timeout=2)
                await asyncio.wait_for(socket_closed.wait(), timeout=2)
                # The loopback handler is a separate test-server task. Let it finish
                # before checking for tasks left by the adapter startup cancellation.
                assert handler_task is not None
                await asyncio.wait_for(handler_task, timeout=2)
                new_pending_tasks = asyncio.all_tasks() - tasks_before_startup
                current_task = asyncio.current_task()
                if current_task is not None:
                    new_pending_tasks.discard(current_task)
                assert not new_pending_tasks, f"pending tasks after startup cancellation: {new_pending_tasks!r}"

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_stop_cancels_when_bounded_queue_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel the stream and close its socket when the event queue is full."""

    async def scenario() -> None:
        socket_closed = asyncio.Event()

        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            websocket = await _accept_websocket(path, headers, writer)
            await _next_client_message(reader, writer, websocket)
            await _send_ws_json(writer, websocket, {"T": "success", "msg": "authenticated"})
            await _next_client_message(reader, writer, websocket)
            await _send_ws_json(writer, websocket, {"T": "subscription", "news": ["*"]})
            await _send_ws_json(
                writer,
                websocket,
                [{"T": "n", "headline": str(index)} for index in range(257)],
            )
            while events := await _next_ws_events(reader, websocket):
                for event in events:
                    if isinstance(event, CloseConnection):
                        socket_closed.set()
                        writer.write(websocket.send(event.response()))
                        await writer.drain()
                        return

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/news",
                    ),
                    stop_event=stop_event,
                )
                await _wait_for_full_queue(cast("asyncio.Queue[object]", stream.events), monkeypatch)
                assert stream.events.qsize() == EXPECTED_EVENT_QUEUE_CAPACITY
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)
                await asyncio.wait_for(socket_closed.wait(), timeout=2)

    asyncio.run(scenario())


def test_stop_event_closes_an_established_idle_news_stream() -> None:
    """Close an idle established news stream when shutdown is requested."""

    async def scenario() -> None:
        subscribed = asyncio.Event()
        socket_closed = asyncio.Event()

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            await receive()
            await send({"type": "websocket.accept"})
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    socket_closed.set()
                    return
                request = json.loads(incoming["text"])
                if request["action"] == "auth":
                    await _send_json(send, {"T": "success", "msg": "authenticated"})
                else:
                    await _send_json(send, {"T": "subscription", "news": ["*"]})
                    subscribed.set()

        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=TEST_API_KEY,
                    api_secret=TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=stop_event,
            )
            await asyncio.wait_for(subscribed.wait(), timeout=2)
            stop_event.set()
            await asyncio.wait_for(stream.task, timeout=2)
            await asyncio.wait_for(socket_closed.wait(), timeout=2)
            assert stream.events.empty()

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    ("api_secret", "reason", "expected_detail"),
    [
        (
            REDACTION_TEST_API_CREDENTIAL,
            f"provider rejected {REDACTION_TEST_API_KEY} and {REDACTION_TEST_API_CREDENTIAL}",
            "WebSocket disconnected with code 1008: provider rejected [redacted] and [redacted]",
        ),
        (
            "",
            f"provider rejected {REDACTION_TEST_API_KEY}",
            "WebSocket disconnected with code 1008: provider rejected [redacted]",
        ),
        (REDACTION_TEST_API_CREDENTIAL, "", "WebSocket disconnected with code 1008"),
    ],
    ids=["redact-both-credentials", "empty-secret-is-safe", "empty-provider-reason"],
)
def test_initial_websocket_disconnect_reports_a_safe_diagnostic(
    api_secret: str,
    reason: str,
    expected_detail: str,
) -> None:
    """Sanitize the initial disconnect diagnostic and redact supplied credentials.

    Args:
        api_secret: Credential that may appear in the provider's close reason.
        reason: Close reason supplied by the provider.
        expected_detail: Expected sanitized detail in the startup error.
    """

    async def scenario() -> None:
        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            websocket = await _accept_websocket(path, headers, writer)
            await _next_client_message(reader, writer, websocket)
            writer.write(websocket.send(CloseConnection(1008, reason)))
            await writer.drain()

        async with _LoopbackServer(handler) as server, httpx2.AsyncClient() as client:
            with pytest.raises(alpaca.AlpacaStreamError, match="initial connection failed") as error:
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=REDACTION_TEST_API_KEY,
                        api_secret=api_secret,
                        news_stream_url=f"{server.url}/v1beta1/news",
                    ),
                    stop_event=asyncio.Event(),
                )

        assert str(error.value) == f"Alpaca news stream initial connection failed: {expected_detail}"
        assert REDACTION_TEST_API_KEY not in str(error.value)
        if api_secret:
            assert api_secret not in str(error.value)

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_initial_loopback_socket_reset_fails_stream_startup() -> None:
    """Treat a socket reset during initial setup as a safe startup failure."""

    async def scenario() -> None:
        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            websocket = await _accept_websocket(path, headers, writer)
            await _next_client_message(reader, writer, websocket)
            writer.transport.abort()

        async with _LoopbackServer(handler) as server, httpx2.AsyncClient() as client:
            with pytest.raises(alpaca.AlpacaStreamError, match="initial connection failed") as error:
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=REDACTION_TEST_API_KEY,
                        api_secret=REDACTION_TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/v1beta1/news",
                    ),
                    stop_event=asyncio.Event(),
                )

        assert server.connection_count == 1
        assert REDACTION_TEST_API_KEY not in str(error.value)
        assert REDACTION_TEST_API_CREDENTIAL not in str(error.value)

    asyncio.run(scenario())
