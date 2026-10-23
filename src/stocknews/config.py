"""Load application settings from an explicit environment mapping."""

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from stocknews.models import Config

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_ALPACA_NEWS_REST_URL = "https://data.alpaca.markets/v1beta1/news"
_DEFAULT_ALPACA_ASSETS_URL = "https://api.alpaca.markets"
_MAX_URL_PORT = 65535
_DEFAULT_NEWS_POLL_INTERVAL_SECONDS = 300
_DEFAULT_NEWS_POLL_OVERLAP_SECONDS = 600
_DEFAULT_NEWS_STATE_PATH = ".stocknews/news.sqlite3"
_DEFAULT_STOCK_LOGO = "https://static.stocktitan.net/company-logo/%s.webp"
_DEFAULT_TRANSPARENT_PNG = "https://major.io/transparent.png"


def csv_values(value: str) -> tuple[str, ...]:
    """Split comma-separated values, trimming whitespace and dropping empties."""
    return tuple(part for raw_part in value.split(",") if (part := raw_part.strip()))


def load_config(environ: Mapping[str, str]) -> Config:
    """Load settings from ``environ`` while preserving explicitly empty values."""
    api_key = _required_env(environ, "ALPACA_API_KEY")
    api_secret = _required_env(environ, "ALPACA_API_SECRET")
    poll_interval_seconds = _positive_int_env(
        environ,
        "NEWS_POLL_INTERVAL_SECONDS",
        _DEFAULT_NEWS_POLL_INTERVAL_SECONDS,
    )
    poll_overlap_seconds = _positive_int_env(
        environ,
        "NEWS_POLL_OVERLAP_SECONDS",
        _DEFAULT_NEWS_POLL_OVERLAP_SECONDS,
    )
    if poll_overlap_seconds < poll_interval_seconds:
        error_message = "NEWS_POLL_OVERLAP_SECONDS must be at least the poll interval"
        raise ValueError(error_message)

    return Config(
        alpaca_api_key=api_key,
        alpaca_api_secret=api_secret,
        alpaca_assets_url=_http_base_url_env(
            environ,
            "ALPACA_ASSETS_URL",
            _DEFAULT_ALPACA_ASSETS_URL,
        ),
        alpaca_news_rest_url=_nonblank_env(
            environ,
            "ALPACA_NEWS_REST_URL",
            _DEFAULT_ALPACA_NEWS_REST_URL,
        ),
        news_poll_interval_seconds=poll_interval_seconds,
        news_poll_overlap_seconds=poll_overlap_seconds,
        news_state_path=_nonblank_env(environ, "NEWS_STATE_PATH", _DEFAULT_NEWS_STATE_PATH),
        discord_analyst_webhooks=csv_values(environ.get("DISCORD_ANALYST_WEBHOOKS", "")),
        discord_earnings_webhooks=csv_values(environ.get("DISCORD_EARNINGS_WEBHOOKS", "")),
        discord_news_webhooks=csv_values(environ.get("DISCORD_NEWS_WEBHOOKS", "")),
        discord_analyst_enabled=_boolean_env(environ, "DISCORD_ANALYST_ENABLED"),
        discord_earnings_enabled=_boolean_env(environ, "DISCORD_EARNINGS_ENABLED"),
        discord_news_enabled=_boolean_env(environ, "DISCORD_NEWS_ENABLED"),
        stock_logo=environ.get("STOCK_LOGO", _DEFAULT_STOCK_LOGO),
        transparent_png=environ.get("TRANSPARENT_PNG", _DEFAULT_TRANSPARENT_PNG),
    )


def _http_base_url_env(environ: Mapping[str, str], key: str, default: str) -> str:
    """Read and validate an absolute HTTP or HTTPS base URL."""
    value = environ.get(key, default)
    error_message = f"{key} must be an absolute HTTP or HTTPS URL without userinfo, query, or fragment"
    if not value.strip() or any(character.isspace() for character in value):
        raise ValueError(error_message)

    try:
        parsed = urlsplit(value)
        has_valid_scheme = parsed.scheme.lower() in {"http", "https"}
        has_valid_authority = bool(parsed.hostname) and parsed.username is None and parsed.password is None
        port = parsed.port
        has_valid_port = port is None or 0 <= port <= _MAX_URL_PORT
    except ValueError:
        raise ValueError(error_message) from None

    if not has_valid_scheme or not has_valid_authority or not has_valid_port or parsed.query or parsed.fragment:
        raise ValueError(error_message)
    return value.rstrip("/")


def _nonblank_env(environ: Mapping[str, str], key: str, default: str) -> str:
    """Read a required nonblank setting, retaining the supplied value."""
    value = environ.get(key, default)
    if not value.strip():
        error_message = f"{key} must not be blank"
        raise ValueError(error_message)
    return value


def _positive_int_env(environ: Mapping[str, str], key: str, default: int) -> int:
    """Parse a strictly positive integer environment setting."""
    value = environ.get(key)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        error_message = f"{key} must be a positive integer"
        raise ValueError(error_message) from None
    if parsed <= 0:
        error_message = f"{key} must be a positive integer"
        raise ValueError(error_message)
    return parsed


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
