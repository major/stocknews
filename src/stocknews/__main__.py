"""Start the stocknews process."""

import asyncio
import logging
import os
import signal
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, cast

import httpx2

from stocknews.alpaca import (
    AlpacaStreamError,
    StreamHandle,
    start_news_stream,
    start_trade_stream,
)
from stocknews.config import load_config
from stocknews.logging import configure_logging
from stocknews.models import AlpacaSettings, Config, NewsItem, Trade
from stocknews.runtime import run

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Awaitable, Callable


class TradeStreamStarter(Protocol):
    """Start a trade stream with shared Alpaca settings and shutdown signals."""

    def __call__(
        self,
        client: httpx2.AsyncClient,
        *,
        settings: AlpacaSettings,
        stop_event: asyncio.Event,
        on_terminated: Callable[[AlpacaStreamError], None] | None = None,
    ) -> Awaitable[StreamHandle[Trade]]:
        """Return an awaitable that initializes the trade stream handle."""
        ...


async def _cancel_and_wait[T](task: asyncio.Task[T]) -> None:
    if not task.done():
        task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        # Preserve mixed BaseExceptionGroups while draining ordinary task errors.
        except Exception:  # noqa: BLE001
            break
    with suppress(BaseException):
        task.result()


async def _stream_events[T](
    handle: StreamHandle[T],
    terminated: asyncio.Future[AlpacaStreamError],
) -> AsyncIterable[T]:
    while True:
        if terminated.done():
            error = terminated.result()
            raise error
        if handle.task.done() and handle.events.empty():
            await handle.task
            return

        event = asyncio.create_task(handle.events.get())
        try:
            waiting: set[asyncio.Future[object]] = {
                cast("asyncio.Future[object]", event),
                cast("asyncio.Future[object]", terminated),
            }
            if not handle.task.done():
                waiting.add(cast("asyncio.Future[object]", handle.task))
            completed, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

            if terminated in completed:
                raise terminated.result()
            if event in completed:
                yield event.result()
                continue
            if handle.task in completed:
                if handle.events.empty():
                    await handle.task
                    return
                yield await event
        finally:
            await _cancel_and_wait(event)


async def run_application(
    config: Config,
    client: httpx2.AsyncClient,
    logger: logging.Logger,
    stop_event: asyncio.Event,
    stock_starter: TradeStreamStarter | None = start_trade_stream,
) -> None:
    """Run the news and optional stock streams until shutdown.

    Set ``stop_event`` and await started stream tasks when the run exits.

    Args:
        config: Alpaca, Discord, and routing configuration.
        client: HTTP client used by the streams and webhook delivery.
        logger: Logger used for runtime events.
        stop_event: Event used to stop the stream adapters.
        stock_starter: Optional function that starts the stock stream.
    """
    loop = asyncio.get_running_loop()
    settings = AlpacaSettings(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        news_stream_url=config.alpaca_news_stream_url,
        stock_stream_url=config.alpaca_stock_stream_url,
    )
    news_terminated: asyncio.Future[AlpacaStreamError] = loop.create_future()
    stock_terminated: asyncio.Future[AlpacaStreamError] = loop.create_future()
    handles: list[StreamHandle[NewsItem] | StreamHandle[Trade]] = []

    def terminate_news(error: AlpacaStreamError) -> None:
        if not news_terminated.done():
            news_terminated.set_result(error)

    def terminate_stock(error: AlpacaStreamError) -> None:
        if not stock_terminated.done():
            stock_terminated.set_result(error)

    async def connect_news() -> AsyncIterable[NewsItem]:
        handle = await start_news_stream(
            client,
            settings=settings,
            stop_event=stop_event,
            on_terminated=terminate_news,
        )
        handles.append(handle)
        return _stream_events(handle, news_terminated)

    stock_stream: Callable[[], Awaitable[AsyncIterable[Trade]]] | None = None
    if stock_starter is not None:

        async def connect_stock() -> AsyncIterable[Trade]:
            handle = await stock_starter(
                client,
                settings=settings,
                stop_event=stop_event,
                on_terminated=terminate_stock,
            )
            handles.append(handle)
            return _stream_events(handle, stock_terminated)

        stock_stream = connect_stock

    try:
        await run(config, client, logger, connect_news, stock_stream)
    finally:
        stop_event.set()
        await asyncio.gather(*(handle.task for handle in handles), return_exceptions=True)


async def _run() -> None:
    logger = configure_logging()
    logger.info(
        "starting stocknews",
        extra={"version": "dev", "commit": os.environ.get("GIT_SHA") or "unknown", "build_date": "unknown"},
    )
    try:
        config = load_config(os.environ)
    except ValueError as error:
        # Raw exception chains may carry Alpaca credentials; JSON logging only redacts webhooks.
        logger.exception("invalid configuration", extra={"error": str(error)}, exc_info=False)
        raise SystemExit(2) from error

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    task = asyncio.current_task()
    if task is None:
        error_message = "stocknews startup has no active task"
        raise RuntimeError(error_message)

    def request_shutdown() -> None:
        stop_event.set()
        task.cancel()

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(shutdown_signal, request_shutdown)
    try:
        async with httpx2.AsyncClient(timeout=10) as client:
            try:
                await run_application(config, client, logger, stop_event)
            except asyncio.CancelledError:
                logger.info("shutdown requested")
    finally:
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(shutdown_signal)


def main() -> None:
    """Validate startup configuration and run the command."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return
    except Exception as error:
        # Raw exception chains may carry Alpaca credentials; JSON logging only redacts webhooks.
        logging.getLogger("stocknews").exception("stocknews failed", extra={"error": str(error)}, exc_info=False)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
