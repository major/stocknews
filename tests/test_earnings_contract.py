"""Characterize the earnings alerts shown in the reported Discord examples."""

import pytest

from stocknews.discord import earnings_payload
from stocknews.models import NewsItem
from stocknews.news import classify_news

STOCK_LOGO = "https://example.test/%s.png"
TRANSPARENT_PNG = "https://example.test/transparent.png"


@pytest.mark.parametrize(
    "headline",
    [
        # Reconstructed from the unwanted alerts, not captured API headlines.
        "Apple Q4 Sales $57.183B iPhone Vs $49.025B Prior",
        "Apple Q4 Sales $10.792B Mac Vs $8.726B Prior",
        "Apple Q4 Sales $6.229B iPad Vs $6.952B Prior",
        "Apple Q4 Sales $8.3B Wearables, Home And Accessories Vs $9.013B Prior",
        "Apple Q4 EPS $2.10 Beats $1.98 Estimate",
        "Apple Q4 EPS $2.10 Beats $1.98 Estimate, Sales $10B iPhone Vs $9B Prior",
        "Apple Q4 EPS $2.10, Sales $10B, Revenue $11B",
    ],
)
def test_incomplete_earnings_do_not_create_alerts(headline: str) -> None:
    """Skip incomplete and ambiguous earnings instead of sending alerts."""
    item = NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline=headline)

    classification = classify_news(item)
    payload = earnings_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert classification == "incomplete_earnings"
    assert payload is None


def test_reversed_mixed_results_keep_eps_first() -> None:
    """Render EPS first regardless of source order or outcome."""
    headline = "Apple Q4 Sales $10B Beats $9B Estimate, EPS $1 Misses $2 Estimate"

    payload = earnings_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    assert payload["embeds"][0]["description"] == ("💔 EPS: $1 vs. $2 est.\n💚 Sales: $10B vs. $9B est.")


def test_complete_earnings_keep_the_requested_two_line_format() -> None:
    """Preserve the complete EPS and Sales alert shown in the desired example."""
    headline = "Apple Q4 EPS $2.10 Beats $1.98 Estimate, Sales $114.477B Beats $113.392B Estimate"

    payload = earnings_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    assert payload["embeds"][0]["description"] == (
        "💚 EPS: $2.10 vs. $1.98 est.\n💚 Sales: $114.477B vs. $113.392B est."
    )


@pytest.mark.parametrize(
    "headline",
    [
        "Acme Q1 EPS $, Sales $5B",
        "Acme Q1 EPS $1..2, Sales $5B",
        "Acme Q1 EPS $2 Beats $ Estimate, Sales $5B",
        "Acme Q1 EPS $(0.10, Sales $5B",
        "Analyst Estimates Q1 EPS $2, Sales $5B",
        "Acme Affirms Q1 EPS $2, Sales $5B",
        "Acme Forecasts Q1 EPS $2, Sales $5B",
        "Acme Q1 EPS -$-2, Sales $5B",
    ],
)
def test_pair_format_cannot_bypass_amount_and_forecast_safety(headline: str) -> None:
    """Reject malformed amounts and analyst forecasts even with both metric labels."""
    payload = earnings_payload("ACME", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is None
