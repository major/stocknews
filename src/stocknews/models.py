"""Immutable values shared by the stock news application."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class NewsItem:
    """One news message received from Alpaca."""

    symbols: tuple[str, ...]
    author: str
    headline: str
    summary: str = ""
    url: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Trade:
    """One stock trade received from Alpaca."""

    symbol: str
    price: float
    size: float
    exchange: str = ""
    timestamp: str = ""
    conditions: tuple[str, ...] = ()
    tape: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AlpacaSettings:
    """Credentials and stream URLs for Alpaca connections.

    Attributes:
        api_key: Alpaca API key, excluded from the generated representation.
        api_secret: Alpaca API secret, excluded from the generated representation.
        news_stream_url: WebSocket URL for the news stream.
        stock_stream_url: Base WebSocket URL for the stock stream.
    """

    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    news_stream_url: str
    stock_stream_url: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    """Runtime settings loaded from environment variables."""

    alpaca_api_key: str
    alpaca_api_secret: str
    alpaca_news_stream_url: str
    alpaca_stock_stream_url: str
    discord_analyst_webhooks: tuple[str, ...]
    discord_earnings_webhooks: tuple[str, ...]
    discord_news_webhooks: tuple[str, ...]
    stock_logo: str
    transparent_png: str
    blocked_phrases: tuple[str, ...]
