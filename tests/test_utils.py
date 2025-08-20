from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stocknews.utils import (
    article_in_cache,
    boolean_to_emoji,
    check_redis,
    dump_article_cache,
    extract_earnings_data,
    get_cache_expiration,
    get_company_name,
    get_earnings_notification_description,
    get_stocks_from_wikipedia,
    has_blocked_phrases,
    is_analyst_rating_change,
    is_earnings_news,
    parse_earnings_result,
    update_sp1500_stocks,
)


@pytest.fixture
def mock_redis():
    with patch("stocknews.utils.REDIS_CONN") as mock_redis_conn:
        yield mock_redis_conn


@pytest.fixture
def mock_settings():
    with patch("stocknews.utils.settings") as mock_settings:
        mock_settings.blocked_phrases = ["spam", "scam"]
        mock_settings.sp1500_stocks_file = "test_stocks.json"
        yield mock_settings


@pytest.fixture
def mock_pandas():
    with patch("stocknews.utils.pd") as mock_pd:
        # Mock DataFrame with Symbol column
        mock_df = MagicMock()
        mock_df.__getitem__.return_value.astype.return_value.tolist.return_value = [
            "AAPL",
            "MSFT",
            "GOOGL",
        ]
        mock_pd.read_html.return_value = [mock_df]
        yield mock_pd


@pytest.fixture
def mock_path():
    with patch("stocknews.utils.Path") as mock_path_class:
        mock_path_instance = MagicMock()
        mock_path_class.return_value = mock_path_instance
        yield mock_path_instance


def test_check_redis(mock_redis):
    mock_redis.ping.return_value = True
    check_redis()
    mock_redis.ping.assert_called_once()


def test_article_in_cache_exists(mock_redis):
    mock_redis.exists.return_value = True
    result = article_in_cache(["AAPL"], "Apple releases new iPhone")
    assert result is True
    mock_redis.exists.assert_called_once()


def test_article_in_cache_not_exists(mock_redis):
    mock_redis.exists.return_value = False
    result = article_in_cache(["AAPL"], "Apple releases new iPhone")
    assert result is False
    mock_redis.set.assert_called_once()


def test_get_cache_expiration():
    assert get_cache_expiration(["AAPL"], "Apple releases new iPhone") == 3600
    assert get_cache_expiration(["AAPL"], "AAPL Q1 EPS $2.00 vs $1.80 est.") is None


def test_is_earnings_news():
    assert is_earnings_news(["AAPL"], "AAPL Q1 EPS $2.00 vs $1.80 est.") is True
    assert (
        is_earnings_news(["AAPL", "MSFT"], "AAPL Q1 EPS $2.00 vs $1.80 est.") is False
    )


def test_extract_earnings_data():
    headline = "AAPL Q1 EPS $2.00 beat $1.80 Estimate"
    result = extract_earnings_data(headline)
    assert result == {"EPS": {"actual": "$2.00", "estimate": "$1.80", "beat": True}}


def test_parse_earnings_result():
    assert parse_earnings_result("beat") is True
    assert parse_earnings_result("miss") is False


def test_get_company_name():
    assert get_company_name("Apple Q1 Earnings Report") == "Apple"
    assert get_company_name("Q1 Earnings Report") == ""


def test_boolean_to_emoji():
    assert boolean_to_emoji(True) == "💚"
    assert boolean_to_emoji(False) == "💔"


def test_get_earnings_notification_description():
    headline = "AAPL Q1 EPS $2.00 beat $1.80 Estimate"
    result = get_earnings_notification_description(headline)
    assert result == "💚 EPS: $2.00 vs. $1.80 est."


def test_has_blocked_phrases(mock_settings):
    assert has_blocked_phrases("This is a spam headline") is True
    assert has_blocked_phrases("This is a normal headline") is False


def test_is_analyst_rating_change():
    assert is_analyst_rating_change("Apple price target raised to $200") is True
    assert is_analyst_rating_change("Apple releases new iPhone") is False


def test_check_redis_connection_error(mock_redis):
    mock_redis.ping.side_effect = Exception("Connection error")
    with pytest.raises(Exception, match="Connection error"):
        check_redis()
    mock_redis.ping.assert_called_once()


def test_extract_earnings_data_no_matches():
    headline = "Apple announces new product launch"
    result = extract_earnings_data(headline)
    assert result == {}


def test_dump_article_cache(mock_redis):
    mock_redis.keys.return_value = ["key1", "key2", "key3"]
    mock_redis.get.side_effect = [
        "AAPL: First article",
        "MSFT: Second article",
        "GOOGL: Third article",
    ]

    result = dump_article_cache()

    assert "AAPL: First article" in result
    assert "GOOGL: Third article" in result
    assert "MSFT: Second article" in result
    mock_redis.keys.assert_called_once()
    assert mock_redis.get.call_count == 3


def test_is_earnings_news_with_blocked_phrases():
    headlines_with_blocked = [
        "AAPL Q1 EPS $2.00 up from $1.80 estimate",
        "MSFT Q2 Sales $50B down from $48B estimate",
    ]

    for headline in headlines_with_blocked:
        assert is_earnings_news(["AAPL"], headline) is False


def test_get_stocks_from_wikipedia(mock_pandas):
    result = get_stocks_from_wikipedia("https://example.com")

    assert result == ["AAPL", "MSFT", "GOOGL"]
    mock_pandas.read_html.assert_called_once_with("https://example.com")


def test_update_sp1500_stocks(mock_pandas, mock_path, mock_settings):
    # Mock the get_stocks_from_wikipedia calls
    with patch("stocknews.utils.get_stocks_from_wikipedia") as mock_get_stocks:
        mock_get_stocks.side_effect = [
            ["AAPL", "MSFT", "BAD.WS"],  # S&P 500 (includes warrant)
            ["GOOGL", "TSLA"],  # S&P 400
            ["NVDA", "AMD.PR"],  # S&P 600 (includes preferred)
        ]

        update_sp1500_stocks()

        # Should call get_stocks_from_wikipedia 3 times
        assert mock_get_stocks.call_count == 3

        # Should write filtered and sorted stocks (no warrants/preferreds with dots)
        expected_stocks = ["AAPL", "GOOGL", "MSFT", "NVDA", "TSLA"]
        mock_path.write_text.assert_called_once()

        # Verify the JSON content written
        written_content = mock_path.write_text.call_args[0][0]
        import json

        written_stocks = json.loads(written_content)
        assert written_stocks == expected_stocks
