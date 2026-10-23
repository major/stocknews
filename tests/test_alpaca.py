"""Behavioral tests for Alpaca's WebSocket adapters."""

import asyncio
import json
import logging
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
EXPECTED_RECONNECT_ATTEMPT = 2
EXPECTED_DIAGNOSTIC_TEXT_LIMIT = 64

# Fake credentials used by stream fixtures that do not inspect authentication.
TEST_API_KEY = "key"
TEST_API_CREDENTIAL = "secret"

# Fake credentials used when tests inspect authentication requests.
NEWS_TEST_API_KEY = "news-key"
NEWS_TEST_API_CREDENTIAL = "news-secret"
# Raw article body with HTML markup and newlines, which must reach consumers unchanged.
CONTENT_WITH_HTML_AND_NEWLINES = "<p>Raw <strong>HTML</strong> content.</p>\n<p>Second line.</p>\n"
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
def test_news_stream_authenticates_subscribes_and_decodes_events(
    wrapped_subscription: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """Authenticate to the news stream, subscribe, and decode the delivered article."""
    caplog.set_level("INFO", logger=alpaca.__name__)

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
                                "source": "benzinga",
                                "content": CONTENT_WITH_HTML_AND_NEWLINES,
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
                source="benzinga",
                content=CONTENT_WITH_HTML_AND_NEWLINES,
            )
            assert requests == [
                {"action": "auth", "key": NEWS_TEST_API_KEY, "secret": NEWS_TEST_API_CREDENTIAL},
                {"action": "subscribe", "news": ["*"]},
            ]
            stop_event.set()
            await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())

    records = [
        record
        for record in caplog.records
        if record.name == alpaca.__name__ and record.getMessage() != "received Alpaca stream message"
    ]
    assert [record.getMessage() for record in records] == [
        "connecting to Alpaca stream",
        "connected to Alpaca stream",
        "authenticated Alpaca stream",
        "subscribed to Alpaca stream",
    ]
    assert [(record.stream, record.attempt) for record in records] == [("news", 1)] * 4
    assert records[-1].channel == "news"
    assert records[-1].symbols == ("*",)
    assert not any(record.getMessage() == "reconnecting Alpaca stream" for record in records)


def test_news_parser_defaults_raw_fields_to_empty_when_absent() -> None:
    """Use empty strings for absent raw Alpaca news fields."""
    item = alpaca._parse_news({"T": "n"})  # noqa: SLF001

    assert item == NewsItem(symbols=(), author="", headline="")
    assert item.source == ""
    assert item.content == ""


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
    response: dict[str, object], diagnostic: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep credentials out of errors and tracebacks when initial news authentication fails.

    Args:
        response: Authentication response sent by the provider.
        diagnostic: Expected safe detail in the resulting error.
        caplog: Fixture that captures Alpaca lifecycle logs.
    """
    caplog.set_level("INFO", logger=alpaca.__name__)

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
    messages = [record.getMessage() for record in caplog.records if record.name == alpaca.__name__]
    assert "authenticated Alpaca stream" not in messages
    assert "subscribed to Alpaca stream" not in messages
    assert "reconnecting Alpaca stream" not in messages


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(f"{REDACTION_TEST_API_KEY} {REDACTION_TEST_API_CREDENTIAL}"),
        httpx2.ConnectTimeout(f"{REDACTION_TEST_API_KEY} {REDACTION_TEST_API_CREDENTIAL}"),
    ],
    ids=["connection-refused", "connection-timeout"],
)
def test_initial_transport_failure_is_clear_and_does_not_leak_tasks_or_credentials(
    failure: httpx2.TransportError, caplog: pytest.LogCaptureFixture
) -> None:
    """Report initial transport failures without leaking credentials or leaving tasks behind.

    Args:
        failure: Transport error raised before the WebSocket handshake.
        caplog: Fixture that captures Alpaca lifecycle logs.
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
    assert not any(
        record.getMessage() == "reconnecting Alpaca stream"
        for record in caplog.records
        if record.name == alpaca.__name__
    )


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
    [
        ("author", 17, "author"),
        ("symbols", "AAPL", "symbols"),
        ("source", 17, "source"),
        ("content", 17, "content"),
    ],
    ids=["author-type", "symbols-type", "source-type", "content-type"],
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


def test_terminal_protocol_error_without_callback_raises_from_stream_task() -> None:
    """Propagate terminal provider errors from the stream task without a callback."""

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
            await _send_json(send, {"T": "error", "code": 403, "msg": "stream revoked"})

        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=TEST_API_KEY,
                    api_secret=TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=asyncio.Event(),
            )
            with pytest.raises(alpaca.AlpacaStreamError, match="stream revoked"):
                await stream.task

    asyncio.run(scenario())


class _StopOnActionWebSocket:
    """Script a stop request when the selected protocol action is sent."""

    def __init__(self, stop_event: asyncio.Event, stop_after_action: str) -> None:
        """Save the stop event and action that trigger shutdown."""
        self.stop_event = stop_event
        self.stop_after_action = stop_after_action
        self.requests: list[dict[str, object]] = []
        self.receive_count = 0

    async def send_json(self, message: dict[str, object]) -> None:
        """Record a wire-normalized request and synchronously stop after the selected action."""
        self.requests.append(
            {key: list(value) if isinstance(value, tuple) else value for key, value in message.items()}
        )
        if message["action"] == self.stop_after_action:
            self.stop_event.set()

    async def receive_json(self, **_: object) -> object:
        """Return the auth acknowledgment, rejecting reads after shutdown."""
        self.receive_count += 1
        assert not self.stop_event.is_set()
        assert self.requests
        assert self.requests[-1]["action"] == "auth"
        return {"T": "success", "msg": "authenticated"}


@pytest.mark.parametrize("stopped_stage", ["auth", "subscription"], ids=["auth", "subscription"])
def test_news_handshake_stops_without_reading_the_next_ack(stopped_stage: str) -> None:
    """Stop deterministically during either news handshake stage.

    Args:
        stopped_stage: Handshake action that synchronously sets the stop event.
    """

    async def scenario() -> None:
        """Arrange a news stream and stop after the selected request."""
        stop_event = asyncio.Event()
        stop_after_action = "auth" if stopped_stage == "auth" else "subscribe"
        websocket = _StopOnActionWebSocket(stop_event, stop_after_action)
        async with httpx2.AsyncClient() as client:
            config = alpaca._StreamConfig(  # noqa: SLF001
                client=client,
                url="ws://alpaca.test/v1beta1/news",
                api_key=NEWS_TEST_API_KEY,
                api_secret=NEWS_TEST_API_CREDENTIAL,
                stream_name="news",
                channel="news",
                symbols=("*",),
                parse_event=alpaca._parse_news,  # noqa: SLF001
            )
            pending_messages = await alpaca._authenticate_and_subscribe(  # noqa: SLF001
                config,
                cast("httpx2.websockets.AsyncWebSocketSession", websocket),
                stop_event,
                attempt=1,
            )

        expected_requests = [{"action": "auth", "key": NEWS_TEST_API_KEY, "secret": NEWS_TEST_API_CREDENTIAL}]
        expected_receive_count = 0
        if stopped_stage == "subscription":
            expected_requests.append({"action": "subscribe", "news": ["*"]})
            expected_receive_count = 1

        assert stop_event.is_set()
        assert websocket.requests == expected_requests
        assert websocket.receive_count == expected_receive_count
        assert pending_messages == []

    asyncio.run(scenario())


def test_header_handshake_stops_before_sending_subscription() -> None:
    """Skip subscription when shutdown is requested before header authentication."""

    async def scenario() -> None:
        """Run the header handshake helper with shutdown already requested."""
        stop_event = asyncio.Event()
        stop_event.set()
        websocket = _StopOnActionWebSocket(stop_event, "subscribe")
        async with httpx2.AsyncClient() as client:
            config = alpaca._StreamConfig(  # noqa: SLF001
                client=client,
                url="ws://alpaca.test/v1beta1/news",
                api_key=NEWS_TEST_API_KEY,
                api_secret=NEWS_TEST_API_CREDENTIAL,
                stream_name="news",
                channel="news",
                symbols=("*",),
                parse_event=alpaca._parse_news,  # noqa: SLF001
                auth_mode="headers",
            )
            pending_messages = await alpaca._authenticate_and_subscribe(  # noqa: SLF001
                config,
                cast("httpx2.websockets.AsyncWebSocketSession", websocket),
                stop_event,
                attempt=1,
            )

        assert stop_event.is_set()
        assert websocket.requests == []
        assert websocket.receive_count == 0
        assert pending_messages == []

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


def test_stop_set_on_transport_failure_skips_reconnect_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Skip the retry warning if shutdown is requested as a connection fails."""
    caplog.set_level("INFO", logger=alpaca.__name__)

    async def scenario() -> None:
        """Stop an established ASGI stream after its server disconnects."""
        stop_event = asyncio.Event()
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
            await send({"type": "websocket.close", "code": 1001, "reason": "shutdown"})

        read_connection = alpaca._read_connection  # noqa: SLF001

        async def set_stop_after_disconnect(
            config: alpaca._StreamConfig[NewsItem],
            event: asyncio.Event,
            events: asyncio.Queue[NewsItem],
            started: asyncio.Future[None],
            attempt: int,
        ) -> alpaca._Stopped | alpaca._TransportFailure | alpaca._TerminalFailure:
            """Set shutdown after the real connection adapter reports a disconnect."""
            outcome = await read_connection(config, event, events, started, attempt)
            if isinstance(outcome, alpaca._TransportFailure):  # noqa: SLF001
                event.set()
            return outcome

        monkeypatch.setattr(alpaca, "_read_connection", set_stop_after_disconnect)
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
            await asyncio.wait_for(stream.task, timeout=2)
        assert connection_count == 1

    asyncio.run(scenario())
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == alpaca.__name__ and record.getMessage() != "received Alpaca stream message"
    ]
    assert messages == [
        "connecting to Alpaca stream",
        "connected to Alpaca stream",
        "authenticated Alpaca stream",
        "subscribed to Alpaca stream",
    ]


def test_established_subscription_failure_uses_terminal_callback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Report news resubscription failures without setting the stop event."""
    caplog.set_level("INFO", logger=alpaca.__name__)

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
                    await _send_json(send, {"T": "subscription", "news": ["*"]})
                    await send(
                        {
                            "type": "websocket.close",
                            "code": 1001,
                            "reason": f"reconnect {REDACTION_TEST_API_KEY} {REDACTION_TEST_API_CREDENTIAL}",
                        }
                    )
                    return
                else:
                    await _send_json(
                        send,
                        {
                            "T": "error",
                            "code": 403,
                            "msg": f"subscription rejected: {REDACTION_TEST_API_CREDENTIAL}",
                        },
                    )
                    return

        stop_event = asyncio.Event()
        terminal_errors: list[alpaca.AlpacaStreamError] = []
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=REDACTION_TEST_API_KEY,
                    api_secret=REDACTION_TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=stop_event,
                on_terminated=terminal_errors.append,
            )
            await asyncio.wait_for(stream.task, timeout=2)

        assert len(terminal_errors) == 1
        assert "subscription failed" in str(terminal_errors[0])
        assert "403" in str(terminal_errors[0])
        assert REDACTION_TEST_API_KEY not in str(terminal_errors[0])
        assert REDACTION_TEST_API_CREDENTIAL not in str(terminal_errors[0])
        assert [request["action"] for request in requests] == ["auth", "subscribe", "auth", "subscribe"]
        assert not stop_event.is_set()

    asyncio.run(scenario())

    records = [record for record in caplog.records if record.name == alpaca.__name__]
    reconnect_index = next(
        index for index, record in enumerate(records) if record.getMessage() == "reconnecting Alpaca stream"
    )
    assert [record.getMessage() for record in records][reconnect_index - 1 : reconnect_index + 2] == [
        "subscribed to Alpaca stream",
        "reconnecting Alpaca stream",
        "connecting to Alpaca stream",
    ]
    reconnect = records[reconnect_index]
    assert reconnect.stream == "news"
    assert reconnect.attempt == EXPECTED_RECONNECT_ATTEMPT
    assert reconnect.retry_delay_seconds == 0
    assert "WebSocket disconnected with code 1001" in reconnect.error_summary
    assert "[redacted]" in reconnect.error_summary
    assert REDACTION_TEST_API_KEY not in reconnect.error_summary
    assert REDACTION_TEST_API_CREDENTIAL not in reconnect.error_summary
    assert [(record.getMessage(), record.attempt) for record in records if hasattr(record, "attempt")] == [
        ("connecting to Alpaca stream", 1),
        ("connected to Alpaca stream", 1),
        ("authenticated Alpaca stream", 1),
        ("subscribed to Alpaca stream", 1),
        ("reconnecting Alpaca stream", 2),
        ("connecting to Alpaca stream", 2),
        ("connected to Alpaca stream", 2),
        ("authenticated Alpaca stream", 2),
    ]
    assert sum(record.getMessage() == "reconnecting Alpaca stream" for record in records) == 1


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

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Match the transport stream interface, including its timeout keyword."""
        if buffer and buffer[0] & 0x0F == Opcode.PING:
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
def test_loopback_header_auth_sends_credentials_in_handshake_and_skips_auth_message() -> None:
    """Send Alpaca credentials in the WebSocket handshake and subscribe directly."""

    async def scenario() -> None:
        """Run header authentication against a local WebSocket server."""
        handshake_headers: dict[bytes, bytes] = {}
        requests: list[dict[str, object]] = []

        async def handler(
            _: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            """Send unsolicited status messages and acknowledge the subscription."""
            handshake_headers.update(headers)
            websocket = await _accept_websocket(path, headers, writer)
            await _send_ws_json(writer, websocket, {"T": "success", "msg": "connected"})
            await _send_ws_json(writer, websocket, {"T": "success", "msg": "authenticated"})
            request = await _next_client_message(reader, writer, websocket)
            assert request is not None
            requests.append(request)
            await _send_ws_json(
                writer,
                websocket,
                [
                    {"T": "subscription", "news": ["*"]},
                    {"T": "n", "symbols": ["AAPL"], "author": "Desk", "headline": "Large", "summary": "A summary"},
                ],
            )

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=NEWS_TEST_API_KEY,
                        api_secret=NEWS_TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/v1beta1/news",
                        auth_mode="headers",
                    ),
                    stop_event=stop_event,
                )
                item = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert item == NewsItem(
                    symbols=("AAPL",),
                    author="Desk",
                    headline="Large",
                    summary="A summary",
                )
                assert requests == [{"action": "subscribe", "news": ["*"]}]
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)

        assert handshake_headers[b"apca-api-key-id"] == NEWS_TEST_API_KEY.encode()
        assert handshake_headers[b"apca-api-secret-key"] == NEWS_TEST_API_CREDENTIAL.encode()

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    "acknowledged",
    [[], "*", ["*", 17]],
    ids=["missing-requested-symbol", "wrong-acknowledgement-type", "non-text-symbol"],
)
def test_loopback_subscription_ack_requires_all_requested_symbols(
    acknowledged: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Reject news subscriptions with an invalid acknowledgment.

    Args:
        acknowledged: Symbols or malformed data returned in the acknowledgment.
        caplog: Fixture that captures Alpaca lifecycle logs.
    """
    caplog.set_level("INFO", logger=alpaca.__name__)

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
            await _send_ws_json(writer, websocket, {"T": "subscription", "news": acknowledged})
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server, httpx2.AsyncClient() as client:
            with pytest.raises(alpaca.AlpacaStreamError, match="incomplete news subscription"):
                await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/v1beta1/news",
                    ),
                    stop_event=asyncio.Event(),
                )

    asyncio.run(scenario())
    messages = [record.getMessage() for record in caplog.records if record.name == alpaca.__name__]
    assert "authenticated Alpaca stream" in messages
    assert "subscribed to Alpaca stream" not in messages
    assert "reconnecting Alpaca stream" not in messages


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_loopback_established_503_retries_and_401_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry an established 503 response, then terminate on a 401 response."""

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        requests: list[dict[str, object]] = []
        service_unavailable_retry_attempt = 2
        unauthorized_terminal_attempt = 4
        expected_connection_count = 4

        async def handler(
            number: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            if number == service_unavailable_retry_attempt:
                await _reject_upgrade(writer, 503)
                return
            if number == unauthorized_terminal_attempt:
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
                            {"T": "subscription", "news": ["*"]},
                            {
                                "T": "n",
                                "symbols": ["SPY"],
                                "author": "Benzinga Newsdesk",
                                "headline": "Market update",
                            },
                        ],
                    )
                    writer.write(websocket.send(CloseConnection(1012, "restart")))
                    await writer.drain()
                    return

        async with _LoopbackServer(handler) as server:
            stop_event = asyncio.Event()
            terminal: list[alpaca.AlpacaStreamError] = []
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/v1beta1/news",
                    ),
                    stop_event=stop_event,
                    on_terminated=terminal.append,
                )
                await asyncio.wait_for(stream.task, timeout=2)
                news_items = [stream.events.get_nowait(), stream.events.get_nowait()]

            assert server.paths == ["/v1beta1/news"] * 4
            assert [request["action"] for request in requests] == ["auth", "subscribe", "auth", "subscribe"]
            assert [item.symbols for item in news_items] == [("SPY",), ("SPY",)]
            assert len(terminal) == 1
            assert "HTTP 401" in str(terminal[0])
            assert server.connection_count == expected_connection_count

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize("status_code", [401, 503])
@pytest.mark.parametrize("auth_mode", ["message", "headers"], ids=["message-auth", "header-auth"])
def test_loopback_initial_upgrade_failure_remains_a_startup_failure(status_code: int, auth_mode: str) -> None:
    """Treat an initial WebSocket upgrade rejection as a startup error.

    Args:
        status_code: HTTP status returned for the rejected upgrade.
        auth_mode: Authentication method used for the WebSocket connection.
    """

    async def scenario() -> None:
        request_headers: list[dict[bytes, bytes]] = []

        async def handler(
            _: int,
            __: str,
            ___: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            request_headers.append(dict(headers))
            await _reject_upgrade(writer, status_code)

        async with _LoopbackServer(handler) as server:
            async with httpx2.AsyncClient() as client:
                with pytest.raises(alpaca.AlpacaStreamError, match=f"HTTP {status_code}") as error:
                    await alpaca.start_news_stream(
                        client,
                        settings=AlpacaSettings(
                            api_key=REDACTION_TEST_API_KEY,
                            api_secret=REDACTION_TEST_API_CREDENTIAL,
                            news_stream_url=f"{server.url}/news",
                            auth_mode=auth_mode,
                        ),
                        stop_event=asyncio.Event(),
                    )
            assert server.connection_count == 1
            assert len(request_headers) == 1
            if auth_mode == "headers":
                assert request_headers[0][b"apca-api-key-id"] == REDACTION_TEST_API_KEY.encode()
                assert request_headers[0][b"apca-api-secret-key"] == REDACTION_TEST_API_CREDENTIAL.encode()
            assert REDACTION_TEST_API_KEY not in str(error.value)
            assert REDACTION_TEST_API_CREDENTIAL not in str(error.value)

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
@pytest.mark.parametrize("auth_mode", ["message", "headers"], ids=["message-auth", "header-auth"])
def test_loopback_keepalive_disconnect_reconnects_and_resubscribes(
    monkeypatch: pytest.MonkeyPatch, auth_mode: str
) -> None:
    """Reconnect after keepalive failure and resubscribe with either auth mode."""

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        ping_seen = asyncio.Event()
        requests: list[dict[str, object]] = []
        connection_headers: list[dict[bytes, bytes]] = []
        recovered_connection_number = 2

        async def handler(
            number: int,
            path: str,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            headers: list[tuple[bytes, bytes]],
        ) -> None:
            connection_headers.append(dict(headers))
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
                                        "symbols": ["QQQ"],
                                        "author": "Benzinga Newsdesk",
                                        "headline": "Market update",
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
                        news_stream_url=f"{server.url}/v1beta1/news",
                        auth_mode=auth_mode,
                    ),
                    stop_event=stop_event,
                )
                await asyncio.wait_for(ping_seen.wait(), timeout=2)
                item = await asyncio.wait_for(stream.events.get(), timeout=2)
                assert item.symbols == ("QQQ",)
                expected_actions = {
                    "message": ["auth", "subscribe", "auth", "subscribe"],
                    "headers": ["subscribe", "subscribe"],
                }[auth_mode]
                assert [request["action"] for request in requests] == expected_actions
                assert len(connection_headers) == recovered_connection_number
                expected_credentials = {
                    "message": {},
                    "headers": {
                        b"apca-api-key-id": TEST_API_KEY.encode(),
                        b"apca-api-secret-key": TEST_API_CREDENTIAL.encode(),
                    },
                }[auth_mode]
                assert all(
                    all(headers.get(name) == value for name, value in expected_credentials.items())
                    for headers in connection_headers
                )
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reconnect and resubscribe after an injected keepalive write failure.

    Args:
        monkeypatch: Fixture used to set the keepalive and reconnect timing.
        network_error: Write error type injected into the ping operation.
        caplog: Fixture that captures Alpaca lifecycle logs.
    """
    caplog.set_level("INFO", logger=alpaca.__name__)

    async def scenario() -> None:
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_INTERVAL_SECONDS", 0.5)
        monkeypatch.setattr(alpaca, "KEEPALIVE_PING_TIMEOUT_SECONDS", 0.5)
        monkeypatch.setattr(alpaca, "RECONNECT_DELAY_SECONDS", 0)
        requests: list[dict[str, object]] = []
        connection_count = 0
        recovered_connection_count = 2

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
                    await _send_json(send, {"T": "subscription", "news": ["*"]})
                    if connection_count == recovered_connection_count:
                        await _send_json(
                            send,
                            {
                                "T": "n",
                                "symbols": ["SPY"],
                                "author": "Benzinga Newsdesk",
                                "headline": "Market update",
                            },
                        )

        grouped_failure = ExceptionGroup(
            f"ping failed while handling {REDACTION_TEST_API_KEY} {REDACTION_TEST_API_CREDENTIAL}",
            [network_error(f"injected keepalive failure {REDACTION_TEST_API_CREDENTIAL}")],
        )
        transport = _PingFailureTransport(app, {1: grouped_failure})
        stop_event = asyncio.Event()
        async with transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=TEST_API_KEY,
                    api_secret=TEST_API_CREDENTIAL,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=stop_event,
            )
            assert await asyncio.wait_for(transport.ping_failures.get(), timeout=2) == 1
            item = await asyncio.wait_for(stream.events.get(), timeout=2)
            assert item.symbols == ("SPY",)
            assert transport.connection_count == recovered_connection_count
            assert [request["action"] for request in requests[:4]] == ["auth", "subscribe", "auth", "subscribe"]
            stop_event.set()
            await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())
    warnings = [
        record
        for record in caplog.records
        if record.name == alpaca.__name__ and record.getMessage() == "reconnecting Alpaca stream"
    ]
    assert len(warnings) == 1
    assert warnings[0].attempt == EXPECTED_RECONNECT_ATTEMPT
    assert "ExceptionGroup" in warnings[0].error_summary
    assert REDACTION_TEST_API_KEY not in warnings[0].error_summary
    assert REDACTION_TEST_API_CREDENTIAL not in warnings[0].error_summary


def test_httpx2_mixed_ping_exception_group_terminates_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminate on mixed ping failures without exposing secrets in exception output."""

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
                    await _send_json(send, {"T": "subscription", "news": ["*"]})

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
        assert [request["action"] for request in requests] == ["auth", "subscribe"]
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
def test_loopback_stop_cancels_reconnect_backoff() -> None:
    """Cancel reconnect backoff promptly when shutdown is requested."""

    async def scenario() -> None:
        backoff_wait_count = 2

        class ObservedStopEvent(asyncio.Event):
            def __init__(self) -> None:
                super().__init__()
                self.backoff_started = asyncio.Event()
                self.wait_count = 0

            async def wait(self) -> bool:
                self.wait_count += 1
                if self.wait_count >= backoff_wait_count:
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
            await _send_ws_json(writer, websocket, {"T": "subscription", "news": ["*"]})
            writer.write(websocket.send(CloseConnection(1012, "restart")))
            await writer.drain()
            await _next_client_message(reader, writer, websocket)

        async with _LoopbackServer(handler) as server:
            stop_event = ObservedStopEvent()
            async with httpx2.AsyncClient() as client:
                stream = await alpaca.start_news_stream(
                    client,
                    settings=AlpacaSettings(
                        api_key=TEST_API_KEY,
                        api_secret=TEST_API_CREDENTIAL,
                        news_stream_url=f"{server.url}/v1beta1/news",
                    ),
                    stop_event=stop_event,
                )
                await asyncio.wait_for(stop_event.backoff_started.wait(), timeout=2)
                stop_event.set()
                await asyncio.wait_for(stream.task, timeout=2)
                assert server.connection_count == 1

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


def test_decoded_stream_messages_log_safe_metadata_once_before_parsing(caplog: pytest.LogCaptureFixture) -> None:
    """Log safe metadata for handshake, pending, mixed-source, and ignored messages once."""
    caplog.set_level("INFO", logger=alpaca.__name__)
    api_key = "diagnostic-key"
    api_credential = "diagnostic-secret"
    private_body = "private-body-content"
    private_url = "https://private.example/news"
    private_provider_text = "private-provider-message"

    async def scenario() -> None:
        """Run a local Alpaca handshake followed by mixed news and ignored messages."""

        async def app(
            scope: dict[str, object],
            receive: Callable[..., Awaitable[dict[str, object]]],
            send: Callable[..., Awaitable[None]],
        ) -> None:
            """Send staged and stream messages, then wait for client shutdown."""
            await receive()
            await send({"type": "websocket.accept"})
            while True:
                incoming = await receive()
                if incoming["type"] == "websocket.disconnect":
                    return
                request = json.loads(incoming["text"])
                if request["action"] == "auth":
                    await _send_json(
                        send,
                        [
                            {
                                "T": f"auth-{api_key}",
                                "id": True,
                                "source": api_key,
                                "author": api_credential,
                                "msg": private_provider_text,
                                "key": api_key,
                                "secret": api_credential,
                            },
                            {"T": "success", "msg": "authenticated"},
                        ],
                    )
                    continue
                await _send_json(
                    send,
                    [
                        {
                            "T": "n",
                            "id": True,
                            "symbols": ["PEND"],
                            "source": "pending-source",
                            "author": "Pending Desk",
                            "headline": private_body,
                        },
                        {
                            "T": "t" * 60 + api_credential,
                            "id": 42,
                            "source": None,
                            "author": {"private": api_key},
                            "msg": private_provider_text,
                        },
                        {"T": "subscription", "news": ["*"]},
                    ],
                )
                await _send_json(
                    send,
                    [
                        {
                            "T": "n",
                            "id": 7,
                            "symbols": ["BZFD"],
                            "source": "s" * 60 + api_key,
                            "author": "a" * 60 + api_credential,
                            "headline": private_body,
                            "summary": private_body,
                            "content": private_body,
                            "url": private_url,
                            "key": api_key,
                            "secret": api_credential,
                            "msg": private_provider_text,
                        },
                        {
                            "T": "n",
                            "id": "article-id-is-not-an-int",
                            "symbols": ["FISC"],
                            "source": "fiscal.ai",
                            "author": "Fiscal Desk",
                            "headline": private_body,
                        },
                        {"T": {"private": api_credential}, "id": 12, "source": 9, "author": False},
                    ],
                )

        stop_event = asyncio.Event()
        async with ASGIWebSocketTransport(app) as transport, httpx2.AsyncClient(transport=transport) as client:
            stream = await alpaca.start_news_stream(
                client,
                settings=AlpacaSettings(
                    api_key=api_key,
                    api_secret=api_credential,
                    news_stream_url="ws://alpaca.test/v1beta1/news",
                ),
                stop_event=stop_event,
            )
            items = [await asyncio.wait_for(stream.events.get(), timeout=2) for _ in range(3)]
            assert [item.source for item in items] == ["pending-source", "s" * 60 + api_key, "fiscal.ai"]
            stop_event.set()
            await asyncio.wait_for(stream.task, timeout=2)

    asyncio.run(scenario())

    records = [
        record
        for record in caplog.records
        if record.name == alpaca.__name__ and record.getMessage() == "received Alpaca stream message"
    ]
    assert [
        (record.stream, record.stage, record.message_type, record.article_id, record.source, record.author)
        for record in records
    ] == [
        ("news", "authentication", f"auth-{api_key}".replace(api_key, "[redacted]"), None, "[redacted]", "[redacted]"),
        ("news", "authentication", "success", None, "", ""),
        ("news", "subscription", "n", None, "pending-source", "Pending Desk"),
        ("news", "subscription", "t" * 60 + "[red", 42, "", ""),
        ("news", "subscription", "subscription", None, "", ""),
        ("news", "stream", "n", 7, "s" * 60 + "[red", "a" * 60 + "[red"),
        ("news", "stream", "n", None, "fiscal.ai", "Fiscal Desk"),
        ("news", "stream", "", 12, "", ""),
    ]
    standard_fields = set(logging.makeLogRecord({}).__dict__) | {"message"}
    safe_fields = {"stream", "stage", "message_type", "article_id", "source", "author"}
    for record in records:
        assert set(record.__dict__) - standard_fields == safe_fields
        assert all(
            len(getattr(record, field)) <= EXPECTED_DIAGNOSTIC_TEXT_LIMIT
            for field in ("message_type", "source", "author")
        )
    captured = "\n".join(repr(record.__dict__) for record in records)
    for private_value in (api_key, api_credential, private_body, private_provider_text, private_url):
        assert private_value not in captured
