"""Load application settings from an explicit environment mapping."""

from typing import TYPE_CHECKING

from stocknews.models import Config

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_NEWS_STREAM_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
_DEFAULT_STOCK_LOGO = "https://static.stocktitan.net/company-logo/%s.webp"
_DEFAULT_TRANSPARENT_PNG = "https://major.io/transparent.png"


def csv_values(value: str) -> tuple[str, ...]:
    """Split comma-separated values, trimming whitespace and dropping empties."""
    return tuple(part for raw_part in value.split(",") if (part := raw_part.strip()))


def load_config(environ: Mapping[str, str]) -> Config:
    """Load settings from ``environ`` while preserving explicitly empty values."""
    api_key = _required_env(environ, "ALPACA_API_KEY")
    api_secret = _required_env(environ, "ALPACA_API_SECRET")
    return Config(
        alpaca_api_key=api_key,
        alpaca_api_secret=api_secret,
        alpaca_news_stream_url=environ.get("ALPACA_NEWS_STREAM_URL", _DEFAULT_NEWS_STREAM_URL),
        discord_analyst_webhooks=csv_values(environ.get("DISCORD_ANALYST_WEBHOOKS", "")),
        discord_earnings_webhooks=csv_values(environ.get("DISCORD_EARNINGS_WEBHOOKS", "")),
        discord_news_webhooks=csv_values(environ.get("DISCORD_NEWS_WEBHOOKS", "")),
        discord_analyst_enabled=_boolean_env(environ, "DISCORD_ANALYST_ENABLED"),
        discord_earnings_enabled=_boolean_env(environ, "DISCORD_EARNINGS_ENABLED"),
        discord_news_enabled=_boolean_env(environ, "DISCORD_NEWS_ENABLED"),
        stock_logo=environ.get("STOCK_LOGO", _DEFAULT_STOCK_LOGO),
        transparent_png=environ.get("TRANSPARENT_PNG", _DEFAULT_TRANSPARENT_PNG),
    )


def _required_env(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key)
    if value is None or not value.strip():
        error_message = f"{key} is required"
        raise ValueError(error_message)
    return value


def _boolean_env(environ: Mapping[str, str], key: str) -> bool:
    """Parse a boolean environment setting, defaulting missing settings to true."""
    value = environ.get(key)
    if value is None:
        return True

    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    error_message = f"{key} must be a boolean"
    raise ValueError(error_message)
