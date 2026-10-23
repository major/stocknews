"""Start the stocknews process."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

import httpx2

from stocknews.config import load_config
from stocknews.logging import configure_logging, report_failure
from stocknews.rest_news import poll_news
from stocknews.runtime import run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from stocknews.models import Config, NewsItem

STOCKNEWS_BANNER = r"""     _             _
 ___| |_ ___   ___| | ___ __   _____      _____
/ __| __/ _ \ / __| |/ / '_ \ / _ \ \ /\ / / __|
\__ \ || (_) | (__|   <| | | |  __/\ V  V /\__ \
|___/\__\___/ \___|_|\_\_| |_|\___| \_/\_/ |___/"""


async def run_application(
    config: Config,
    client: httpx2.AsyncClient,
    logger: logging.Logger,
    stop_event: asyncio.Event,
) -> None:
    """Run the REST news poller until shutdown.

    Set ``stop_event`` when the runtime exits so the poller can stop.

    Args:
        config: Alpaca REST, Discord, and routing configuration.
        client: HTTP client used by REST polling and webhook delivery.
        logger: Logger used for runtime events.
        stop_event: Event used to stop REST polling.
    """

    async def connect_rest() -> AsyncIterator[NewsItem]:
        """Return the configured Alpaca REST news poller."""
        return poll_news(config, client, logger, stop_event)

    try:
        await run(config, client, logger, connect_rest)
    finally:
        stop_event.set()


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
        sys.stderr.write(STOCKNEWS_BANNER + "\n\n")
        sys.stderr.flush()
        asyncio.run(_run())
    except KeyboardInterrupt:
        return
    except Exception as error:
        report_failure(logging.getLogger("stocknews"), "stocknews failed", str(error))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
