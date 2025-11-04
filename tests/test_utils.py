from unittest.mock import patch

import pytest

from stocknews.utils import (
    boolean_to_emoji,
    extract_earnings_data,
    get_company_name,
    get_earnings_notification_description,
    has_blocked_phrases,
    is_analyst_rating_change,
    is_earnings_news,
    parse_earnings_result,
)


@pytest.fixture
def mock_settings():
    with patch("stocknews.utils.settings") as mock_settings:
        mock_settings.blocked_phrases = ["spam", "scam"]
        yield mock_settings


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


def test_extract_earnings_data_no_matches():
    headline = "Apple announces new product launch"
    result = extract_earnings_data(headline)
    assert result == {}


def test_is_earnings_news_with_blocked_phrases():
    headlines_with_blocked = [
        "AAPL Q1 EPS $2.00 up from $1.80 estimate",
        "MSFT Q2 Sales $50B down from $48B estimate",
    ]

    for headline in headlines_with_blocked:
        assert is_earnings_news(["AAPL"], headline) is False
