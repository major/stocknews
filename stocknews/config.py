"""Configuration values for the stocknews package."""

import logging
import os

log = logging.getLogger(__name__)

NEWS_API_KEY = os.getenv("NEWS_API_KEY", "missing")
NEWS_API_SECRET = os.getenv("NEWS_API_SECRET", "missing")
NEWS_API_ENDPOINT = os.getenv("NEWS_API_ENDPOINT", "https://localhost")

# How far back to gather news articles (in minutes).
NEWS_TIME_PERIOD = 10

# Blocked tickers that we don't want to see.
BLOCKED_TICKERS = [
    "ADAUSD",
    "AVAXUSD",
    "BNBUSD",
    "BTCUSD",
    "DOGEUSD",
    "ETHUSD",
    "LINKUSD",
    "MATICUSD",
    "PEPEUSD",
    "SHIBUSD",
    "SOLUSD",
    "TONUSD",
    "USDCUSD",
    "USDTUSD",
    "XRPUSD",
]

# Redis connection details
REDIS_HOST: str = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.environ.get("REDIS_PORT", 6379))

# Discord Channels
DISCORD_EARNINGS_WEBHOOK = os.getenv("DISCORD_EARNINGS_WEBHOOK", "missing")
DISCORD_NEWS_WEBHOOK = os.getenv("DISCORD_NEWS_WEBHOOK", "missing")

# Mastodon authentication
MASTODON_SERVER_URL = os.getenv("MASTODON_SERVER_URL", "missing")
MASTODON_SERVER_TOKEN = os.getenv("MASTODON_SERVER_TOKEN", "missing")

# Stock logo URL.
STOCK_LOGO = "https://s3.amazonaws.com/logos.atom.finance/stocks-and-funds/%s.png"

# Transparent long PNG to make all embeds the same length
TRANSPARENT_PNG = "https://major.io/transparent.png"

# Blocked phrases in news stories that we don't want.
BLOCKED_PHRASES = ["if you invested", "you would have", "would be worth"]
