#!/usr/bin/env python
"""Run the news bot."""

import logging
from time import sleep

from schedule import every, repeat, run_pending

from stocknews.news import get_all_news
from stocknews.notify import (
    send_earnings_to_discord,
    send_earnings_to_mastodon,
)
from stocknews.utils import (
    article_in_cache,
    is_earnings_news,
)

# Setup our shared logger.
log = logging.getLogger(__name__)


@repeat(every().minute)
def fetch_news() -> None:
    news_items = get_all_news()

    for news_item in news_items:
        if len(news_item["symbols"]) != 1:
            continue

        # Trigger a notification if this is an earnings report.
        if is_earnings_news(news_item["symbols"], news_item["headline"]):
            # Skip the article if it's already in the cache.
            if article_in_cache(news_item["symbols"], news_item["headline"]):
                log.info(f"👎 Article in cache: {news_item['headline']}")
                continue

            log.info(f"💸 Earnings news: {news_item['headline']}")
            send_earnings_to_discord(news_item["symbols"], news_item["headline"])
            send_earnings_to_mastodon(news_item["symbols"], news_item["headline"])
        else:
            log.info(f"📰 Non-earnings news: {news_item['headline']}")


if __name__ == "__main__":
    logging.info("🚀 Running News Bot")

    # Run it once to get things started.
    fetch_news()

    # Run the schedule loop.
    while True:
        run_pending()
        sleep(1)
