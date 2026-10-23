"""Immutable values shared by the stock news application."""

from dataclasses import dataclass, field
from typing import Literal

AlpacaAuthMode = Literal["message", "headers"]
AlpacaNewsMode = Literal["websocket", "rest"]


@dataclass(frozen=True, slots=True, kw_only=True)
class NewsItem:
    """One news message received from Alpaca."""

    symbols: tuple[str, ...]
    author: str
    headline: str
    summary: str = ""
    url: str = ""
    source: str = ""
    content: str = ""
    article_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AlpacaSettings:
    """Credentials and news stream URL for Alpaca connections.

    Attributes:
        api_key: Alpaca API key, excluded from the generated representation.
        api_secret: Alpaca API secret, excluded from the generated representation.
        news_stream_url: WebSocket URL for the news stream.
        auth_mode: Authentication method for the WebSocket connection.
    """

    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    news_stream_url: str
    auth_mode: AlpacaAuthMode = "message"


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    """Runtime settings loaded from environment variables."""

    alpaca_api_key: str
    alpaca_api_secret: str
    alpaca_news_stream_url: str
    discord_analyst_webhooks: tuple[str, ...]
    discord_earnings_webhooks: tuple[str, ...]
    discord_news_webhooks: tuple[str, ...]
    stock_logo: str
    transparent_png: str
    discord_analyst_enabled: bool = True
    discord_earnings_enabled: bool = True
    discord_news_enabled: bool = True
    alpaca_ws_auth_mode: AlpacaAuthMode = "message"
    alpaca_news_mode: AlpacaNewsMode = "websocket"
    alpaca_news_rest_url: str = "https://data.alpaca.markets/v1beta1/news"
    news_poll_interval_seconds: int = 300
    news_poll_overlap_seconds: int = 600
    news_state_path: str = ".stocknews/news.sqlite3"
