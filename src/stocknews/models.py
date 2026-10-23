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
    source: str = ""
    content: str = ""
    article_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    """Runtime settings loaded from environment variables."""

    alpaca_api_key: str
    alpaca_api_secret: str
    discord_analyst_webhooks: tuple[str, ...]
    discord_earnings_webhooks: tuple[str, ...]
    discord_news_webhooks: tuple[str, ...]
    stock_logo: str
    transparent_png: str
    discord_analyst_enabled: bool = True
    discord_earnings_enabled: bool = True
    discord_news_enabled: bool = True
    alpaca_assets_url: str = "https://api.alpaca.markets"
    alpaca_news_rest_url: str = "https://data.alpaca.markets/v1beta1/news"
    news_poll_interval_seconds: int = 300
    news_poll_overlap_seconds: int = 600
    news_state_path: str = ".stocknews/news.sqlite3"
