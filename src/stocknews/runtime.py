"""Coordinate Alpaca stream events and sequential Discord delivery."""

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from html import unescape
from typing import cast

import httpx2

from stocknews.discord import (
    WebhookPayload,
    analyst_payload,
    earnings_payload,
    news_payload,
    send_payload,
)
from stocknews.models import Config, NewsItem, Trade
from stocknews.news import classify_news

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
            except Exception as error:
                logger.warning(
                    "failed to send Discord webhook",
                    extra={"error": str(error), "kind": job.kind, "symbol": job.symbol},
                )
        finally:
            jobs.task_done()


async def _settle_task(task: asyncio.Task[object]) -> bool:
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            interrupted = interrupted or (current is not None and current.cancelling() > 0)
            continue
        except Exception:
            break
    try:
        task.result()
    except BaseException:
        pass
    return interrupted


async def _close_iterator[T](stream: AsyncIterator[T]) -> None:
    close = getattr(stream, "aclose", None)
    if callable(close):
        await cast(Awaitable[None], close())


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
    jobs: asyncio.Queue[_Delivery | None] = asyncio.Queue(maxsize=DELIVERY_QUEUE_CAPACITY)
    worker: asyncio.Task[None] | None = None
    stock_connection: asyncio.Task[AsyncIterable[Trade]] | None = None
    news_iterator: AsyncIterator[NewsItem] | None = None
    stock_iterator: AsyncIterator[Trade] | None = None
    news_read: asyncio.Task[NewsItem | _StreamEnded] | None = None
    stock_read: asyncio.Task[Trade | _StreamEnded] | None = None
    normal_completion = False

    if stock_stream is not None:
        stock_factory = stock_stream

        async def connect_stock() -> AsyncIterable[Trade]:
            return await stock_factory()

        stock_connection = asyncio.create_task(connect_stock())

    try:
        try:
            connected_news = await news_stream()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise RuntimeError(f"connect Alpaca news stream: {error}") from error

        news_iterator = aiter(connected_news)
        news_read = asyncio.create_task(_next(news_iterator))
        worker = asyncio.create_task(_deliver(client, logger, jobs))

        while True:
            assert news_read is not None
            waiting: set[asyncio.Task[object]] = {cast(asyncio.Task[object], news_read)}
            if stock_read is not None:
                waiting.add(cast(asyncio.Task[object], stock_read))
            if stock_connection is not None:
                waiting.add(cast(asyncio.Task[object], stock_connection))
            completed, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

            if stock_connection is not None and stock_connection in completed:
                connection = stock_connection
                stock_connection = None
                try:
                    connected_stock = connection.result()
                except Exception as error:
                    logger.warning(
                        "failed to connect Alpaca stock stream",
                        extra={"error": str(error)},
                    )
                else:
                    stock_iterator = aiter(connected_stock)
                    stock_read = asyncio.create_task(_next(stock_iterator))

            if news_read in completed:
                try:
                    message = news_read.result()
                except Exception as error:
                    raise RuntimeError(f"alpaca news stream terminated: {error}") from error
                if isinstance(message, _StreamEnded):
                    normal_completion = True
                    return
                delivery = _make_delivery(message, config, logger)
                if delivery is not None:
                    try:
                        jobs.put_nowait(delivery)
                    except asyncio.QueueFull as error:
                        raise RuntimeError("Discord delivery queue is full") from error
                assert news_iterator is not None
                news_read = asyncio.create_task(_next(news_iterator))

            if stock_read is not None and stock_read in completed:
                try:
                    trade_message = stock_read.result()
                except Exception as error:
                    raise RuntimeError(f"alpaca stock stream terminated: {error}") from error
                if isinstance(trade_message, _StreamEnded):
                    normal_completion = True
                    return
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
                assert stock_iterator is not None
                stock_read = asyncio.create_task(_next(stock_iterator))
    except asyncio.CancelledError:
        raise
    finally:
        interrupted_during_cleanup = False
        worker_cancelled = False
        if worker is not None and not normal_completion:
            worker.cancel()
            worker_cancelled = True
            interrupted_during_cleanup |= await _settle_task(cast(asyncio.Task[object], worker))

        pending_tasks = [
            cast(asyncio.Task[object], task) for task in (news_read, stock_read, stock_connection) if task is not None
        ]
        for task in pending_tasks:
            if not task.done():
                task.cancel()
        for task in pending_tasks:
            interrupted_during_cleanup |= await _settle_task(task)

        iterator_close_tasks = [
            asyncio.create_task(_close_iterator(iterator))
            for iterator in (news_iterator, stock_iterator)
            if iterator is not None
        ]
        for task in iterator_close_tasks:
            interrupted_during_cleanup |= await _settle_task(cast(asyncio.Task[object], task))

        if worker is not None:
            if normal_completion and not interrupted_during_cleanup:
                try:
                    await jobs.put(None)
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    if not worker_cancelled:
                        worker.cancel()
                        worker_cancelled = True
                    await _settle_task(cast(asyncio.Task[object], worker))
                    raise
            else:
                if not worker_cancelled:
                    worker.cancel()
                    worker_cancelled = True
                interrupted_during_cleanup |= await _settle_task(cast(asyncio.Task[object], worker))
        if interrupted_during_cleanup:
            raise asyncio.CancelledError
