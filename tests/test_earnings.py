"""Tests for earnings classification, extraction, and formatting."""

import pytest

from stocknews.earnings import (
    EarningsAnalysis,
    analyze_earnings,
    company_name,
    describe_earnings,
    extract_earnings,
    is_earnings_news,
    outcome_emoji,
)


def test_earnings_extraction_is_separate_from_classification() -> None:
    """Extract earnings data independently of whether a headline is classified as earnings."""
    extracted = extract_earnings("AAPL Q1 EPS 2.00 beat 1.80; EPS $3.00 missed $2.50 up from last quarter")

    assert extracted["EPS"].actual == "$3.00"
    assert extracted["EPS"].estimate == "$2.50"
    assert extracted["EPS"].outcome == "miss"
    assert not is_earnings_news("AAPL Q1 EPS $2.00 up from $1.80 estimate")
    assert extract_earnings("Apple announces a product") == {}


@pytest.mark.parametrize(
    ("headline", "metric", "actual", "estimate", "outcome"),
    [
        ("Acme Q1 Sales $1.2T beat $1.1T Estimate", "Sales", "$1.2T", "$1.1T", "beat"),
        ("Acme Q1 Sales $900B missed $1T Estimate", "Sales", "$900B", "$1T", "miss"),
        ("Acme Q1 Normalized\tFFO $2 vs $1 Est", "Normalized FFO", "$2", "$1", "vs"),
    ],
)
def test_earnings_extraction_preserves_metric_names_and_units(
    headline: str,
    metric: str,
    actual: str,
    estimate: str,
    outcome: str,
) -> None:
    """Preserve canonical metric names and raw amounts during extraction."""
    result = extract_earnings(headline)[metric]

    assert (result.actual, result.estimate, result.outcome) == (actual, estimate, outcome)


def test_earnings_classification_and_extraction_are_case_insensitive() -> None:
    """Classify and extract fields without case sensitivity."""
    headline = "Acme Q1 sales $1.2T beat $1.1T Estimate"

    assert extract_earnings(headline)["Sales"].actual == "$1.2T"
    assert not is_earnings_news(headline)


@pytest.mark.parametrize(
    ("headline", "kind"),
    [
        # Captured stocknews logs, 2026-10-26 through 2026-10-28
        ("TE Connectivity Sees Q1 GAAP EPS $3.04 vs $2.81 Est", "guidance_only"),
        (
            "TE Connectivity Sees Q1 Adj EPS $3.30 vs $3.01 Est; Sees Sales $5.350B vs $5.131B Est",
            "guidance_only",
        ),
        (
            (
                "Hubbell Raises FY2026 Adj EPS Guidance from $20.25-$20.55 to $20.40-$20.60 vs $20.49 Est; "
                "Raises FY2026 Sales Guidance from $6.780B-$6.897B to $6.897B-$6.956B vs $6.837B Est"
            ),
            "guidance_only",
        ),
        (
            "TE Connectivity Q4 Adj. EPS $3.14 Beats $3.07 Estimate, Sales $5.312B Beat $5.282B Estimate",
            "structured",
        ),
        ("Stellantis Q3 Adj. EPS $(0.01), Sales $43.285B", "structured"),
        ("Acumen Pharmaceuticals Q3 EPS $(0.35) Beats $(0.37) Estimate", "incomplete"),
        (
            "Baldwin Reports Preliminary Q3 Sales Of $480.3M, Up 31% YoY, Adjusted EBITDA Of $106.9M, Up 47% YoY",
            "incomplete",
        ),
        (
            (
                "ZenaTech Targets 2027 Adjusted EBITDA Breakeven On Revenue Growth, "
                "Expanding Drone As A Service Operations; Preliminary Q3 2026 Revenue Of C$13.4\u2013C$14.8 Million"
            ),
            "incomplete",
        ),
    ],
)
def test_captured_earnings_headlines(headline: str, kind: str) -> None:
    """Classify captured headlines without losing actuals or emitting guidance."""
    analysis = analyze_earnings(headline)

    assert analysis.kind == kind
    assert analysis.text == (headline if kind == "structured" else "")
    assert is_earnings_news(headline) == (kind == "structured")


@pytest.mark.parametrize(
    "headline",
    [
        # Captured stocknews logs, 2026-10-26 through 2026-10-28
        (
            "AudioEye FY2026  Adj EPS expected to be more than $1.04 vs $0.98 Est; "
            "Narrows FY2026 Sales Guidance from $43.500M-$44.000M to $43.600M-$43.800M vs $43.619M Est"
        ),
        (
            "Nitto Denko Raises FY2026 GAAP EPS Guidance from $1.33 to $1.36 vs $1.30 Est; "
            "Affirms FY2026 Sales Guidance of $6.965B"
        ),
        (
            "UPDATE: Centene FY2026  Adj EPS expected to be more than $5.20 vs $4.88 Est; "
            "Raises FY2026 Sales Guidance from $193.500B-$197.500B to $196.500B-$200.500B vs $196.334B Est"
        ),
        (
            "Applied Industrial Techs Raises FY2027 GAAP EPS Guidance from $11.65-$12.15 to $11.70-$12.20 "
            "vs $12.03 Est; Affirms FY2027 Sales Guidance of $5.166B-$5.290B vs $5.259B Est"
        ),
        (
            "Royal Caribbean Group Raises FY2026 Adj EPS Guidance from $17.73-$17.87 to $17.85-$17.95 "
            "vs $17.83 Est; Raises FY2026 Sales Guidance of $19.549B vs $19.582B Est."
        ),
        "CORRECTION: Simply Good Foods Sees Q1 Sales $265.354M-$275.560M vs $300.376M Est",
        (
            "CORRECTION: SoFi Technologies Raises FY2026 Adj EPS Guidance from $0.60 to $0.61 vs $0.59 Est; "
            "Raises FY2026 Sales Guidance from $4.750B-$4.850B to $5.040B vs $4.874B Est"
        ),
        (
            "CORRECTION: S&P Global Lowers FY2026 GAAP EPS Guidance from $16.35-$16.60 to $16.25-$16.50 "
            "vs $16.03 Est; Raises FY2026 Sales Guidance from $16.302B-$16.609B to $16.471B-$16.624B"
        ),
        "Conference call:\nCheesecake Factory Sees Q4 Sales $1.040B-$1.050B vs $1.036B Est",
        # Captured Discord alert, 2026-10-28
        (
            "FedEx Raises FY2026 Adj EPS Guidance from $16.90-$18.10 to $18.00-$18.60 vs $17.85 Est; "
            "Revenue Growth Outlook Rises To 12% From 11%"
        ),
    ],
)
def test_captured_additional_guidance_is_suppressed(headline: str) -> None:
    """Suppress fully understood guidance from captured headlines."""
    assert analyze_earnings(headline) == EarningsAnalysis("guidance_only")
    assert not is_earnings_news(headline)
    assert describe_earnings(headline) == ""


@pytest.mark.parametrize(
    "headline",
    [
        "Acme Affirms FY2026 Revenue Guidance at $5B-$6B vs $5.5B Est",
        "Acme FY2026 Revenue expected to be less than $5B vs $5.5B Est",
        "Acme FY2026 Sales expected to be $5B",
    ],
)
def test_reviewed_forecast_variants_are_suppressed(headline: str) -> None:
    """Recognize the requested narrow guidance variants."""
    assert analyze_earnings(headline) == EarningsAnalysis("guidance_only")


@pytest.mark.parametrize(
    "headline",
    [
        # Synthetic cases exercise the reviewed grammar beyond captured examples.
        "acme sees q1 non-gaap eps ~-$0.20 vs $(0.10) est.",
        "Acme Lowers FY2026 Revenue Guidance from $2B-$3B to $1B-$2B",
    ],
)
def test_reviewed_guidance_is_not_earnings(headline: str) -> None:
    """Suppress only fully understood guidance constructions."""
    assert not is_earnings_news(headline)
    assert describe_earnings(headline) == ""


@pytest.mark.parametrize(
    "headline",
    [
        "Analyst Raises Acme Q1 EPS Estimate From $1.00 To $2.00",
        "Analyst Estimates Acme Q1 EPS $2",
        "Acme To Report Q1 Earnings Tomorrow",
        "Acme Q1 Vehicle Sales $2M",
        "Acme Announces New Product",
    ],
)
def test_unrelated_items_are_not_earnings(headline: str) -> None:
    """Exclude estimate revisions, future reporting, and product sales."""
    assert analyze_earnings(headline) == EarningsAnalysis("other")
    assert describe_earnings(headline) == ""


def test_clean_company_guidance_remains_suppressed() -> None:
    """Keep the reviewed company and Sees construction suppressed."""
    headline = "Acme Sees Q1 EPS $3 vs $2 Est"

    assert analyze_earnings(headline) == EarningsAnalysis("guidance_only")


@pytest.mark.parametrize(
    ("headline", "description"),
    [
        (
            "Acme Q1 EPS $2 beat $1 Estimate, Sales $5 missed $6 Estimate",
            "💚 EPS: $2 vs. $1 est.\n💔 Sales: $5 vs. $6 est.",
        ),
        ("Acme Q1 Sales $5B; EPS -$0.10", "📊 EPS: -$0.10\n📊 Sales: $5B"),
        ("Stellantis Q3 Adj. EPS $(0.01), Sales $43.285B", "📊 EPS: $(0.01)\n📊 Sales: $43.285B"),
        (
            "Ambev Q3 Adj. EPS $0.05, Inline, Sales $4.201B Beat $4.090B Estimate",
            "🎯 EPS: $0.05 inline\n💚 Sales: $4.201B vs. $4.090B est.",
        ),
        ("EPS $2, Inline; Sales $5, Inline", "🎯 EPS: $2 inline\n🎯 Sales: $5 inline"),
        (
            "Acme Q2 EPS $2 Up From $1 YoY, Sales $5B Down From $6B YoY",
            "📈 EPS: $2 vs. $1 YoY\n📉 Sales: $5B vs. $6B YoY",
        ),
        ("Acme &amp; Co Q1 EPS &#36;2 beat &#36;1 Estimate; Sales &#36;5", "💚 EPS: $2 vs. $1 est.\n📊 Sales: $5"),
    ],
)
def test_complete_pairs_are_structured(headline: str, description: str) -> None:
    """Format complete, fully understood pairs in EPS then Sales order."""
    analysis = analyze_earnings(headline)

    assert analysis.kind == "structured"
    assert is_earnings_news(headline)
    assert describe_earnings(headline) == description


@pytest.mark.parametrize(
    "headline",
    [
        "Acme Q1 EPS $2, Sales $5; Sees Q2 EPS $3 vs $2 Est",
        "Acme Sees Q2 EPS $3 vs $2 Est; Q1 EPS $2, Sales $5",
    ],
)
def test_guidance_is_stripped_from_complete_pairs(headline: str) -> None:
    """Strip semicolon guidance on either side of a safe comma pair."""
    analysis = analyze_earnings(headline)

    assert analysis.kind == "structured"
    assert describe_earnings(headline) == "📊 EPS: $2\n📊 Sales: $5"


@pytest.mark.parametrize(
    "headline",
    [
        "Acme Q1 EPS $2",
        "Acme Q1 Sales $5; Sees Q2 EPS $3 vs $2 Est",
        "OTHER: Acme Q1 EPS $2, Sales $5",
        "Acme Q1 EPS $2, Sales $5, Unexpected update",
        "Acme Q1 EPS $2, EPS $3, Sales $5",
        "Acme Q1 EPS $2, Sales $5, Revenue $6",
        "Acme Q1 EPS $2, Sales $5, FFO $1",
        "Acme Q1 EPS $2, Vehicle Sales $5",
        "Acme Q4 EPS $2 Beats $1 Estimate, Will Report Full Results Next Week",
        "Acme Beats Estimates And Sees Q1 EPS $3 vs $2 Est; Q4 Sales $5B",
        "Acme FY2026 EPS expected to be $5.00 plus an unknown update",
    ],
)
def test_incomplete_or_uncovered_financial_candidates_are_suppressed(headline: str) -> None:
    """Reject missing, duplicate, qualified, and unexplained financial context."""
    analysis = analyze_earnings(headline)

    assert analysis == EarningsAnalysis("incomplete")
    assert not is_earnings_news(headline)
    assert describe_earnings(headline) == ""


def test_company_name_is_a_prefix() -> None:
    """Derive company names independently of earnings formatting."""
    assert company_name("Acme Q1 EPS $2") == "Acme"
    assert company_name("Q1 Earnings Report") == ""
    assert company_name("Acme Q10 Earnings Report") == "Acme"
    assert company_name("CORRECTION: Cogeco Q4 EPS $1.00") == "Cogeco"


def test_outcome_emoji() -> None:
    """Map extraction outcomes to their independent marker symbols."""
    outcomes = ("beat", "miss", "inline", "up", "down", "vs", "bare")

    assert [outcome_emoji(outcome) for outcome in outcomes] == ["💚", "💔", "🎯", "📈", "📉", "⚖️", "📊"]
