"""Implement real time news feed. 🚀"""

import logging

import sentry_sdk
import structlog
from httpx_ws import WebSocketSession, connect_ws

from stocknews import notify, utils
from stocknews.config import settings

logging.basicConfig(level=logging.DEBUG)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG))
logger = structlog.get_logger()


def stream_news() -> None:
    """Connect to the Alpaca WebSocket API and stream news."""
    ws_url = settings.alpaca_news_stream_url
    with connect_ws(ws_url) as ws:  # type: WebSocketSession
        authenticate(ws)
        subscribe(ws)

        while True:
            message = ws.receive_json()
            logger.info(message)
            for item in message:
                handle_message(item)


def authenticate(ws: WebSocketSession) -> None:
    """Authenticate with the Alpaca WebSocket API"""
    auth_json = {
        "action": "auth",
        "key": settings.alpaca_api_key,
        "secret": settings.alpaca_api_secret,
    }
    ws.send_json(auth_json)

    message = ws.receive_json()
    logger.info(message)

    if message[0].get("T") != "success":
        logger.error("Authentication failed")
        raise Exception("Authentication failed")


def subscribe(ws: WebSocketSession) -> None:
    """Subscribe to the Alpaca WebSocket API"""
    subscribe_msg = {"action": "subscribe", "news": ["*"]}
    ws.send_json(subscribe_msg)

    message = ws.receive_json()
    logger.info(message)

    if message[0].get("T") != "success":
        logger.error("Subscription failed")
        raise Exception("Subscription failed")


def handle_message(news_item: dict) -> None:
    """Handle incoming WebSocket messages"""
    # Throw out anything that doesn't have a single symbol.
    symbols = news_item.get("symbols", [])
    if len(symbols) != 1:
        return None

    headline = news_item.get("headline", "")
    # Take a new earnings report we haven't seen before and process it.
    if utils.is_earnings_news(symbols, headline) and not utils.article_in_cache(
        symbols, headline
    ):
        logger.info(f"💸 Earnings news for {symbols[0]}: {headline}")
        notify.send_earnings_to_discord(news_item["symbols"], news_item["headline"])
        notify.send_earnings_to_mastodon(news_item["symbols"], news_item["headline"])
        return None

    # Take a new analyst report we haven't seen before and process it.
    if utils.is_analyst_rating_change(headline) and not utils.article_in_cache(
        symbols, headline
    ):
        logger.info(f"📈 Analyst rating change for {symbols[0]}: {headline}")
        notify.send_rating_change_to_discord(
            news_item["symbols"], news_item["headline"]
        )
        return None

    logger.info(f"🤷‍♂️ Unknown type: {symbols[0]} {news_item['headline']}")


def main() -> None:
    """Main function to run the news streaming."""
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        send_default_pii=True,
        traces_sample_rate=0.25,
        profile_session_sample_rate=0.25,
        profile_lifecycle="trace",
    )
    stream_news()


if __name__ == "__main__":
    main()
