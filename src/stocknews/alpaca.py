"""Async WebSocket adapters for Alpaca news and stock trades."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

import anyio
import httpcore2
import httpx2
from httpx2.websockets import (
    AsyncWebSocketSession,
    HTTPXWSException,
    WebSocketDisconnect,
    WebSocketNetworkError,
    WebSocketUpgradeError,
)

from .models import AlpacaSettings, NewsItem, Trade

__all__ = [
    "AlpacaStreamError",
    "StreamHandle",
    "start_news_stream",
    "start_trade_stream",
]

QUEUE_CAPACITY = 256
RECONNECT_DELAY_SECONDS = 5
ACK_TIMEOUT_SECONDS = 10
KEEPALIVE_PING_INTERVAL_SECONDS = 20
KEEPALIVE_PING_TIMEOUT_SECONDS = 20
MAX_MESSAGE_SIZE_BYTES = 16 * 1024 * 1024
UINT32_MAX = 2**32 - 1
WEBSOCKET_MESSAGE_TOO_BIG_CLOSE_CODE = 1009
HTTP_SERVER_ERROR_STATUS_CODE = 500

EventT = TypeVar("EventT")


@dataclass(frozen=True, slots=True)
class _StreamConfig(Generic[EventT]):
    client: httpx2.AsyncClient
    url: str
    api_key: str
    api_secret: str
    stream_name: str
    channel: str
    symbols: tuple[str, ...]
    parse_event: Callable[[dict[str, object]], EventT | None]


class AlpacaStreamError(RuntimeError):
    """Raised when Alpaca rejects or cannot establish a market data stream."""


@dataclass(slots=True)
class StreamHandle(Generic[EventT]):
    """An active stream's bounded event queue and background task."""

    events: asyncio.Queue[EventT]
    task: asyncio.Task[None]


class _Stopped:
    pass


_STOPPED = _Stopped()


@dataclass(frozen=True, slots=True)
class _TransportFailure:
    error: Exception


@dataclass(frozen=True, slots=True)
class _TerminalFailure:
    error: AlpacaStreamError


async def start_news_stream(
    client: httpx2.AsyncClient,
    *,
    settings: AlpacaSettings,
    stop_event: asyncio.Event,
    on_terminated: Callable[[AlpacaStreamError], None] | None = None,
) -> StreamHandle[NewsItem]:
    """Start the news stream and return after its first successful subscription.

    Initial connection, authentication, and subscription failures are raised by
    this function. Later non-retryable failures are passed to ``on_terminated``;
    without a callback they are raised by the returned task.
    """
    return await _start_stream(
        _StreamConfig(
            client,
            settings.news_stream_url,
            settings.api_key,
            settings.api_secret,
            "news",
            "news",
            ("*",),
            _parse_news,
        ),
        stop_event,
        on_terminated=on_terminated,
    )


async def start_trade_stream(
    client: httpx2.AsyncClient,
    *,
    settings: AlpacaSettings,
    stop_event: asyncio.Event,
    on_terminated: Callable[[AlpacaStreamError], None] | None = None,
) -> StreamHandle[Trade]:
    """Start the IEX trades stream and return after its first subscription.

    ``settings.stock_stream_url`` is Alpaca's configured stock stream URL,
    normally ending in ``/v2``. The IEX feed path is appended as the Go SDK does.
    """
    return await _start_stream(
        _StreamConfig(
            client,
            f"{settings.stock_stream_url.rstrip('/')}/iex",
            settings.api_key,
            settings.api_secret,
            "trades",
            "trades",
            ("SPY", "QQQ"),
            _parse_trade,
        ),
        stop_event,
        on_terminated=on_terminated,
    )


async def _start_stream(
    config: _StreamConfig[EventT],
    stop_event: asyncio.Event,
    on_terminated: Callable[[AlpacaStreamError], None] | None,
) -> StreamHandle[EventT]:
    events: asyncio.Queue[EventT] = asyncio.Queue(maxsize=QUEUE_CAPACITY)
    started: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
        _manage_stream(
            config,
            stop_event,
            events,
            started,
            on_terminated,
        ),
        name=f"alpaca-{config.stream_name}-stream",
    )
    try:
        await started
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    return StreamHandle(events=events, task=task)


async def _manage_stream(
    config: _StreamConfig[EventT],
    stop_event: asyncio.Event,
    events: asyncio.Queue[EventT],
    started: asyncio.Future[None],
    on_terminated: Callable[[AlpacaStreamError], None] | None,
) -> None:
    stream_task = asyncio.create_task(
        _run_stream(
            config,
            stop_event,
            events,
            started,
            on_terminated,
        ),
        name=f"alpaca-{config.stream_name}-connection-loop",
    )
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait((stream_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
        if stream_task in done:
            await stream_task
            return
        if stop_task in done:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)
            return
    except asyncio.CancelledError:
        stream_task.cancel()
        await asyncio.gather(stream_task, return_exceptions=True)
        raise
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)


async def _run_stream(
    config: _StreamConfig[EventT],
    stop_event: asyncio.Event,
    events: asyncio.Queue[EventT],
    started: asyncio.Future[None],
    on_terminated: Callable[[AlpacaStreamError], None] | None,
) -> None:
    try:
        while not stop_event.is_set():
            outcome = await _read_connection(
                config,
                stop_event,
                events,
                started,
            )
            if outcome is _STOPPED:
                break
            if isinstance(outcome, _TransportFailure):
                if not started.done():
                    started.set_exception(
                        _stream_failure(
                            config.stream_name,
                            "initial connection",
                            outcome.error,
                            config.api_key,
                            config.api_secret,
                        )
                    )
                    return
                if await _wait_before_reconnect(stop_event):
                    return
                continue
            if isinstance(outcome, _TerminalFailure):
                if not started.done():
                    started.set_exception(outcome.error)
                    return
                if on_terminated is None:
                    raise outcome.error
                on_terminated(outcome.error)
                return
    except asyncio.CancelledError:
        if not started.done():
            started.set_exception(AlpacaStreamError(f"Alpaca {config.stream_name} stream stopped before subscription"))
        raise

    if not started.done():
        started.set_exception(AlpacaStreamError(f"Alpaca {config.stream_name} stream stopped before subscription"))


async def _read_connection(
    config: _StreamConfig[EventT],
    stop_event: asyncio.Event,
    events: asyncio.Queue[EventT],
    started: asyncio.Future[None],
) -> _Stopped | _TransportFailure | _TerminalFailure:
    try:
        async with config.client.websocket(
            config.url,
            max_message_size_bytes=MAX_MESSAGE_SIZE_BYTES,
            keepalive_ping_interval_seconds=KEEPALIVE_PING_INTERVAL_SECONDS,
            keepalive_ping_timeout_seconds=KEEPALIVE_PING_TIMEOUT_SECONDS,
        ) as websocket:
            # Catch expected failures before leaving the context so HTTPX2's
            # task group does not wrap them in an ExceptionGroup.
            try:
                pending_messages = await _authenticate_and_subscribe(config, websocket, stop_event)
                if stop_event.is_set():
                    return _STOPPED
                if not started.done():
                    started.set_result(None)
                await _read_events(config, websocket, stop_event, events, pending_messages)
            except Exception as error:
                return _failure_outcome(config, "stream protocol", error, started.done())
            else:
                return _STOPPED
    except Exception as error:
        return _failure_outcome(config, "connection", error, started.done())


async def _authenticate_and_subscribe(
    config: _StreamConfig[EventT],
    websocket: AsyncWebSocketSession,
    stop_event: asyncio.Event,
) -> list[dict[str, object]]:
    await websocket.send_json({"action": "auth", "key": config.api_key, "secret": config.api_secret})
    pending_messages = await _wait_for_ack(config, websocket, stop_event, "authentication")
    if pending_messages is None:
        return []
    await websocket.send_json({"action": "subscribe", config.channel: config.symbols})
    subscription_messages = await _wait_for_ack(config, websocket, stop_event, "subscription")
    if subscription_messages is not None:
        pending_messages.extend(subscription_messages)
    return pending_messages


async def _wait_for_ack(
    config: _StreamConfig[EventT],
    websocket: AsyncWebSocketSession,
    stop_event: asyncio.Event,
    stage: str,
) -> list[dict[str, object]] | None:
    deadline = asyncio.get_running_loop().time() + ACK_TIMEOUT_SECONDS
    timeout_message = f"Alpaca {config.stream_name} stream timed out waiting for {stage} acknowledgement"
    while not stop_event.is_set():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AlpacaStreamError(timeout_message)
        try:
            payload = await websocket.receive_json(timeout=remaining)
        except TimeoutError as error:
            raise AlpacaStreamError(timeout_message) from error
        messages = _message_objects(payload)
        for index, message in enumerate(messages):
            message_type = message.get("T")
            if message_type == "error":
                raise _server_error(config.stream_name, stage, message, config.api_key, config.api_secret)
            if stage == "authentication" and message_type == "success":
                response = message.get("msg")
                if isinstance(response, str) and response.casefold() == "authenticated":
                    return messages[:index] + messages[index + 1 :]
            if stage == "subscription" and message_type == "subscription":
                subscriptions = message.get("streams")
                if not isinstance(subscriptions, dict):
                    subscriptions = message
                acknowledged = subscriptions.get(config.channel)
                if isinstance(acknowledged, list) and all(isinstance(symbol, str) for symbol in acknowledged):
                    if set(config.symbols).issubset(acknowledged):
                        return messages[:index] + messages[index + 1 :]
                detail = f"Alpaca {config.stream_name} stream acknowledged an incomplete {config.channel} subscription"
                raise AlpacaStreamError(detail)
    return None


async def _read_events(
    config: _StreamConfig[EventT],
    websocket: AsyncWebSocketSession,
    stop_event: asyncio.Event,
    events: asyncio.Queue[EventT],
    pending_messages: list[dict[str, object]],
) -> None:
    while not stop_event.is_set():
        if pending_messages:
            messages, pending_messages = pending_messages, []
        else:
            try:
                payload = await websocket.receive_json(timeout=None)
            except json.JSONDecodeError as error:
                error_message = f"Alpaca {config.stream_name} stream received invalid JSON"
                raise AlpacaStreamError(error_message) from error
            messages = _message_objects(payload)
        for message in messages:
            if message.get("T") == "error":
                raise _server_error(config.stream_name, "stream", message, config.api_key, config.api_secret)
            event = config.parse_event(message)
            if event is not None:
                await events.put(event)


def _failure_outcome(
    config: _StreamConfig[EventT],
    stage: str,
    error: Exception,
    established: bool,
) -> _TransportFailure | _TerminalFailure:
    if _is_retryable_transport_error(error, established):
        return _TransportFailure(error)
    terminal_error = _stream_failure(config.stream_name, stage, error, config.api_key, config.api_secret)
    return _TerminalFailure(terminal_error)


def _is_retryable_transport_error(error: BaseException, established: bool) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(
            _is_retryable_transport_error(child, established) for child in error.exceptions
        )
    if isinstance(error, WebSocketDisconnect):
        return error.code != WEBSOCKET_MESSAGE_TOO_BIG_CLOSE_CODE
    if isinstance(error, WebSocketUpgradeError):
        return established and error.response.status_code >= HTTP_SERVER_ERROR_STATUS_CODE
    return isinstance(
        error,
        (
            WebSocketNetworkError,
            httpx2.TransportError,
            httpcore2.NetworkError,
            httpcore2.TimeoutException,
            anyio.EndOfStream,
            TimeoutError,
        ),
    )


def _message_objects(payload: object) -> list[dict[str, object]]:
    values = payload if isinstance(payload, list) else [payload]
    messages: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            message = "Alpaca stream sent a message with an invalid shape"
            raise AlpacaStreamError(message)
        messages.append(cast("dict[str, object]", value))
    return messages


def _parse_news(message: dict[str, object]) -> NewsItem | None:
    if message.get("T") != "n":
        return None
    return NewsItem(
        symbols=_text_tuple(message, "symbols", "news"),
        author=_text(message, "author", "news"),
        headline=_text(message, "headline", "news"),
        summary=_text(message, "summary", "news"),
        url=_text(message, "url", "news"),
    )


def _parse_trade(message: dict[str, object]) -> Trade | None:
    if message.get("T") != "t":
        return None
    price = message.get("p", 0)
    size = message.get("s", 0)
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
        error_message = "Alpaca trades stream sent an invalid price"
        raise AlpacaStreamError(error_message)
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= UINT32_MAX:
        error_message = "Alpaca trades stream sent an invalid size"
        raise AlpacaStreamError(error_message)
    return Trade(
        symbol=_text(message, "S", "trades"),
        price=float(price),
        size=float(size),
        exchange=_text(message, "x", "trades"),
        timestamp=_text(message, "t", "trades"),
        conditions=_text_tuple(message, "c", "trades"),
        tape=_text(message, "z", "trades"),
    )


def _text(message: dict[str, object], field: str, stream_name: str) -> str:
    value = message.get(field, "")
    if not isinstance(value, str):
        error_message = f"Alpaca {stream_name} stream sent an invalid {field} field"
        raise AlpacaStreamError(error_message)
    return value


def _text_tuple(message: dict[str, object], field: str, stream_name: str) -> tuple[str, ...]:
    value = message.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        error_message = f"Alpaca {stream_name} stream sent an invalid {field} field"
        raise AlpacaStreamError(error_message)
    return tuple(value)


def _server_error(
    stream_name: str,
    stage: str,
    message: dict[str, object],
    api_key: str,
    api_secret: str,
) -> AlpacaStreamError:
    response = message.get("msg")
    detail = response if isinstance(response, str) and response else "server rejected the request"
    code = message.get("code")
    if isinstance(code, (str, int)) and not isinstance(code, bool):
        detail = f"{detail} (code {code})"
    return AlpacaStreamError(f"Alpaca {stream_name} stream {stage} failed: {_redact(detail, api_key, api_secret)}")


def _stream_failure(
    stream_name: str,
    stage: str,
    error: Exception,
    api_key: str = "",
    api_secret: str = "",
) -> AlpacaStreamError:
    if isinstance(error, AlpacaStreamError):
        return error
    if isinstance(error, WebSocketUpgradeError):
        detail = f"WebSocket upgrade rejected with HTTP {error.response.status_code}"
    elif isinstance(error, WebSocketDisconnect):
        if error.code == WEBSOCKET_MESSAGE_TOO_BIG_CLOSE_CODE:
            detail = f"WebSocket message exceeded the configured maximum size (code {error.code})"
        else:
            reason = _redact(error.reason, api_key, api_secret)
            detail = f"WebSocket disconnected with code {error.code}"
            if reason:
                detail = f"{detail}: {reason}"
    elif isinstance(error, WebSocketNetworkError):
        detail = "WebSocket network error"
    elif isinstance(error, httpx2.TransportError):
        detail = f"network transport error ({type(error).__name__})"
    elif isinstance(error, HTTPXWSException):
        detail = f"WebSocket protocol error ({type(error).__name__})"
    elif isinstance(error, json.JSONDecodeError):
        detail = "invalid JSON received"
    else:
        detail = f"unexpected error ({type(error).__name__})"
    return AlpacaStreamError(f"Alpaca {stream_name} stream {stage} failed: {detail}")


def _redact(value: str, api_key: str, api_secret: str) -> str:
    for credential in (api_key, api_secret):
        if credential:
            value = value.replace(credential, "[redacted]")
    return value


async def _wait_before_reconnect(stop_event: asyncio.Event) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=RECONNECT_DELAY_SECONDS)
    except TimeoutError:
        return False
    return True
