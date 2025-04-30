"""Implement real time news feed. 🚀"""

import html
import logging

import sentry_sdk
import structlog
from httpx_ws import WebSocketSession, connect_ws
from rich import print

from stocknews import notify, utils
from stocknews.config import settings

logging.basicConfig(level=logging.INFO)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
logger = structlog.get_logger()


def stream_news() -> None:
    """Connect to the Alpaca WebSocket API and stream news."""
    ws_url = settings.alpaca_news_stream_url
    with connect_ws(ws_url) as ws:  # type: WebSocketSession
        authenticate(ws)
        subscribe(ws)

        while True:
            message = ws.receive_json()
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
    logger.debug(message)

    if message[0].get("T") != "success":
        logger.error("Authentication failed")
        raise Exception("Authentication failed")
    else:
        logger.info("🟢 Authenticated successfully")


def subscribe(ws: WebSocketSession) -> None:
    """Subscribe to the Alpaca WebSocket API"""
    subscribe_msg = {"action": "subscribe", "news": ["*"]}
    ws.send_json(subscribe_msg)

    message = ws.receive_json()
    logger.debug(message)

    if message[0].get("T") != "success":
        logger.error("Subscription failed")
        raise Exception("Subscription failed")
    else:
        logger.info("🟢 Subscribed to news successfully")


def handle_message(news_item: dict) -> None:
    """Handle incoming WebSocket messages"""
    # Throw out anything that doesn't have a single symbol.
    symbols = news_item.get("symbols", [])
    if len(symbols) != 1:
        return None

    # Unescape the headline to make it readable.
    headline = html.unescape(news_item.get("headline", ""))

    if utils.article_in_cache(symbols, headline):
        return None

    # Take a new earnings report we haven't seen before and process it.
    if utils.is_earnings_news(symbols, headline):
        logger.info(f"💸 Earnings news for {symbols[0]}: {headline}")
        notify.send_earnings_to_discord(symbols, headline)
        notify.send_earnings_to_mastodon(symbols, headline)
        return None

    # Take a new analyst report we haven't seen before and process it.
    if utils.is_analyst_rating_change(headline):
        logger.info(f"📈 Analyst rating change for {symbols[0]}: {headline}")
        notify.send_rating_change_to_discord(symbols, headline)
        return None

    logger.warning(f"❓ Unknown type: {symbols[0]} {headline}")
    notify.send_news_to_discord(symbols, headline, news_item)


def main() -> None:
    """Main function to run the news streaming."""
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        debug=settings.sentry_debug,
        send_default_pii=True,
        traces_sample_rate=1.0,
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
    )
    stream_news()
    print(settings)


if __name__ == "__main__":
    main()
