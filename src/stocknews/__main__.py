"""Start the stocknews process."""

import asyncio
import logging
import os
import signal
from contextlib import suppress
from typing import TYPE_CHECKING, cast

import httpx2

from stocknews.alpaca import (
    AlpacaStreamError,
    StreamHandle,
    start_news_stream,
)
from stocknews.config import load_config
from stocknews.logging import configure_logging, report_failure
from stocknews.models import AlpacaSettings, Config, NewsItem
from stocknews.runtime import run

if TYPE_CHECKING:
    from collections.abc import AsyncIterable


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
) -> None:
    """Run the news stream until shutdown.

    Set ``stop_event`` and await started stream tasks when the run exits.

    Args:
        config: Alpaca, Discord, and routing configuration.
        client: HTTP client used by the streams and webhook delivery.
        logger: Logger used for runtime events.
        stop_event: Event used to stop the stream adapters.
    """
    settings = AlpacaSettings(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        news_stream_url=config.alpaca_news_stream_url,
        auth_mode=config.alpaca_ws_auth_mode,
    )
    news_terminated: asyncio.Future[AlpacaStreamError] = asyncio.get_running_loop().create_future()
    handles: list[StreamHandle[NewsItem]] = []

    def terminate_news(error: AlpacaStreamError) -> None:
        if not news_terminated.done():
            news_terminated.set_result(error)

    async def connect_news() -> AsyncIterable[NewsItem]:
        handle = await start_news_stream(
            client,
            settings=settings,
            stop_event=stop_event,
            on_terminated=terminate_news,
        )
        handles.append(handle)
        return _stream_events(handle, news_terminated)

    try:
        await run(config, client, logger, connect_news)
    finally:
        stop_event.set()
        await asyncio.gather(*(handle.task for handle in handles), return_exceptions=True)


async def _run() -> None:
    logger = configure_logging()
    logger.info(
        "starting stocknews",
        extra={
            "version": "dev",
            "commit": (os.environ.get("GIT_SHA") or "unknown")[:7],
            "build_date": os.environ.get("BUILD_DATE") or "unknown",
        },
    )
    try:
        config = load_config(os.environ)
    except ValueError as error:
        report_failure(logger, "invalid configuration", str(error))
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
        report_failure(logging.getLogger("stocknews"), "stocknews failed", str(error))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
