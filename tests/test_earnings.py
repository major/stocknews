"""Tests for earnings classification, extraction, and formatting."""

import pytest

from stocknews.earnings import (
    beat_emoji,
    company_name,
    describe_earnings,
    extract_earnings,
    has_blocked_phrases,
    is_earnings_news,
)


def test_earnings_classification_excludes_up_or_down_from() -> None:
    """Do not classify headlines that describe values as up or down from a prior period."""
    assert is_earnings_news("AAPL Q1 EPS $2.00 vs $1.80 est.")
    assert not is_earnings_news("AAPL Q1 EPS $2.00 up from $1.80 estimate")
    assert not is_earnings_news("MSFT Q2 Sales $50B down from $48B estimate")


def test_earnings_extraction_is_separate_from_classification() -> None:
    """Extract earnings data independently of whether a headline is classified as earnings."""
    extracted = extract_earnings("AAPL Q1 EPS 2.00 beat 1.80; EPS $3.00 missed $2.50 up from last quarter")

    assert extracted["EPS"].actual == "$3.00"
    assert extracted["EPS"].estimate == "$2.50"
    assert not extracted["EPS"].beat
    assert not is_earnings_news("AAPL Q1 EPS $2.00 up from $1.80 estimate")
    assert extract_earnings("Apple announces a product") == {}


@pytest.mark.parametrize(
    ("headline", "actual", "estimate", "beat"),
    [
        ("Acme Q1 Sales $1.2T beat $1.1T Estimate", "$1.2T", "$1.1T", True),
        ("Acme Q1 Sales $900B missed $1T Estimate", "$900B", "$1T", False),
    ],
)
def test_earnings_extraction_preserves_trillion_and_mixed_units(
    headline: str,
    actual: str,
    estimate: str,
    beat: bool,
) -> None:
    """Preserve trillion and mixed-unit amounts when extracting sales results."""
    result = extract_earnings(headline)["Sales"]

    assert (result.actual, result.estimate, result.beat) == (actual, estimate, beat)


def test_earnings_classification_is_case_sensitive_but_extraction_is_not() -> None:
    """Classify earnings case-sensitively while extracting fields without case sensitivity."""
    headline = "Acme Q1 sales $1.2T beat $1.1T Estimate"

    assert extract_earnings(headline)["Sales"].actual == "$1.2T"
    assert not is_earnings_news(headline)


def test_earnings_description_sorts_fields_and_company_name_is_a_prefix() -> None:
    """Sort earnings description lines and derive the company name from the headline prefix."""
    headline = "Acme Q1 EPS $2.00 beat $1.80 Estimate; Sales $50B missed $48B Estimate"

    assert company_name(headline) == "Acme"
    assert company_name("Q1 Earnings Report") == ""
    assert company_name("Acme Q10 Earnings Report") == "Acme"
    assert describe_earnings(headline) == "💔 Sales: $50B vs. $48B est.\n💚 EPS: $2.00 vs. $1.80 est."
    assert beat_emoji(True) == "💚"
    assert beat_emoji(False) == "💔"


def test_blocked_phrases_ignore_case() -> None:
    """Match configured blocked phrases without regard to case."""
    assert has_blocked_phrases("This would be WORTH it", ("would be worth",))
    assert not has_blocked_phrases("This is normal", ("spam", "scam"))
