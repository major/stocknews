"""Immutable values shared by the stock news application."""

from dataclasses import dataclass


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
