"""Behavioral tests for Alpaca's WebSocket adapters."""

import asyncio
import json
import traceback
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import cast

import httpcore2
import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport
from wsproto import ConnectionType, WSConnection
from wsproto.connection import ConnectionState
from wsproto.events import AcceptConnection, CloseConnection, Ping, TextMessage

from stocknews import alpaca
from stocknews.models import NewsItem, Trade


@pytest.mark.parametrize("wrapped_subscription", [False, True], ids=["flat-ack", "wrapped-ack"])
def test_news_stream_authenticates_subscribes_and_decodes_events(wrapped_subscription: bool) -> None:
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
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_news_stream(
                    client,
                    url="ws://alpaca.test/v1beta1/news",
                    api_key="news-key",
                    api_secret="news-secret",
                    stop_event=stop_event,
                )
                item = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert stream.events.maxsize == 256
                assert item == NewsItem(
                    symbols=("AAPL", "MSFT"),
                    author="Benzinga Newsdesk",
                    headline="A headline",
                    summary="A summary",
                    url="https://example.test/news",
                )
                assert requests == [
                    {"action": "auth", "key": "news-key", "secret": "news-secret"},
                    {"action": "subscribe", "news": ["*"]},
                ]
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


def test_trade_stream_reconnects_on_disconnect_and_resubscribes_to_iex(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        requests: list[dict[str, object]] = []
        paths: list[str] = []
        second_subscription = asyncio.Event()
        connection_count = 0

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            nonlocal connection_count
            connection_count += 1
            paths.append(str(scope["path"]))
            await receive()
            await send({"type": "websocket.accept"})
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    return
                message = json.loads(incoming["text"])
                requests.append(message)
                if message["action"] == "auth":
                    await _send_json(send, {"T": "success", "msg": "authenticated"})
                else:
                    await _send_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})
                    await _send_json(
                        send,
                        [
                            {"T": "status", "msg": "feed ready"},
                            {
                                "T": "t",
                                "S": "SPY",
                                "p": 500.25,
                                "s": 100,
                                "x": "V",
                                "t": "2026-09-21T14:30:00Z",
                                "c": ["@", "F"],
                                "z": "C",
                            },
                        ],
                    )
                    if connection_count == 1:
                        await send({"type": "websocket.close", "code": 1001, "reason": "server restart"})
                        return
                    second_subscription.set()

        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url="ws://alpaca.test/v2/",
                    api_key="trade-key",
                    api_secret="trade-secret",
                    stop_event=stop_event,
                )
                first = await asyncio.wait_for(stream.events.get(), timeout=2)
                await asyncio.wait_for(second_subscription.wait(), timeout=2)
                second = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert first == Trade(
                    symbol="SPY",
                    price=500.25,
                    size=100,
                    exchange="V",
                    timestamp="2026-09-21T14:30:00Z",
                    conditions=("@", "F"),
                    tape="C",
                )
                assert second == first
                assert paths == ["/v2/iex", "/v2/iex"]
                assert requests == [
                    {"action": "auth", "key": "trade-key", "secret": "trade-secret"},
                    {"action": "subscribe", "trades": ["SPY", "QQQ"]},
                    {"action": "auth", "key": "trade-key", "secret": "trade-secret"},
                    {"action": "subscribe", "trades": ["SPY", "QQQ"]},
                ]
                assert stream.events.maxsize == 256
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response", "diagnostic"),
    [
        (
            {"T": "error", "code": 401, "msg": "invalid demo-key and demo-secret"},
            "401",
        ),
        ({"T": "error", "msg": None, "code": True}, "server rejected the request"),
    ],
    ids=["provider-error-redacts-credentials", "missing-error-details-use-safe-fallback"],
)
def test_initial_trade_auth_failure_is_raised_without_exposing_credentials(
    response: dict[str, object], diagnostic: str
) -> None:
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
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                with pytest.raises(alpaca.AlpacaStreamError) as error:
                    await alpaca.start_trade_stream(
                        client,
                        base_url="ws://alpaca.test/v2",
                        api_key="demo-key",
                        api_secret="demo-secret",
                        stop_event=stop_event,
                    )
        assert "authentication failed" in str(error.value)
        assert diagnostic in str(error.value)
        assert "demo-key" not in str(error.value)
        assert "demo-secret" not in str(error.value)
        formatted = "".join(traceback.format_exception(error.value))
        assert "demo-key" not in formatted
        assert "demo-secret" not in formatted

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError("private-key private-secret"),
        httpx2.ConnectTimeout("private-key private-secret"),
    ],
    ids=["connection-refused", "connection-timeout"],
)
def test_initial_transport_failure_is_clear_and_does_not_leak_tasks_or_credentials(
    failure: httpx2.TransportError,
) -> None:
    async def scenario() -> None:
        async def unreachable_app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            raise AssertionError("the failing transport must reject the connection before the ASGI app runs")

        class FailingTransport(ASGIWebSocketTransport):
            async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
                raise failure

        pending_before = asyncio.all_tasks()
        async with FailingTransport(unreachable_app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                with pytest.raises(alpaca.AlpacaStreamError, match="initial connection failed") as error:
                    await alpaca.start_news_stream(
                        client,
                        url="ws://alpaca.test/v1beta1/news",
                        api_key="private-key",
                        api_secret="private-secret",
                        stop_event=asyncio.Event(),
                    )

        assert "network transport error" in str(error.value)
        assert "private-key" not in str(error.value)
        assert "private-secret" not in str(error.value)
        formatted = "".join(traceback.format_exception(error.value))
        assert "private-key" not in formatted
        assert "private-secret" not in formatted
        assert not [task for task in asyncio.all_tasks() - pending_before if not task.done()]

    asyncio.run(scenario())


def test_initial_authentication_timeout_is_reported_without_leaking_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
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

        pending_before = asyncio.all_tasks()
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                with pytest.raises(alpaca.AlpacaStreamError, match="timed out waiting for authentication"):
                    await alpaca.start_news_stream(
                        client,
                        url="ws://alpaca.test/v1beta1/news",
                        api_key="key",
                        api_secret="secret",
                        stop_event=asyncio.Event(),
                    )

        assert not [task for task in asyncio.all_tasks() - pending_before if not task.done()]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response", "diagnostic"),
    [("{", "invalid JSON"), ('"unexpected scalar"', "invalid shape")],
    ids=["malformed-json", "invalid-message-shape"],
)
def test_invalid_handshake_messages_fail_startup(response: str, diagnostic: str) -> None:
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

        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                with pytest.raises(alpaca.AlpacaStreamError, match=diagnostic):
                    await alpaca.start_news_stream(
                        client,
                        url="ws://alpaca.test/v1beta1/news",
                        api_key="key",
                        api_secret="secret",
                        stop_event=asyncio.Event(),
                    )

    asyncio.run(scenario())


def test_malformed_established_message_terminates_the_stream() -> None:
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
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_news_stream(
                    client,
                    url="ws://alpaca.test/v1beta1/news",
                    api_key="key",
                    api_secret="secret",
                    stop_event=asyncio.Event(),
                    on_terminated=terminal.append,
                )
                await asyncio.wait_for(stream.task, timeout=2)

        assert connection_count == 1
        assert len(terminal) == 1
        assert "invalid JSON" in str(terminal[0])

    asyncio.run(scenario())


def test_unexpected_binary_message_terminates_the_stream() -> None:
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
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_news_stream(
                    client,
                    url="ws://alpaca.test/v1beta1/news",
                    api_key="key",
                    api_secret="secret",
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
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_news_stream(
                    client,
                    url="ws://alpaca.test/v1beta1/news",
                    api_key="key",
                    api_secret="secret",
                    stop_event=asyncio.Event(),
                    on_terminated=terminal.append,
                )
                await asyncio.wait_for(stream.task, timeout=2)

        assert len(terminal) == 1
        assert diagnostic in str(terminal[0])

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("p", True, "price"),
        ("p", "500.25", "price"),
        ("p", float("nan"), "price"),
        ("p", float("inf"), "price"),
        ("s", True, "size"),
        ("s", 1.5, "size"),
        ("s", -1, "size"),
        ("s", 2**32, "size"),
    ],
    ids=[
        "boolean-price",
        "text-price",
        "nan-price",
        "infinite-price",
        "boolean-size",
        "fractional-size",
        "negative-size",
        "oversized-size",
    ],
)
def test_invalid_trade_number_terminates_established_stream(field: str, value: object, diagnostic: str) -> None:
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
            await _send_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})
            trade: dict[str, object] = {"T": "t", "S": "SPY", "p": 500.25, "s": 10}
            trade[field] = value
            await _send_json(send, trade)

        terminal: list[alpaca.AlpacaStreamError] = []
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url="ws://alpaca.test/v2",
                    api_key="key",
                    api_secret="secret",
                    stop_event=asyncio.Event(),
                    on_terminated=terminal.append,
                )
                await asyncio.wait_for(stream.task, timeout=2)

        assert len(terminal) == 1
        assert diagnostic in str(terminal[0])

    asyncio.run(scenario())


def test_terminal_protocol_error_without_callback_raises_from_stream_task() -> None:
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
            await _send_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})
            await _send_json(send, {"T": "error", "code": 403, "msg": "stream revoked"})

        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url="ws://alpaca.test/v2",
                    api_key="key",
                    api_secret="secret",
                    stop_event=asyncio.Event(),
                )
                with pytest.raises(alpaca.AlpacaStreamError, match="stream revoked"):
                    await stream.task

    asyncio.run(scenario())


def test_already_set_stop_event_does_not_open_connection_or_leak_tasks() -> None:
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
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                before = asyncio.all_tasks()
                with pytest.raises(alpaca.AlpacaStreamError, match="stopped before subscription"):
                    await alpaca.start_news_stream(
                        client,
                        url="ws://alpaca.test/v1beta1/news",
                        api_key="key",
                        api_secret="secret",
                        stop_event=stop_event,
                    )
                assert connection_count == 0
                assert not [task for task in asyncio.all_tasks() - before if not task.done()]

    asyncio.run(scenario())


def test_established_subscription_failure_uses_terminal_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        requests: list[dict[str, object]] = []
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
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    return
                message = json.loads(incoming["text"])
                requests.append(message)
                if message["action"] == "auth":
                    await _send_json(send, {"T": "success", "msg": "authenticated"})
                elif connection_count == 1:
                    await _send_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})
                    await send({"type": "websocket.close", "code": 1001, "reason": "reconnect"})
                    return
                else:
                    await _send_json(send, {"T": "error", "code": 403, "msg": "subscription rejected: private-secret"})
                    return

        stop_event = asyncio.Event()
        terminal_errors: list[alpaca.AlpacaStreamError] = []
        async with ASGIWebSocketTransport(app) as transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url="ws://alpaca.test/v2",
                    api_key="private-key",
                    api_secret="private-secret",
                    stop_event=stop_event,
                    on_terminated=terminal_errors.append,
                )
                await asyncio.wait_for(stream.task, timeout=2)

        assert len(terminal_errors) == 1
        assert "subscription failed" in str(terminal_errors[0])
        assert "403" in str(terminal_errors[0])
        assert "private-key" not in str(terminal_errors[0])
        assert "private-secret" not in str(terminal_errors[0])
        assert [request["action"] for request in requests] == ["auth", "subscribe", "auth", "subscribe"]
        assert not stop_event.is_set()

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

    async def __aenter__(self) -> _LoopbackServer:
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
            raise ExceptionGroup("loopback WebSocket server handler failed", errors)

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


async def _wait_for_full_queue(queue: asyncio.Queue[object]) -> None:
    async with asyncio.timeout(2):
        while not queue.full():
            await asyncio.sleep(0)


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

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self._stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if buffer and buffer[0] & 0x0F == 0x9:
            if self._failure is not None:
                failure, self._failure = self._failure, None
                self._failures.put_nowait(self._connection_number)
                raise failure
            return
        await self._stream.write(buffer, timeout)

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
        if response.status_code == 101:
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
                    url=f"{server.url}/v1beta1/news",
                    api_key="news-key",
                    api_secret="news-secret",
                    stop_event=stop_event,
                )
                item = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert len(item.summary) == 70_100
                assert item.symbols == ("AAPL",)
                assert requests == [
                    {"action": "auth", "key": "news-key", "secret": "news-secret"},
                    {"action": "subscribe", "news": ["*"]},
                ]
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    "acknowledged",
    [["SPY"], "SPY", ["SPY", 17]],
    ids=["missing-requested-symbol", "wrong-acknowledgement-type", "non-text-symbol"],
)
def test_loopback_subscription_ack_requires_all_requested_symbols(acknowledged: object) -> None:
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
            await _send_ws_json(writer, websocket, {"T": "success", "msg": "authenticated"})
            await _next_client_message(reader, writer, websocket)
            await _send_ws_json(writer, websocket, {"T": "subscription", "trades": acknowledged})
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server:
            async with httpx2.AsyncClient() as client:
                with pytest.raises(alpaca.AlpacaStreamError, match="incomplete trades subscription"):
                    await alpaca.start_trade_stream(
                        client,
                        base_url=f"{server.url}/v2",
                        api_key="key",
                        api_secret="secret",
                        stop_event=asyncio.Event(),
                    )

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_established_503_retries_and_401_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        requests: list[dict[str, object]] = []

        async def handler(
            number: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            if number == 2:
                await _reject_upgrade(writer, 503)
                return
            if number == 4:
                await _reject_upgrade(writer, 401)
                return
            websocket = await _accept_websocket(path, headers, writer)
            while request := await _next_client_message(reader, writer, websocket):
                requests.append(request)
                if request["action"] == "auth":
                    await _send_ws_json(writer, websocket, {"T": "success", "msg": "authenticated"})
                else:
                    await _send_ws_json(
                        writer,
                        websocket,
                        [
                            {"T": "subscription", "trades": ["SPY", "QQQ"]},
                            {"T": "t", "S": "SPY", "p": 500, "s": 1},
                        ],
                    )
                    writer.write(websocket.send(CloseConnection(1012, "restart")))
                    await writer.drain()
                    return

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            terminal: list[alpaca.AlpacaStreamError] = []
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url=f"{server.url}/v2",
                    api_key="key",
                    api_secret="secret",
                    stop_event=stop_event,
                    on_terminated=terminal.append,
                )
                await asyncio.wait_for(stream.task, timeout=2)
                trades = [stream.events.get_nowait(), stream.events.get_nowait()]

            assert server.paths == ["/v2/iex"] * 4
            assert [request["action"] for request in requests] == ["auth", "subscribe", "auth", "subscribe"]
            assert [trade.symbol for trade in trades] == ["SPY", "SPY"]
            assert len(terminal) == 1
            assert "HTTP 401" in str(terminal[0])
            assert server.connection_count == 4

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize("status_code", [401, 503])
def test_loopback_initial_upgrade_failure_remains_a_startup_failure(status_code: int) -> None:
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
                        url=f"{server.url}/news",
                        api_key="key",
                        api_secret="secret",
                        stop_event=asyncio.Event(),
                    )
            assert server.connection_count == 1

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_acknowledgement_timeout_covers_the_entire_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "ACK_TIMEOUT_SECONDS", 0.6)
        heartbeat_count = 0

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
            for _ in range(10):
                tick = asyncio.Event()
                asyncio.get_running_loop().call_later(0.16, tick.set)
                await tick.wait()
                await _send_ws_json(writer, websocket, {"T": "success", "msg": "still connecting"})
                heartbeat_count += 1
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server:
            async with httpx2.AsyncClient() as client:
                with pytest.raises(alpaca.AlpacaStreamError, match="timed out waiting for authentication"):
                    await alpaca.start_news_stream(
                        client,
                        url=f"{server.url}/news",
                        api_key="key",
                        api_secret="secret",
                        stop_event=asyncio.Event(),
                    )

        assert 1 <= heartbeat_count < 10

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_keepalive_disconnect_reauthenticates_and_resubscribes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        ping_seen = asyncio.Event()
        requests: list[dict[str, object]] = []

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
                            await _send_ws_json(writer, websocket, {"T": "subscription", "trades": ["SPY", "QQQ"]})
                            subscribed = True
                            if number == 2:
                                await _send_ws_json(writer, websocket, {"T": "t", "S": "QQQ", "p": 480, "s": 1})

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url=f"{server.url}/v2",
                    api_key="key",
                    api_secret="secret",
                    stop_event=stop_event,
                )
                await asyncio.wait_for(ping_seen.wait(), timeout=2)
                trade = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert trade.symbol == "QQQ"
                assert [request["action"] for request in requests] == ["auth", "subscribe", "auth", "subscribe"]
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "network_error",
    [httpcore2.WriteError, httpcore2.WriteTimeout],
    ids=["write-error", "write-timeout"],
)
def test_httpx2_ping_transport_groups_reconnect_and_resubscribe(
    monkeypatch: pytest.MonkeyPatch,
    network_error: type[Exception],
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_INTERVAL_SECONDS", 0.5)
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_TIMEOUT_SECONDS", 0.5)
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        requests: list[dict[str, object]] = []
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
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    return
                request = json.loads(incoming["text"])
                requests.append(request)
                if request["action"] == "auth":
                    await _send_json(send, {"T": "success", "msg": "authenticated"})
                else:
                    await _send_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})
                    if connection_count == 2:
                        await _send_json(send, {"T": "t", "S": "SPY", "p": 500, "s": 1})

        transport = _PingFailureTransport(app, {1: network_error("injected keepalive failure")})
        stop_event = asyncio.Event()
        async with transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url="ws://alpaca.test/v2",
                    api_key="key",
                    api_secret="secret",
                    stop_event=stop_event,
                )
                assert await asyncio.wait_for(transport.ping_failures.get(), timeout=2) == 1
                trade = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert trade.symbol == "SPY"
                assert transport.connection_count == 2
                assert [request["action"] for request in requests[:4]] == ["auth", "subscribe", "auth", "subscribe"]
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())


def test_httpx2_mixed_ping_exception_group_terminates_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_INTERVAL_SECONDS", 0.5)
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        requests: list[dict[str, object]] = []
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
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    return
                request = json.loads(incoming["text"])
                requests.append(request)
                if request["action"] == "auth":
                    await _send_json(send, {"T": "success", "msg": "authenticated"})
                else:
                    await _send_json(send, {"T": "subscription", "trades": ["SPY", "QQQ"]})

        mixed_error = ExceptionGroup(
            "ping failed while handling private-secret",
            [httpcore2.WriteTimeout("private-secret"), ValueError("private-key")],
        )
        transport = _PingFailureTransport(app, {1: mixed_error})
        terminal: list[alpaca.AlpacaStreamError] = []
        async with transport:
            async with httpx2.AsyncClient(transport=transport) as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url="ws://alpaca.test/v2",
                    api_key="private-key",
                    api_secret="private-secret",
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
        assert "private-key" not in formatted
        assert "private-secret" not in formatted
        assert [request["action"] for request in requests] == ["auth", "subscribe"]
        assert connection_count == 1

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_oversized_message_is_terminal_with_1009_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
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
                    url=f"{server.url}/news",
                    api_key="key",
                    api_secret="secret",
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
    async def scenario() -> None:
        auth_received = asyncio.Event()
        socket_closed = asyncio.Event()

        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
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
                startup = asyncio.create_task(
                    alpaca.start_news_stream(
                        client,
                        url=f"{server.url}/news",
                        api_key="key",
                        api_secret="secret",
                        stop_event=stop_event,
                    )
                )
                await asyncio.wait_for(auth_received.wait(), timeout=2)
                stop_event.set()
                with pytest.raises(alpaca.AlpacaStreamError, match="stopped before subscription"):
                    await asyncio.wait_for(startup, timeout=2)
                await asyncio.wait_for(socket_closed.wait(), timeout=2)

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_stop_cancels_reconnect_backoff() -> None:
    async def scenario() -> None:
        class ObservedStopEvent(asyncio.Event):
            def __init__(self) -> None:
                super().__init__()
                self.backoff_started = asyncio.Event()
                self.wait_count = 0

            async def wait(self) -> bool:
                self.wait_count += 1
                if self.wait_count >= 2:
                    self.backoff_started.set()
                return await super().wait()

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
            await _send_ws_json(writer, websocket, {"T": "subscription", "trades": ["SPY", "QQQ"]})
            writer.write(websocket.send(CloseConnection(1012, "restart")))
            await writer.drain()
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server:
            stop_event = ObservedStopEvent()
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_trade_stream(
                    client,
                    base_url=f"{server.url}/v2",
                    api_key="key",
                    api_secret="secret",
                    stop_event=stop_event,
                )
                await asyncio.wait_for(stop_event.backoff_started.wait(), timeout=2)
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)
                assert server.connection_count == 1

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_stop_cancels_when_bounded_queue_is_full() -> None:
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
                    url=f"{server.url}/news",
                    api_key="key",
                    api_secret="secret",
                    stop_event=stop_event,
                )
                await _wait_for_full_queue(cast(asyncio.Queue[object], stream.events))
                assert stream.events.qsize() == 256
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)
                await asyncio.wait_for(socket_closed.wait(), timeout=2)

    asyncio.run(scenario())
