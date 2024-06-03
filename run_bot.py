#!/usr/bin/env python
"""Run the news bot."""

import logging
from time import sleep

from schedule import every, repeat, run_pending

from stocknews.config import DISCORD_EARNINGS_WEBHOOK, DISCORD_NEWS_WEBHOOK
from stocknews.news import get_all_news
from stocknews.notify import send_to_discord
from stocknews.utils import article_in_cache, is_blocked_ticker, is_earnings_news

# Setup our shared logger.
log = logging.getLogger(__name__)


@repeat(every().minute)
def fetch_news() -> None:
    news_items = get_all_news()

    for news_item in news_items:
        if is_blocked_ticker(news_item["symbols"]):
            log.info(
                f"🚫 Blocked symbols: {news_item["symbols"]} {news_item['headline']}"
            )
            continue

        if len(news_item["symbols"]) > 2:
            log.info(
                f"🐘 Too many symbols: {news_item["symbols"]} {news_item['headline']}"
            )
            continue

        if article_in_cache(news_item["symbols"], news_item["headline"]):
            log.info(f"👎 Article in cache: {news_item['headline']}")
            continue

        if is_earnings_news(news_item["symbols"], news_item["headline"]):
            log.info(f"💸 Earnings news: {news_item['headline']}")
            send_to_discord(
                news_item["symbols"], news_item["headline"], DISCORD_EARNINGS_WEBHOOK
            )
        else:
            log.info(f"💤 Regular news: {news_item['headline']}")
            send_to_discord(
                news_item["symbols"], news_item["headline"], DISCORD_NEWS_WEBHOOK
            )


if __name__ == "__main__":
    logging.info("🚀 Running News Bot")

    # Run it once to get things started.
    fetch_news()

    # Run the schedule loop.
    while True:
        run_pending()
        sleep(1)
