"""Tests for application configuration and CSV parsing."""

import pytest

from stocknews.config import csv_values, load_config
from stocknews.models import NewsItem

_DEFAULT_NEWS_POLL_INTERVAL_SECONDS = 300
_DEFAULT_NEWS_POLL_OVERLAP_SECONDS = 600
_DEFAULT_ALPACA_ASSETS_URL = "https://api.alpaca.markets"
_ARTICLE_ID = 123


def test_config_applies_defaults_and_parses_csv_values() -> None:
    """Apply default settings and parse configured comma-separated values."""
    settings = load_config(
        {
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            "DISCORD_ANALYST_WEBHOOKS": "a, b,, ",
            "DISCORD_EARNINGS_WEBHOOKS": "c",
            "DISCORD_NEWS_WEBHOOKS": "d,e",
        }
    )

    assert settings.alpaca_news_rest_url == "https://data.alpaca.markets/v1beta1/news"
    assert settings.alpaca_assets_url == _DEFAULT_ALPACA_ASSETS_URL
    assert settings.news_poll_interval_seconds == _DEFAULT_NEWS_POLL_INTERVAL_SECONDS
    assert settings.news_poll_overlap_seconds == _DEFAULT_NEWS_POLL_OVERLAP_SECONDS
    assert settings.news_state_path == ".stocknews/news.sqlite3"
    assert settings.stock_logo == "https://static.stocktitan.net/company-logo/%s.webp"
    assert settings.transparent_png == "https://major.io/transparent.png"
    assert settings.discord_analyst_webhooks == ("a", "b")
    assert settings.discord_earnings_webhooks == ("c",)
    assert settings.discord_news_webhooks == ("d", "e")
    assert settings.discord_analyst_enabled is True
    assert settings.discord_earnings_enabled is True
    assert settings.discord_news_enabled is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://assets.example.test", "http://assets.example.test"),
        ("https://paper-api.alpaca.markets", "https://paper-api.alpaca.markets"),
        ("https://assets.example.test/base/", "https://assets.example.test/base"),
    ],
)
def test_config_loads_alpaca_assets_base_url(value: str, expected: str) -> None:
    """Load the assets API base URL and normalize trailing slashes."""
    settings = load_config(
        {
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            "ALPACA_ASSETS_URL": value,
        }
    )

    assert settings.alpaca_assets_url == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        "relative/path",
        "ftp://assets.example.test",
        "https:///missing-host",
        "https://user:secret@assets.example.test",
        "https://assets.example.test?query=1",
        "https://assets.example.test#fragment",
        "https://assets.example.test:invalid",
    ],
)
def test_config_rejects_invalid_alpaca_assets_base_url(value: str) -> None:
    """Reject blank, relative, or malformed Alpaca assets base URLs."""
    with pytest.raises(ValueError, match="ALPACA_ASSETS_URL"):
        load_config(
            {
                "ALPACA_API_KEY": "key",
                "ALPACA_API_SECRET": "secret",
                "ALPACA_ASSETS_URL": value,
            }
        )


@pytest.mark.parametrize(
    ("env_key", "field_name", "value"),
    [
        ("ALPACA_NEWS_REST_URL", "alpaca_news_rest_url", " https://example.test/news "),
        ("NEWS_STATE_PATH", "news_state_path", " /tmp/news.sqlite3 "),
    ],
)
def test_config_preserves_nonblank_rest_settings(
    env_key: str,
    field_name: str,
    value: str,
) -> None:
    """Load nonblank REST URL and state path values without changing them."""
    settings = load_config(
        {
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            env_key: value,
        }
    )

    assert getattr(settings, field_name) == value


@pytest.mark.parametrize("env_key", ["ALPACA_NEWS_REST_URL", "NEWS_STATE_PATH"])
@pytest.mark.parametrize("value", ["", "  ", "\t"])
def test_config_rejects_blank_rest_settings(env_key: str, value: str) -> None:
    """Reject empty or whitespace-only REST URL and state path settings."""
    with pytest.raises(ValueError, match=env_key):
        load_config(
            {
                "ALPACA_API_KEY": "key",
                "ALPACA_API_SECRET": "secret",
                env_key: value,
            }
        )


@pytest.mark.parametrize(
    ("env_key", "field_name", "value", "expected"),
    [
        ("NEWS_POLL_INTERVAL_SECONDS", "news_poll_interval_seconds", "1", 1),
        ("NEWS_POLL_INTERVAL_SECONDS", "news_poll_interval_seconds", "45", 45),
        ("NEWS_POLL_OVERLAP_SECONDS", "news_poll_overlap_seconds", "600", 600),
        ("NEWS_POLL_OVERLAP_SECONDS", "news_poll_overlap_seconds", "900", 900),
    ],
)
def test_config_parses_news_poll_seconds(
    env_key: str,
    field_name: str,
    value: str,
    expected: int,
) -> None:
    """Parse positive news polling settings as integers."""
    settings = load_config(
        {
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            env_key: value,
        }
    )

    assert getattr(settings, field_name) == expected


@pytest.mark.parametrize(
    "env_key",
    ["NEWS_POLL_INTERVAL_SECONDS", "NEWS_POLL_OVERLAP_SECONDS"],
)
@pytest.mark.parametrize("value", ["", "  ", "0", "-1", "1.5", "invalid"])
def test_config_rejects_nonpositive_or_invalid_news_poll_seconds(
    env_key: str,
    value: str,
) -> None:
    """Reject blank, non-integer, zero, and negative polling settings."""
    with pytest.raises(ValueError, match=env_key):
        load_config(
            {
                "ALPACA_API_KEY": "key",
                "ALPACA_API_SECRET": "secret",
                env_key: value,
            }
        )


@pytest.mark.parametrize(
    ("interval", "overlap"),
    [("300", "299"), ("301", "300"), ("900", "600")],
)
def test_config_rejects_overlap_shorter_than_interval(
    interval: str,
    overlap: str,
) -> None:
    """Reject polling overlap values shorter than the polling interval."""
    with pytest.raises(ValueError, match="NEWS_POLL_OVERLAP_SECONDS"):
        load_config(
            {
                "ALPACA_API_KEY": "key",
                "ALPACA_API_SECRET": "secret",
                "NEWS_POLL_INTERVAL_SECONDS": interval,
                "NEWS_POLL_OVERLAP_SECONDS": overlap,
            }
        )


@pytest.mark.parametrize(
    ("env_key", "value"),
    [
        ("NEWS_POLL_INTERVAL_SECONDS", "601"),
        ("NEWS_POLL_OVERLAP_SECONDS", "299"),
    ],
)
def test_config_checks_overlap_against_default_for_unset_poll_setting(
    env_key: str,
    value: str,
) -> None:
    """Check the overlap constraint when the other polling value is defaulted."""
    with pytest.raises(ValueError, match="NEWS_POLL_OVERLAP_SECONDS"):
        load_config(
            {
                "ALPACA_API_KEY": "key",
                "ALPACA_API_SECRET": "secret",
                env_key: value,
            }
        )


def test_news_item_article_id_is_optional() -> None:
    """Keep news items valid without an article identifier."""
    news_item = NewsItem(symbols=(), author="", headline="")

    assert news_item.article_id is None


def test_news_item_accepts_article_id() -> None:
    """Store a positive article identifier on a news item."""
    news_item = NewsItem(symbols=(), author="", headline="", article_id=_ARTICLE_ID)

    assert news_item.article_id == _ARTICLE_ID


@pytest.mark.parametrize(
    ("env_key", "field_name"),
    [
        ("DISCORD_ANALYST_ENABLED", "discord_analyst_enabled"),
        ("DISCORD_EARNINGS_ENABLED", "discord_earnings_enabled"),
        ("DISCORD_NEWS_ENABLED", "discord_news_enabled"),
    ],
)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        (" TRUE ", True),
        ("1", True),
        ("yes", True),
        ("On", True),
        ("false", False),
        (" FALSE ", False),
        ("0", False),
        ("no", False),
        ("OFF", False),
    ],
)
def test_config_parses_discord_enabled_settings_independently(
    env_key: str,
    field_name: str,
    value: str,
    expected: bool,
) -> None:
    """Parse each Discord category toggle without changing the other defaults."""
    settings = load_config(
        {
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            env_key: value,
        }
    )

    assert getattr(settings, field_name) is expected
    assert all(
        getattr(settings, other_field) is True
        for other_field in (
            "discord_analyst_enabled",
            "discord_earnings_enabled",
            "discord_news_enabled",
        )
        if other_field != field_name
    )


@pytest.mark.parametrize(
    "env_key",
    [
        "DISCORD_ANALYST_ENABLED",
        "DISCORD_EARNINGS_ENABLED",
        "DISCORD_NEWS_ENABLED",
    ],
)
@pytest.mark.parametrize("value", ["", "  ", "sometimes", "true-ish"])
def test_config_rejects_invalid_discord_enabled_settings(env_key: str, value: str) -> None:
    """Reject invalid toggle values without including the value in the error."""
    environ = {
        "ALPACA_API_KEY": "key",
        "ALPACA_API_SECRET": "secret",
        env_key: value,
    }

    with pytest.raises(ValueError, match=env_key) as error:
        load_config(environ)

    assert str(error.value) == f"{env_key} must be a boolean"
    if value:
        assert value not in str(error.value)


def test_config_preserves_explicitly_empty_optional_values() -> None:
    """Preserve empty optional settings instead of replacing them with defaults."""
    settings = load_config(
        {
            "ALPACA_API_KEY": " key ",
            "ALPACA_API_SECRET": "secret",
            "STOCK_LOGO": "",
            "TRANSPARENT_PNG": "",
        }
    )

    assert settings.alpaca_api_key == " key "
    assert settings.stock_logo == ""
    assert settings.transparent_png == ""


@pytest.mark.parametrize(
    "env_key",
    ["ALPACA_NEWS_MODE", "ALPACA_NEWS_STREAM_URL", "ALPACA_WS_AUTH_MODE"],
)
def test_config_ignores_obsolete_websocket_settings(env_key: str) -> None:
    """Ignore removed WebSocket settings, including invalid values."""
    environ = {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"}
    config_without_obsolete_setting = load_config(environ)
    config_with_obsolete_setting = load_config({**environ, env_key: "invalid"})

    assert config_with_obsolete_setting == config_without_obsolete_setting


@pytest.mark.parametrize(
    "legacy_value",
    ["", "wss://legacy-stock-stream.example/v2"],
    ids=["empty", "nonempty"],
)
def test_legacy_stock_stream_environment_does_not_change_config(legacy_value: str) -> None:
    """Ignore legacy stock stream environment values when loading config.

    Args:
        legacy_value: Empty or nonempty value for the removed environment variable.
    """
    environ = {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"}
    legacy_environ = {**environ, "ALPACA_STOCK_STREAM_URL": legacy_value}

    config_without_legacy_setting = load_config(environ)
    config_with_legacy_setting = load_config(legacy_environ)

    assert config_with_legacy_setting == config_without_legacy_setting


@pytest.mark.parametrize(
    ("name", "environ"),
    [
        ("missing key", {"ALPACA_API_SECRET": "secret"}),
        ("missing secret", {"ALPACA_API_KEY": "key"}),
        ("blank key", {"ALPACA_API_KEY": "  ", "ALPACA_API_SECRET": "secret"}),
        ("blank secret", {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "\t"}),
    ],
)
def test_config_requires_nonblank_credentials(name: str, environ: dict[str, str]) -> None:
    """Reject missing or whitespace-only Alpaca credentials."""
    with pytest.raises(ValueError, match="is required"):
        load_config(environ)


def test_csv_values_trims_values_and_removes_empty_entries() -> None:
    """Trim CSV entries and discard empty values."""
    assert csv_values("spam, scam,, legit ") == ("spam", "scam", "legit")
