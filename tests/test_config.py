"""Tests for application configuration and CSV parsing."""

import pytest

from stocknews.config import csv_values, load_config


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

    assert settings.alpaca_news_stream_url == "wss://stream.data.alpaca.markets/v1beta1/news"
    assert settings.alpaca_ws_auth_mode == "message"
    assert settings.stock_logo == "https://static.stocktitan.net/company-logo/%s.webp"
    assert settings.transparent_png == "https://major.io/transparent.png"
    assert settings.discord_analyst_webhooks == ("a", "b")
    assert settings.discord_earnings_webhooks == ("c",)
    assert settings.discord_news_webhooks == ("d", "e")
    assert settings.discord_analyst_enabled is True
    assert settings.discord_earnings_enabled is True
    assert settings.discord_news_enabled is True


@pytest.mark.parametrize("value", ["message", "headers", " HEADERS "])
def test_config_parses_alpaca_ws_auth_mode(value: str) -> None:
    """Parse supported Alpaca WebSocket authentication modes."""
    settings = load_config(
        {
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            "ALPACA_WS_AUTH_MODE": value,
        }
    )

    assert settings.alpaca_ws_auth_mode == value.strip().casefold()


@pytest.mark.parametrize("value", ["", "  ", "invalid"])
def test_config_rejects_invalid_alpaca_ws_auth_mode(value: str) -> None:
    """Reject empty or unsupported Alpaca WebSocket authentication modes."""
    with pytest.raises(ValueError, match="ALPACA_WS_AUTH_MODE"):
        load_config(
            {
                "ALPACA_API_KEY": "key",
                "ALPACA_API_SECRET": "secret",
                "ALPACA_WS_AUTH_MODE": value,
            }
        )


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
            "ALPACA_NEWS_STREAM_URL": "",
            "STOCK_LOGO": "",
            "TRANSPARENT_PNG": "",
        }
    )

    assert settings.alpaca_api_key == " key "
    assert settings.alpaca_news_stream_url == ""
    assert settings.stock_logo == ""
    assert settings.transparent_png == ""


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
