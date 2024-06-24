"""Test the utilities."""

import fakeredis
import pytest

from stocknews import utils


def test_article_in_cache():
    utils.REDIS_CONN = fakeredis.FakeStrictRedis(decode_responses=True)
    assert not utils.article_in_cache(["AAPL"], "Apple stock is up 10%")
    assert utils.article_in_cache(["AAPL"], "Apple stock is up 10%")


def test_get_cache_expiration():
    assert utils.get_cache_expiration(["AAPL"], "Apple stock is up 10%") == 3600
    assert (
        utils.get_cache_expiration(
            ["AAPL"],
            "CVD Equipment Q1 EPS $(0.22) Down From $(0.01) YoY, Sales $4.92M Down From $8.70M YoY",
        )
        is None
    )


def test_is_earnings_news_symbols():
    assert not utils.is_earnings_news(["ONE", "TWO"], "Headline")
    assert not utils.is_earnings_news([], "Headline")


def test_is_earnings_news_regex():
    headlines = [
        "CXApp Q1 EPS $(0.34) Down From $0.20 YoY, Sales $1.82M Miss $2.30M Estimate",
        "e.l.f. Beauty Q4 Adj $0.53 Beats $0.32 Estimate, Sales $321.14M Beat $292.17M Estimate",
        "Autodesk Preliminary Q1 2025 EPS ~$1.87 Vs $1.74 Est.; Revenue ~$1.42B Vs $1.39B Est",
        "Smart Sand Q1 EPS $(0.01) Misses $0.05 Estimate, Sales $83.05M Beat $75.20M Estimate",
        "CVD Equipment Q1 EPS $(0.22) Down From $(0.01) YoY, Sales $4.92M Down From $8.70M YoY",
    ]
    assert all(utils.is_earnings_news(["XYZ"], headline) for headline in headlines)

    headlines = [
        "Live Ventures Post 30% Hike In Q2 Sales As Acquisitions Boost",
        "Fed's Preferred Inflation Gauge Matches Estimates; Personal Spending Slows More Than Predicted (CORRECTED)",
        "Humana Intends To Reaffirm Its FY24 Adjusted EPS Guidance Of Approximately $16.00; Est $16.36 - Filing",
        "Dell Technologies Shares Dip Despite Beating Q1 Expectations: What You Need To Know",
        "Alkermes Unveils Phase 1b Data for Narcolepsy Treatment at SLEEP 2024",
    ]
    assert not any(utils.is_earnings_news(["XYZ"], headline) for headline in headlines)


def test_is_blocked_ticker():
    utils.BLOCKED_TICKERS = ["AAPL", "GOOGL"]
    assert utils.is_blocked_ticker(["AAPL"])
    assert not utils.is_blocked_ticker(["TSLA"])
    assert utils.is_blocked_ticker(["AAPL", "TSLA"])
    assert not utils.is_blocked_ticker(["TSLA", "MSFT"])


def test_extract_earnings_data():
    """Verify that we can parse EPS results."""
    headline = "Nutanix Q3 2024 Adj EPS $0.28 Beats $0.17 Estimate, Sales $524.577M Beat $516.183M Estimate"
    data = utils.extract_earnings_data(headline)

    expected = {
        "eps": {
            "actual": "$0.28",
            "estimate": "$0.17",
            "beat": True,
        },
        "sales": {
            "actual": "$524.577M",
            "estimate": "$516.183M",
            "beat": True,
        },
    }

    assert data == expected


def test_extract_earnings_data_failure():
    """Verify a failure to parse earnings data."""
    headline = "Texas Community Bancshares Q1 2024 EPS $(0.89) Down From $(0.33) YoY"
    assert utils.extract_earnings_data(headline) == {}


@pytest.mark.parametrize(
    "headline,expected",
    [
        ("Beats", True),
        ("Beat", True),
        ("Misses", False),
        ("Miss", False),
        ("Missed", False),
    ],
)
def test_parse_earnings_result(headline, expected):
    """Verify that we can parse a beat or miss."""
    assert utils.parse_earnings_result(headline) == expected
