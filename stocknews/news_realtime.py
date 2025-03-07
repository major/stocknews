"""Implement real time news feed. 🚀"""

import asyncio
import json
import logging

import aiohttp

from stocknews import notify, utils
from stocknews.config import settings

logger = logging.getLogger(__name__)


class AlpacaNewsClient:
    """Alpaca News WebSocket client"""

    async def connect(self) -> None:
        """Establish WebSocket connection to Alpaca API"""
        # Define headers for authentication
        headers = {
            "APCA-API-KEY-ID": settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
        }

        websocket_url = settings.alpaca_news_stream_url
        print(f"Connecting to {websocket_url}...")

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(websocket_url, headers=headers, heartbeat=30) as ws,
        ):
            logger.info("WebSocket connection opened")

            # Subscribe to all news
            subscribe_msg = {"action": "subscribe", "news": ["*"]}
            await ws.send_json(subscribe_msg)
            logger.info("Sent subscription message")

            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self.handle_message(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        print(f"WebSocket error: {msg}")
                        break
            except asyncio.CancelledError:
                print("\nDisconnecting from WebSocket...")
                await ws.close()
            except Exception as e:
                print(f"Error: {e}")

    async def handle_message(self, message: str) -> None:
        """Handle incoming WebSocket messages"""
        data = json.loads(message)

        for item in data:
            # Check if it's a news item
            if isinstance(item, dict) and item.get("T") == "n":
                self.process_news_item(item)
            else:
                print(f"Connection message: {item}")

    def process_news_item(self, news_item: dict) -> None:
        """Process and display a news item"""
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
            notify.send_earnings_to_mastodon(
                news_item["symbols"], news_item["headline"]
            )
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

        logger.info(f"🤷‍♂️ Unknown news type: {news_item['headline']}")


async def main_async() -> None:
    """Get real time news from Alpaca's news API! 🚀"""

    # Verify that redis is up!
    utils.check_redis()

    # Create news client
    client = AlpacaNewsClient()

    try:
        # Connect and stream news
        task = asyncio.create_task(client.connect())

        # Wait for keyboard interrupt
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        # Cancel the task on keyboard interrupt
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    print("Done.")


def main() -> None:
    """Entry point for the script"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
