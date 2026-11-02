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
    ],
)
def test_incomplete_earnings_currently_create_alerts(headline: str) -> None:
    """Characterize the overly broad delivery gate before tightening it."""
    item = NewsItem(symbols=("AAPL",), author="Benzinga Newsdesk", headline=headline)

    classification = classify_news(item)
    payload = earnings_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert classification == "earnings"
    assert payload is not None


def test_complete_earnings_keep_the_requested_two_line_format() -> None:
    """Preserve the complete EPS and Sales alert shown in the desired example."""
    headline = "Apple Q4 EPS $2.10 Beats $1.98 Estimate, Sales $114.477B Beats $113.392B Estimate"

    payload = earnings_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    assert payload["embeds"][0]["description"] == (
        "💚 EPS: $2.10 vs. $1.98 est.\n💚 Sales: $114.477B vs. $113.392B est."
    )
