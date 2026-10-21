"""Coordinate Alpaca stream events and sequential Discord delivery."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from html import unescape
from typing import TYPE_CHECKING, cast

from stocknews.discord import (
    WebhookPayload,
    analyst_payload,
    earnings_payload,
    news_payload,
    send_payload,
)
from stocknews.news import classify_news

if TYPE_CHECKING:
    import logging

    import httpx2

    from stocknews.models import Config, NewsItem, Trade

DELIVERY_QUEUE_CAPACITY = 16

type StreamFactory[T] = Callable[[], Awaitable[AsyncIterable[T]]]


@dataclass(frozen=True, slots=True)
class _Delivery:
    webhooks: tuple[str, ...]
    payload: WebhookPayload
    kind: str
    symbol: str


@dataclass(frozen=True, slots=True)
class _StreamEnded:
    pass


_STREAM_ENDED = _StreamEnded()


@dataclass(slots=True)
class _RunState:
    jobs: asyncio.Queue[_Delivery | None]
    worker: asyncio.Task[None] | None = None
    stock_connection: asyncio.Task[AsyncIterable[Trade]] | None = None
    news_iterator: AsyncIterator[NewsItem] | None = None
    stock_iterator: AsyncIterator[Trade] | None = None
    news_read: asyncio.Task[NewsItem | _StreamEnded] | None = None
    stock_read: asyncio.Task[Trade | _StreamEnded] | None = None
    worker_cancelled: bool = False

    def cancel_worker(self) -> None:
        if self.worker is not None and not self.worker_cancelled:
            self.worker.cancel()
            self.worker_cancelled = True


async def _next[T](stream: AsyncIterator[T]) -> T | _StreamEnded:
    try:
        return await anext(stream)
    except StopAsyncIteration:
        return _STREAM_ENDED


def _make_delivery(item: NewsItem, config: Config, logger: logging.Logger) -> _Delivery | None:
    classification = classify_news(item, config.blocked_phrases)
    if classification not in {"earnings", "analyst", "news"}:
        logger.info(
            "skipping news item",
            extra={
                "reason": classification,
                "headline": unescape(item.headline),
                "author": item.author,
                "symbols": item.symbols,
            },
        )
        return None

    symbol = item.symbols[0].strip()
    headline = unescape(item.headline)
    if classification == "earnings":
        payload = earnings_payload(symbol, headline, config.stock_logo, config.transparent_png)
        webhooks = config.discord_earnings_webhooks
    elif classification == "analyst":
        payload = analyst_payload(symbol, headline, config.stock_logo, config.transparent_png)
        webhooks = config.discord_analyst_webhooks
    else:
        # General-news formatting uses the original symbol, matching the Go runtime.
        payload = news_payload(replace(item, headline=headline), config.stock_logo, config.transparent_png)
        webhooks = config.discord_news_webhooks

    if payload is None:
        logger.info(
            "skipping news item",
            extra={
                "reason": "no_payload",
                "kind": classification,
                "headline": headline,
                "author": item.author,
                "symbols": item.symbols,
            },
        )
        return None
    return _Delivery(webhooks, payload, classification, symbol)


async def _deliver(
    client: httpx2.AsyncClient,
    logger: logging.Logger,
    jobs: asyncio.Queue[_Delivery | None],
) -> None:
    while True:
        job = await jobs.get()
        try:
            if job is None:
                return
            try:
                await send_payload(client, job.webhooks, job.payload)
            # Keep per-webhook failures local; cancellation still propagates.
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    "failed to send Discord webhook",
                    extra={"error": str(error), "kind": job.kind, "symbol": job.symbol},
                )
        finally:
            jobs.task_done()


async def _settle_task(
    task: asyncio.Task[object],
    *,
    on_interruption: Callable[[], None] | None = None,
) -> tuple[bool, Exception | None]:
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            cancelled = current is not None and current.cancelling() > 0
            interrupted = interrupted or cancelled
            if cancelled and on_interruption is not None:
                on_interruption()
            continue
        # Preserve mixed BaseExceptionGroups while draining ordinary task errors.
        except Exception:  # noqa: BLE001
            break
    try:
        task_error = task.exception()
    except asyncio.CancelledError:
        return interrupted, None
    return interrupted, task_error if isinstance(task_error, Exception) else None


async def _close_iterator[T](stream: AsyncIterator[T]) -> None:
    close = getattr(stream, "aclose", None)
    if callable(close):
        await cast("Awaitable[None]", close())


def _accept_stock_connection(state: _RunState, completed: set[asyncio.Task[object]], logger: logging.Logger) -> None:
    if state.stock_connection is None or cast("asyncio.Task[object]", state.stock_connection) not in completed:
        return

    connection = state.stock_connection
    state.stock_connection = None
    if connection.cancelled():
        connection.result()
    connection_error = connection.exception()
    if isinstance(connection_error, Exception):
        logger.warning(
            "failed to connect Alpaca stock stream",
            extra={"error": str(connection_error)},
        )
        return

    connected_stock = connection.result()
    state.stock_iterator = aiter(connected_stock)
    state.stock_read = asyncio.create_task(_next(state.stock_iterator))


def _process_news_read(
    state: _RunState,
    completed: set[asyncio.Task[object]],
    config: Config,
    logger: logging.Logger,
) -> bool:
    if state.news_read is None or cast("asyncio.Task[object]", state.news_read) not in completed:
        return False

    try:
        message = state.news_read.result()
    except Exception as error:
        error_message = f"alpaca news stream terminated: {error}"
        raise RuntimeError(error_message) from error
    if isinstance(message, _StreamEnded):
        return True

    delivery = _make_delivery(message, config, logger)
    if delivery is not None:
        try:
            state.jobs.put_nowait(delivery)
        except asyncio.QueueFull as error:
            error_message = "Discord delivery queue is full"
            raise RuntimeError(error_message) from error
    if state.news_iterator is None:
        error_message = "news stream read completed without an active iterator"
        raise RuntimeError(error_message)
    state.news_read = asyncio.create_task(_next(state.news_iterator))
    return False


def _process_stock_read(
    state: _RunState,
    completed: set[asyncio.Task[object]],
    logger: logging.Logger,
) -> bool:
    if state.stock_read is None or cast("asyncio.Task[object]", state.stock_read) not in completed:
        return False

    try:
        trade_message = state.stock_read.result()
    except Exception as error:
        error_message = f"alpaca stock stream terminated: {error}"
        raise RuntimeError(error_message) from error
    if isinstance(trade_message, _StreamEnded):
        return True

    logger.info(
        "stock trade",
        extra={
            "symbol": trade_message.symbol,
            "price": trade_message.price,
            "size": trade_message.size,
            "exchange": trade_message.exchange,
            "timestamp": trade_message.timestamp,
            "conditions": trade_message.conditions,
            "tape": trade_message.tape,
        },
    )
    if state.stock_iterator is None:
        error_message = "stock stream read completed without an active iterator"
        raise RuntimeError(error_message)
    state.stock_read = asyncio.create_task(_next(state.stock_iterator))
    return False


async def _pump_streams(state: _RunState, config: Config, logger: logging.Logger) -> None:
    while True:
        if state.news_read is None:
            error_message = "news stream read task is unavailable while pumping streams"
            raise RuntimeError(error_message)
        waiting: set[asyncio.Task[object]] = {cast("asyncio.Task[object]", state.news_read)}
        if state.stock_read is not None:
            waiting.add(cast("asyncio.Task[object]", state.stock_read))
        if state.stock_connection is not None:
            waiting.add(cast("asyncio.Task[object]", state.stock_connection))
        completed, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

        _accept_stock_connection(state, completed, logger)
        if _process_news_read(state, completed, config, logger):
            return
        if _process_stock_read(state, completed, logger):
            return


async def _close_streams(state: _RunState, logger: logging.Logger) -> bool:
    pending_tasks = [
        (stream_name, cast("asyncio.Task[object]", task))
        for stream_name, task in (
            ("news", state.news_read),
            ("stock", state.stock_read),
            ("stock", state.stock_connection),
        )
        if task is not None
    ]
    cancelled_tasks: set[asyncio.Task[object]] = set()
    for _, task in pending_tasks:
        if not task.done() and task.cancel():
            cancelled_tasks.add(task)

    interrupted_during_cleanup = False
    for stream_name, task in pending_tasks:
        interrupted, cleanup_error = await _settle_task(task, on_interruption=state.cancel_worker)
        interrupted_during_cleanup |= interrupted
        if cleanup_error is not None and task in cancelled_tasks:
            logger.warning(
                "failed to close Alpaca %s stream",
                stream_name,
                extra={"error": type(cleanup_error).__name__, "stream": stream_name},
            )

    iterator_close_tasks = [
        (stream_name, asyncio.create_task(_close_iterator(iterator)))
        for stream_name, iterator in (("news", state.news_iterator), ("stock", state.stock_iterator))
        if iterator is not None
    ]
    for stream_name, task in iterator_close_tasks:
        interrupted, close_error = await _settle_task(task, on_interruption=state.cancel_worker)
        interrupted_during_cleanup |= interrupted
        if close_error is not None:
            logger.warning(
                "failed to close Alpaca %s stream",
                stream_name,
                extra={"error": type(close_error).__name__, "stream": stream_name},
            )
    return interrupted_during_cleanup


async def _shutdown(state: _RunState, logger: logging.Logger, *, normal_completion: bool) -> None:
    interrupted_during_cleanup = False
    if state.worker is not None and not normal_completion:
        state.cancel_worker()
        interrupted_during_cleanup, _ = await _settle_task(cast("asyncio.Task[object]", state.worker))

    interrupted_during_cleanup |= await _close_streams(state, logger)

    if state.worker is not None and normal_completion and not interrupted_during_cleanup:
        try:
            await state.jobs.put(None)
            await asyncio.shield(state.worker)
        except asyncio.CancelledError:
            state.cancel_worker()
            await _settle_task(cast("asyncio.Task[object]", state.worker))
            raise
    elif state.worker is not None and interrupted_during_cleanup:
        state.cancel_worker()
        interrupted, _ = await _settle_task(
            cast("asyncio.Task[object]", state.worker),
            on_interruption=state.cancel_worker,
        )
        interrupted_during_cleanup |= interrupted

    if interrupted_during_cleanup:
        raise asyncio.CancelledError


async def run(
    config: Config,
    client: httpx2.AsyncClient,
    logger: logging.Logger,
    news_stream: StreamFactory[NewsItem],
    stock_stream: StreamFactory[Trade] | None = None,
) -> None:
    """Run connected stream factories and drain Discord jobs on normal shutdown.

    A stream factory connects and returns an async iterable. Exceptions raised by
    a stream factory indicate a connection error; iteration errors indicate an
    established stream failure.
    """
    state = _RunState(asyncio.Queue(maxsize=DELIVERY_QUEUE_CAPACITY))
    normal_completion = False

    if stock_stream is not None:
        stock_factory = stock_stream

        async def connect_stock() -> AsyncIterable[Trade]:
            return await stock_factory()

        state.stock_connection = asyncio.create_task(connect_stock())

    try:
        try:
            connected_news = await news_stream()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            error_message = f"connect Alpaca news stream: {error}"
            raise RuntimeError(error_message) from error

        state.news_iterator = aiter(connected_news)
        state.news_read = asyncio.create_task(_next(state.news_iterator))
        state.worker = asyncio.create_task(_deliver(client, logger, state.jobs))

        await _pump_streams(state, config, logger)
        normal_completion = True
    finally:
        await _shutdown(state, logger, normal_completion=normal_completion)
