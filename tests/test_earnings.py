"""Tests for earnings classification, extraction, and formatting."""

import pytest

from stocknews.earnings import (
    EarningsAnalysis,
    analyze_earnings,
    beat_emoji,
    company_name,
    describe_earnings,
    extract_earnings,
    is_earnings_news,
)


def test_earnings_classification_includes_up_or_down_from() -> None:
    """Keep actuals that compare values with a prior period."""
    assert is_earnings_news("AAPL Q1 EPS $2.00 vs $1.80 est.")
    assert is_earnings_news("AAPL Q1 EPS $2.00 up from $1.80 estimate")
    assert is_earnings_news("MSFT Q2 Sales $50B down from $48B estimate")


def test_earnings_extraction_is_separate_from_classification() -> None:
    """Extract earnings data independently of whether a headline is classified as earnings."""
    extracted = extract_earnings("AAPL Q1 EPS 2.00 beat 1.80; EPS $3.00 missed $2.50 up from last quarter")

    assert extracted["EPS"].actual == "$3.00"
    assert extracted["EPS"].estimate == "$2.50"
    assert not extracted["EPS"].beat
    assert is_earnings_news("AAPL Q1 EPS $2.00 up from $1.80 estimate")
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


def test_earnings_classification_and_extraction_are_case_insensitive() -> None:
    """Classify and extract fields without case sensitivity."""
    headline = "Acme Q1 sales $1.2T beat $1.1T Estimate"

    assert extract_earnings(headline)["Sales"].actual == "$1.2T"
    assert is_earnings_news(headline)


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
            "headline",
        ),
        ("Stellantis Q3 Adj. EPS $(0.01), Sales $43.285B", "headline"),
        ("Acumen Pharmaceuticals Q3 EPS $(0.35) Beats $(0.37) Estimate", "headline"),
        (
            "Baldwin Reports Preliminary Q3 Sales Of $480.3M, Up 31% YoY, Adjusted EBITDA Of $106.9M, Up 47% YoY",
            "headline",
        ),
        (
            (
                "ZenaTech Targets 2027 Adjusted EBITDA Breakeven On Revenue Growth, "
                "Expanding Drone As A Service Operations; Preliminary Q3 2026 Revenue Of C$13.4\u2013C$14.8 Million"
            ),
            "headline",
        ),
    ],
)
def test_captured_earnings_headlines(headline: str, kind: str) -> None:
    """Classify captured headlines without losing actuals or emitting guidance."""
    analysis = analyze_earnings(headline)

    assert analysis.kind == kind
    assert analysis.text == (headline if kind == "headline" else "")
    assert is_earnings_news(headline) == (kind == "headline")
    assert describe_earnings(headline) == analysis.text


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
        "CORRECTION: Nitto Denko Q2 EPS $0.41 Up From $0.38 YoY, Sales $1.801B Down From $1.814B YoY",
        "OTHER: Acme Sees Q1 EPS $3.00 vs $2.50 Est",
        "Acme Q1 EPS $2.00; Q2 EPS expected to be $3.00 plus an unknown update",
        "Acme Q1 EPS $2.00, Q2 EPS expected to be $3.00",
        "UPDATE: Acme Q1 EPS $2.00; Sees Q2 EPS $3.00 vs $2.50 Est; Unknown update",
        "Acme Q1 EPS $2.00, FY2026 Revenue expected to be $5B",
        "Acme FY2026 EPS expected to be $5.00 plus an unknown update",
        "OTHER: Acme FY2026 EPS expected to be $5.00",
        "OTHER: Acme Raises FY2026 EPS Guidance of $5.00",
        "Acme Raises FY2026 EPS Guidance of $5.00 plus unknown update",
        "Acme Q1 EPS $2.00, Acme FY2026 EPS expected to be $5.00 vs $4.00 Est",
    ],
)
def test_forecast_boundaries_preserve_original_headline(headline: str) -> None:
    """Keep actuals, unknown prefixes, and unexplained tails intact."""
    assert analyze_earnings(headline) == EarningsAnalysis("headline", headline)
    assert describe_earnings(headline) == headline


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
        # Synthetic cases cover ambiguous and mixed constructions.
        "Acme Q2 EPS $2.00 Up From $1.80 YoY",
        "Acme Q2 sales $50B Down From $55B YoY",
        "Acme Q2 Revenue $50B",
        "Acme Q2 EPS -$0.10",
        "Acme Q2 EPS ~$0.10",
        "Acme Q2 EPS $2.00",
        "Acme Q2 GAAP EPS $1.00, Adjusted EPS $2.00",
        "Acme Q2 EPS $2.00 Beats $1.90 Estimate, Exceeds Prior Guidance",
        "Acme Q2 EPS $2.00 Beats $1.90 Estimate; Expects Demand To Improve",
        "Acme Q2 EPS $2.00 Beats $1.90 Estimate; Raises FY Guidance",
        "Acme Sees Q3 EPS $3.00 vs $2.90 Est; EPS $2.00 Beats $1.90 Estimate",
        "Acme Sees Q3 EPS $3.00 vs $2.90 Est; Unexpected update",
        "Acme Raises FY2026 EPS Guidance from $2 to $3; Unexpected update",
        "Acme Sees Sales $5B vs $4B Est",
        "Sees Q2 EPS $3.00 vs $2.50 Estimate",
        "Acme Reports Q2 EPS $2.00, Sees Q3 EPS $3.00 vs $2.90 Est",
        "Acme Q2 EPS $2.00; Sees Q3 EPS $3.00 vs $2.90 Est; Sales $5B Beat $4B Estimate",
        "Acme Q2 EPS $2.00; Sales $5B Beats $4B Estimate",
        "Acme Q2 EPS $2.00 Beats $1.90 Estimate; Sees Q3 EPS $3.00 vs $2.90 Est; Unexpected update",
        "Acme Q2 EPS $2.00 Beats $1.90 Estimate, Sees Q3 EPS $3.00 vs $2.90 Est",
    ],
)
def test_actuals_with_context_keep_original_headline(headline: str) -> None:
    """Retain original wording when formatting would lose information."""
    assert is_earnings_news(headline)
    assert describe_earnings(headline) == headline


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("Acme Q4 EPS $2.00; Sees Q1 EPS $3.00 vs $2.90 Est", "Acme Q4 EPS $2.00"),
        ("Acme Sees Q1 EPS $3.00 vs $2.90 Est; Q4 EPS $2.00", "Q4 EPS $2.00"),
        (
            "Sees Q2 EPS $3.00 vs $2.50 Estimate; Example Q1 EPS $2.00 beat $1.80 Estimate",
            "Example Q1 EPS $2.00 beat $1.80 Estimate",
        ),
        ("Acme Q1 EPS $2.00; FY2026 Revenue expected to be $5B", "Acme Q1 EPS $2.00"),
        (
            "UPDATE: Acme FY2026 Revenue expected to be $5B; Acme Q1 EPS $2.00",
            "Acme Q1 EPS $2.00",
        ),
        ("CORRECTION: Acme Q1 EPS $2.00; FY2026 Revenue expected to be $5B", "CORRECTION: Acme Q1 EPS $2.00"),
        ("Acme Q4 EPS $2.00; Raises FY2026 EPS Guidance from $2.00 to $3.00", "Acme Q4 EPS $2.00"),
    ],
)
def test_mixed_clauses_keep_self_contained_actuals(headline: str, expected: str) -> None:
    """Remove reviewed guidance only when all actual clauses stand alone."""
    assert analyze_earnings(headline) == EarningsAnalysis("headline", expected)
    assert describe_earnings(headline) == expected


@pytest.mark.parametrize(
    "headline",
    [
        "Analyst Raises Acme Q1 EPS Estimate From $1.00 To $2.00",
        "Acme To Report Q1 Earnings Tomorrow",
        "Acme Q1 Vehicle Sales $2M",
        "Acme Announces New Product",
    ],
)
def test_unrelated_items_are_not_earnings(headline: str) -> None:
    """Exclude estimate revisions, future reporting, and product sales."""
    assert analyze_earnings(headline) == EarningsAnalysis("other")
    assert describe_earnings(headline) == ""


@pytest.mark.parametrize(
    ("headline", "kind"),
    [
        ("Acme Q4 EPS $2 Beats $1 Estimate, Will Report Full Results Next Week", "headline"),
        ("Acme Will Report Q4 EPS $2 Next Week", "other"),
        ("Analyst Raises EPS Estimate, Acme Q4 EPS $2 Beats $1 Estimate", "other"),
        ("Acme Q4 EPS $2 Beats $1 Estimate, Analyst Raises EPS Estimate", "headline"),
    ],
)
def test_actual_evidence_precedes_or_follows_excluded_introduction(headline: str, kind: str) -> None:
    """Preserve earlier actuals but exclude prospective and analyst introductions."""
    analysis = analyze_earnings(headline)

    assert analysis.kind == kind
    assert analysis.text == (headline if kind == "headline" else "")
    assert describe_earnings(headline) == analysis.text


@pytest.mark.parametrize(
    "headline",
    [
        "Acme Beats Estimates And Sees Q1 EPS $3 vs $2 Est",
        "Acme Swings To Profit And Sees Q1 EPS $3 vs $2 Est",
        "Acme Beats Estimates And Sees Q1 EPS $3 vs $2 Est; Q4 Sales $5B",
        "Acme Misses Estimates And Sees Q1 EPS $3 vs $2 Est; Q4 Sales $5B",
        "Acme Reports Losses And Sees Q1 EPS $3 vs $2 Est; Q4 Sales $5B",
        "Acme Swings To Loss And Sees Q1 EPS $3 vs $2 Est, Q4 Revenue $5B",
        "Acme Posts Income And Sees Q1 EPS $3 vs $2 Est; Q4 EPS $2, Sales $5B",
    ],
)
def test_result_actions_cannot_be_swallowed_by_guidance_prefix(headline: str) -> None:
    """Retain result outcomes that precede an otherwise recognized forecast."""
    assert analyze_earnings(headline) == EarningsAnalysis("headline", headline)
    assert describe_earnings(headline) == headline


def test_clean_company_guidance_remains_suppressed() -> None:
    """Keep the reviewed company and Sees construction suppressed."""
    headline = "Acme Sees Q1 EPS $3 vs $2 Est"

    assert analyze_earnings(headline) == EarningsAnalysis("guidance_only")


def test_analysis_decodes_html_and_keeps_structured_original() -> None:
    """Decode HTML while keeping structured analysis distinct from formatting."""
    headline = "Acme &amp; Co Q1 EPS $2 beat $1 Estimate"
    decoded = "Acme & Co Q1 EPS $2 beat $1 Estimate"

    assert analyze_earnings(headline) == EarningsAnalysis("structured", decoded)
    assert describe_earnings(headline) == "💚 EPS: $2 vs. $1 est."


def test_simple_standalone_comparison_is_structured() -> None:
    """Keep the existing standalone comparison format."""
    headline = "EPS $2 beat $1 Estimate"

    assert analyze_earnings(headline) == EarningsAnalysis("structured", headline)
    assert describe_earnings(headline) == "💚 EPS: $2 vs. $1 est."


@pytest.mark.parametrize("separator", [",", ";"])
def test_simple_two_metric_comparisons_are_structured(separator: str) -> None:
    """Format complete simple comparisons separated by commas or semicolons."""
    headline = f"Acme Q1 EPS $2 beat $1 Estimate{separator} Sales $5 missed $6 Estimate"

    assert analyze_earnings(headline) == EarningsAnalysis("structured", headline)
    assert describe_earnings(headline) == "💔 Sales: $5 vs. $6 est.\n💚 EPS: $2 vs. $1 est."


def test_earnings_description_sorts_fields_and_company_name_is_a_prefix() -> None:
    """Sort earnings description lines and derive the company name from the headline prefix."""
    headline = "Acme Q1 EPS $2.00 beat $1.80 Estimate; Sales $50B missed $48B Estimate"

    assert company_name(headline) == "Acme"
    assert company_name("Q1 Earnings Report") == ""
    assert company_name("Acme Q10 Earnings Report") == "Acme"
    assert describe_earnings(headline) == "💔 Sales: $50B vs. $48B est.\n💚 EPS: $2.00 vs. $1.80 est."
    assert beat_emoji(True) == "💚"
    assert beat_emoji(False) == "💔"
