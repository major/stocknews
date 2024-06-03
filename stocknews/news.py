"""Handle stock news from the API."""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from stocknews.config import (
    NEWS_API_ENDPOINT,
    NEWS_API_KEY,
    NEWS_API_SECRET,
    NEWS_TIME_PERIOD,
)

DEFAULT_HEADERS: dict = {
    "accept": "application/json",
    "APCA-API-KEY-ID": NEWS_API_KEY,
    "APCA-API-SECRET-KEY": NEWS_API_SECRET,
}

DEFAULT_PARAMS: dict = {
    "limit": 50,
    "include_content": False,
    "exclude_contentless": False,
}

log = logging.getLogger(__name__)


class NewsFailure(Exception):
    pass


def get_time_params() -> dict:
    """Get the time parameters for the API request."""
    start_time = datetime.now(timezone.utc) - timedelta(minutes=NEWS_TIME_PERIOD)
    end_time = datetime.now(timezone.utc)

    return {"start": start_time.isoformat(), "end": end_time.isoformat()}


def get_all_news() -> list:
    """Get the latest news articles."""
    extra_params = get_time_params()

    params = {**DEFAULT_PARAMS, **extra_params}

    # Get the first page of news.
    resp = httpx.get(NEWS_API_ENDPOINT, params=params, headers=DEFAULT_HEADERS)
    news_items = resp.json().get("news")

    # Get the remaining pages.
    while next_page_token := resp.json().get("next_page_token"):
        params["page_token"] = next_page_token
        resp = httpx.get(NEWS_API_ENDPOINT, params=params, headers=DEFAULT_HEADERS)
        news_items.extend(resp.json().get("news"))

    logging.info(f"Found {len(news_items)} news articles.")

    return list(news_items)
