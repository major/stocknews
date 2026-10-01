import pytest

from stocknews.config import csv_values, load_config


def test_config_applies_defaults_and_parses_csv_values() -> None:
    settings = load_config(
        {
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            "DISCORD_ANALYST_WEBHOOKS": "a, b,, ",
            "DISCORD_EARNINGS_WEBHOOKS": "c",
            "DISCORD_NEWS_WEBHOOKS": "d,e",
            "BLOCKED_PHRASES": "spam, eggs",
        }
    )

    assert settings.alpaca_news_stream_url == "wss://stream.data.alpaca.markets/v1beta1/news"
    assert settings.alpaca_stock_stream_url == "wss://stream.data.alpaca.markets/v2"
    assert settings.stock_logo == "https://static.stocktitan.net/company-logo/%s.webp"
    assert settings.transparent_png == "https://major.io/transparent.png"
    assert settings.discord_analyst_webhooks == ("a", "b")
    assert settings.discord_earnings_webhooks == ("c",)
    assert settings.discord_news_webhooks == ("d", "e")
    assert settings.blocked_phrases == ("spam", "eggs")


def test_config_defaults_to_original_blocked_phrases() -> None:
    settings = load_config({"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"})

    assert settings.blocked_phrases == ("if you invested", "you would have", "would be worth")


def test_config_preserves_explicitly_empty_optional_values() -> None:
    settings = load_config(
        {
            "ALPACA_API_KEY": " key ",
            "ALPACA_API_SECRET": "secret",
            "ALPACA_NEWS_STREAM_URL": "",
            "ALPACA_STOCK_STREAM_URL": "",
            "STOCK_LOGO": "",
            "TRANSPARENT_PNG": "",
            "BLOCKED_PHRASES": "",
        }
    )

    assert settings.alpaca_api_key == " key "
    assert settings.alpaca_news_stream_url == ""
    assert settings.alpaca_stock_stream_url == ""
    assert settings.stock_logo == ""
    assert settings.transparent_png == ""
    assert settings.blocked_phrases == ()


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
    with pytest.raises(ValueError, match="is required"):
        load_config(environ)


def test_csv_values_trims_values_and_removes_empty_entries() -> None:
    assert csv_values("spam, scam,, legit ") == ("spam", "scam", "legit")
