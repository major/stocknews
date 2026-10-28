"""Tests for news classification and rejection precedence."""

import pytest

from stocknews.models import NewsItem
from stocknews.news import classify_news, us_symbols


def _item(symbols: tuple[str, ...], author: str, headline: str) -> NewsItem:
    """Build a news item for classification tests."""
    return NewsItem(symbols=symbols, author=author, headline=headline)


@pytest.mark.parametrize(
    ("symbols", "author", "expected"),
    [
        (("AAPL", "MSFT"), "Someone else", "unapproved_author"),
        (("  ", ""), "Someone else", "unapproved_author"),
        (("TSX:SHOP",), "Someone else", "non_us_exchange"),
        (("TSX:SHOP", "NASDAQ:ABC"), "Benzinga Newsdesk", "non_us_exchange"),
        (("AAPL",), "Someone else", "unapproved_author"),
        (("AAPL",), "Benzinga Newsdesk", "news"),
    ],
)
def test_news_rejections_follow_symbol_then_author_order(symbols: tuple[str, ...], author: str, expected: str) -> None:
    """Check exchange eligibility before exact author approval."""
    assert classify_news(_item(symbols, author, "headline")) == expected


@pytest.mark.parametrize(
    "symbols",
    [(), ("", "  "), ("   ",)],
)
def test_news_accepts_missing_or_blank_symbols(symbols: tuple[str, ...]) -> None:
    """Allow absent or blank-only ticker lists through exchange eligibility."""
    assert classify_news(_item(symbols, "Benzinga Newsdesk", "headline")) == "news"


def test_news_accepts_us_symbol_in_mixed_exchange_list() -> None:
    """Accept a ticker list when normalization retains at least one US symbol."""
    item = _item((" TSX:SHOP ", " AAPL ", " "), "Benzinga Newsdesk", "headline")

    assert classify_news(item) == "news"


@pytest.mark.parametrize(
    ("symbols", "expected"),
    [
        ((" AAPL ", "", "MSFT"), ("AAPL", "MSFT")),
        (("TSX:SHOP", " NASDAQ:ABC ", "NVDA"), ("NVDA",)),
        (("", "  ", "NYSE:IBM"), ()),
    ],
)
def test_us_symbols_trims_and_drops_ineligible_entries(symbols: tuple[str, ...], expected: tuple[str, ...]) -> None:
    """Trim ticker entries and drop blanks or colon-qualified symbols."""
    assert us_symbols(symbols) == expected


@pytest.mark.parametrize(
    "headline",
    [
        "if you invested in AAPL last year",
        "you would have made money with AAPL",
        "AAPL shares would be worth more today",
        "IF YOU INVESTED &amp; HELD AAPL last year",
    ],
)
def test_blocked_phrases_do_not_suppress_news(headline: str) -> None:
    """Classify headlines normally even when they contain former blocked phrases."""
    assert classify_news(_item(("AAPL",), "Benzinga Newsdesk", headline)) == "news"


def test_news_unescapes_encoded_headline_characters_before_classifying() -> None:
    """Decode HTML entities before classifying headline categories."""
    earnings_item = _item(("AAPL",), "Benzinga Newsdesk", "AAPL Q1 EPS &#36;2.00 vs &#36;1.80 est.")
    analyst_item = _item(
        ("AAPL",), "Benzinga Newsdesk", "Baird Upgrades Apple to Outperform, Raises Price Target to $200"
    )

    assert classify_news(earnings_item) == "earnings"
    assert classify_news(analyst_item) == "analyst"


@pytest.mark.parametrize("author", ["benzinga Newsdesk", "Benzinga newsdesk", "Someone else"])
def test_author_match_is_exact_and_case_sensitive(author: str) -> None:
    """Route only the exact Benzinga Newsdesk author name."""
    assert classify_news(_item(("AAPL",), author, "headline")) == "unapproved_author"


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        # Captured from stocknews logs on October 26 to 28, 2026.
        ("TE Connectivity Sees Q1 GAAP EPS $3.04 vs $2.81 Est", "guidance_only"),
        ("TE Connectivity Sees Q1 Adj EPS $3.30 vs $3.01 Est; Sees Sales $5.350B vs $5.131B Est", "guidance_only"),
        ("TE Connectivity Q4 Adj. EPS $3.14 Beats $3.07 Estimate, Sales $5.312B Beat $5.282B Estimate", "earnings"),
        # Constructed case, HTML, and mixed-headline variants.
        ("TE Connectivity sees q1 eps &#36;1.20 vs &#36;1.10 estimate", "guidance_only"),
        ("TE Connectivity Q4 EPS &#36;2.10 beat &#36;2.00 Estimate", "earnings"),
        (
            "TE Connectivity Q4 EPS $2.10 beat $2.00 Estimate; Sees Q1 EPS $1.20 vs $1.10 Estimate",
            "earnings",
        ),
        (
            "TE Connectivity Sees Q1 EPS $1.20 vs $1.10 Estimate; Q4 EPS $2.10 beat $2.00 Estimate",
            "earnings",
        ),
        ("TE Connectivity Sees Q1 EPS $1.20; Q4 EPS $2.10 on a comparable basis", "earnings"),
    ],
)
def test_earnings_guidance_and_actual_classification(headline: str, expected: str) -> None:
    """Skip guidance alone while keeping actual and uncertain mixed results deliverable."""
    assert classify_news(_item(("TEL",), "Benzinga Newsdesk", headline)) == expected


@pytest.mark.parametrize(
    ("symbols", "author", "expected"),
    [
        (("TEL",), "Someone else", "unapproved_author"),
        (("TSX:TEL",), "Someone else", "non_us_exchange"),
        (("TSX:TEL",), "Benzinga Newsdesk", "non_us_exchange"),
    ],
)
def test_guidance_keeps_symbol_and_author_rejection_precedence(
    symbols: tuple[str, ...], author: str, expected: str
) -> None:
    """Apply the existing eligibility gates before earnings analysis."""
    headline = "TE Connectivity Sees Q1 GAAP EPS $3.04 vs $2.81 Est"
    assert classify_news(_item(symbols, author, headline)) == expected
