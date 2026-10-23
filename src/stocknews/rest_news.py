"""Poll Alpaca's REST news endpoint and persist article deduplication state."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx2

from stocknews.models import Config, NewsItem
from stocknews.news_state import NewsState

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator


class _InvalidNewsResponseError(ValueError):
    """Mark an Alpaca response that does not match the expected schema."""


class _AuthenticationError(RuntimeError):
    """Mark a fatal Alpaca authentication failure."""

    def __init__(self, status_code: int) -> None:
        """Build a safe authentication error from the HTTP status."""
        super().__init__(f"Alpaca REST news authentication failed with status {status_code}")


@dataclass(slots=True)
class _PollWindow:
    """Carry the state needed to read and commit one poll window."""

    state: NewsState
    config: Config
    client: httpx2.AsyncClient
    logger: logging.Logger
    start: datetime
    end: datetime
    complete: bool = False


def _utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def _format_rfc3339(value: datetime) -> str:
    """Format an aware datetime as an RFC 3339 UTC timestamp."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_response(payload: Any) -> tuple[list[NewsItem], str | None]:
    """Validate and parse a page of Alpaca news results."""
    if not isinstance(payload, dict):
        raise _InvalidNewsResponseError

    news = payload.get("news")
    next_page_token = payload.get("next_page_token")
    if not isinstance(news, list) or "next_page_token" not in payload:
        raise _InvalidNewsResponseError
    if next_page_token is not None and (not isinstance(next_page_token, str) or not next_page_token):
        raise _InvalidNewsResponseError

    items = [_parse_article(article) for article in news]
    return items, next_page_token


def _parse_article(article: Any) -> NewsItem:
    """Validate and convert one Alpaca article to a news item."""
    if not isinstance(article, dict):
        raise _InvalidNewsResponseError

    article_id = article.get("id")
    symbols = article.get("symbols")
    author = article.get("author")
    headline = article.get("headline")
    if isinstance(article_id, bool) or not isinstance(article_id, int) or article_id <= 0:
        raise _InvalidNewsResponseError
    if not isinstance(symbols, list) or not all(isinstance(symbol, str) for symbol in symbols):
        raise _InvalidNewsResponseError
    if not isinstance(author, str) or not isinstance(headline, str):
        raise _InvalidNewsResponseError

    optional_fields: dict[str, str] = {}
    for field in ("summary", "url", "source", "content"):
        value = article.get(field, "")
        if not isinstance(value, str):
            raise _InvalidNewsResponseError
        optional_fields[field] = value

    return NewsItem(
        article_id=article_id,
        symbols=tuple(symbols),
        author=author,
        headline=headline,
        **optional_fields,
    )


async def _get_page(
    window: _PollWindow,
    page_token: str | None,
    seen_tokens: set[str],
) -> tuple[list[NewsItem], str | None]:
    """Request and validate one page of the current poll window."""
    params: dict[str, str | int] = {
        "start": _format_rfc3339(window.start),
        "end": _format_rfc3339(window.end),
        "sort": "asc",
        "limit": 50,
        "include_content": "true",
    }
    if page_token is not None:
        params["page_token"] = page_token

    response = await window.client.get(
        window.config.alpaca_news_rest_url,
        headers={
            "APCA-API-KEY-ID": window.config.alpaca_api_key,
            "APCA-API-SECRET-KEY": window.config.alpaca_api_secret,
        },
        params=params,
    )
    if response.status_code in {401, 403}:
        raise _AuthenticationError(response.status_code)
    response.raise_for_status()
    items, next_page_token = _parse_response(response.json())
    if next_page_token is not None and next_page_token in seen_tokens:
        raise _InvalidNewsResponseError
    return items, next_page_token


async def _poll_window(
    window: _PollWindow,
) -> AsyncIterator[NewsItem]:
    """Yield claimed articles from a window and record whether all pages were read."""
    page_token: str | None = None
    seen_tokens: set[str] = set()

    while True:
        try:
            items, next_page_token = await _get_page(window, page_token, seen_tokens)
        except httpx2.HTTPStatusError as error:
            window.logger.warning("Alpaca REST news poll failed: status=%d", error.response.status_code)
            return
        except (httpx2.HTTPError, ValueError) as error:
            window.logger.warning("Alpaca REST news poll failed: error_type=%s", type(error).__name__)
            return

        for item in items:
            assert item.article_id is not None  # noqa: S101
            if window.state.claim(item.article_id):
                yield item

        if next_page_token is None:
            window.complete = True
            return
        seen_tokens.add(next_page_token)
        page_token = next_page_token


async def _wait_for_next_poll(stop_event: asyncio.Event, interval_seconds: int) -> None:
    """Wait for the stop signal or for the next poll interval to elapse."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    except TimeoutError:
        return


async def poll_news(
    config: Config,
    client: httpx2.AsyncClient,
    logger: logging.Logger,
    stop_event: asyncio.Event,
) -> AsyncIterator[NewsItem]:
    """Yield unseen Alpaca news while polling the configured REST endpoint."""
    with closing(NewsState(config.news_state_path)) as state:
        while not stop_event.is_set():
            end = _utc_now()
            checkpoint = state.checkpoint()
            if checkpoint is None:
                checkpoint = end
                state.set_checkpoint(checkpoint)
            start = checkpoint - timedelta(seconds=config.news_poll_overlap_seconds)
            window = _PollWindow(state, config, client, logger, start, end)
            async for item in _poll_window(window):
                yield item
            if window.complete:
                state.set_checkpoint(end)

            if stop_event.is_set():
                return
            await _wait_for_next_poll(stop_event, config.news_poll_interval_seconds)
