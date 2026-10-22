"""Tests for parsing analyst-rating headlines."""

import pytest

from stocknews.analyst import parse_analyst


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        (
            "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
            ("Goldman Sachs", "Maintains", "Buy", "Apple", "Raises", 223.0),
        ),
        (
            "Morgan Stanley Upgrades Tesla to Overweight, Raises Price Target to $400",
            ("Morgan Stanley", "Upgrades", "Overweight", "Tesla", "Raises", 400.0),
        ),
        (
            "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135",
            ("JPMorgan", "Downgrades", "Neutral", "Amazon", "Lowers", 135.0),
        ),
        (
            "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850",
            ("Piper Sandler", "Initiates Coverage", "Overweight", "Nvidia", "Announces", 850.0),
        ),
        (
            "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $.. (previously $223)",
            ("Goldman Sachs", "Maintains", "Buy", "Apple", "Raises", 0.0),
        ),
    ],
)
def test_parse_analyst_headline(headline: str, expected: tuple[str, str, str, str, str, float]) -> None:
    """Parse analyst action, rating, stock, and price fields from headlines."""
    result = parse_analyst(headline)
    firm, action, guidance, stock, price_action, price = expected

    assert (result.firm, result.action, result.guidance, result.stock) == (firm, action, guidance, stock)
    assert result.price_target_action == price_action
    assert result.price_target == price


def test_parse_analyst_strips_rating_suffix() -> None:
    """Exclude the word Rating from parsed guidance."""
    expected_guidance_and_target = ("Neutral", 275.0)
    result = parse_analyst("UBS Downgrades Microsoft to Neutral Rating, Lowers Price Target to $275")

    assert (result.guidance, result.price_target) == expected_guidance_and_target


def test_parse_analyst_uses_first_dollar_price() -> None:
    """Use the first dollar amount as the parsed price target."""
    expected = {"price_target": 10.0}
    result = parse_analyst("Goldman Sachs Maintains Buy on Apple $10, Raises Price Target to $223")

    assert result.price_target == expected["price_target"]


def test_parse_analyst_action_is_case_insensitive_but_price_action_is_not() -> None:
    """Match analyst actions without regard to case but preserve price-action case."""
    expected_action_and_target = ("upgrades", 200.0)
    result = parse_analyst("Baird upgrades Apple to Outperform, raises Price Target to $200")

    assert (result.action, result.price_target) == expected_action_and_target
    assert result.price_target_action is None


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        (
            "Argus Research Downgrades Alcoa to Hold",
            ("Argus Research", "Downgrades", "Alcoa", "Hold", None, 0.0),
        ),
        (
            "Argus Research Upgrades Equifax to Buy",
            ("Argus Research", "Upgrades", "Equifax", "Buy", None, 0.0),
        ),
        (
            "Citizens Upgrades Omega Healthcare Invts to Market Outperform",
            ("Citizens", "Upgrades", "Omega Healthcare Invts", "Market Outperform", None, 0.0),
        ),
        (
            "HC Wainwright & Co. Reiterates Buy on 4D Molecular Therapeutics, Maintains $36 Price Target",
            ("HC Wainwright & Co.", "Reiterates", "4D Molecular Therapeutics", "Buy", "Maintains", 36.0),
        ),
        (
            (
                "HC Wainwright & Co. Initiates Coverage On Parabilis Medicines with Buy Rating, "
                "Announces Price Target of $40"
            ),
            ("HC Wainwright & Co.", "Initiates Coverage", "Parabilis Medicines", "Buy", "Announces", 40.0),
        ),
        (
            "Wedbush Assumes Rithm Capital at Outperform, Announces Price Target of $10",
            ("Wedbush", "Assumes", "Rithm Capital", "Outperform", "Announces", 10.0),
        ),
        (
            "Wedbush Assumes Rithm Property Trust at Outperform, Announces Price Target of $13.5",
            ("Wedbush", "Assumes", "Rithm Property Trust", "Outperform", "Announces", 13.5),
        ),
        (
            "Wedbush Assumes Federal Home Loan at Outperform, Announces Price Target of $6",
            ("Wedbush", "Assumes", "Federal Home Loan", "Outperform", "Announces", 6.0),
        ),
        (
            "Wedbush Assumes Federal National Mortgage at Neutral, Announces Price Target of $3.5",
            ("Wedbush", "Assumes", "Federal National Mortgage", "Neutral", "Announces", 3.5),
        ),
        (
            "Wedbush Assumes Burford Capital at Neutral, Announces Price Target of $3.65",
            ("Wedbush", "Assumes", "Burford Capital", "Neutral", "Announces", 3.65),
        ),
        (
            "Baird Upgrades AT&T to Outperform, Raises Price Target to $25",
            ("Baird", "Upgrades", "AT&T", "Outperform", "Raises", 25.0),
        ),
        (
            "Baird Upgrades Knight-Swift to Outperform, Raises Price Target to $92",
            ("Baird", "Upgrades", "Knight-Swift", "Outperform", "Raises", 92.0),
        ),
        (
            "Baird Upgrades McDonald's to Buy, Raises Price Target to $50",
            ("Baird", "Upgrades", "McDonald's", "Buy", "Raises", 50.0),
        ),
        (
            "Baird Upgrades A.O. Smith to Buy, Raises Price Target to $80",
            ("Baird", "Upgrades", "A.O. Smith", "Buy", "Raises", 80.0),
        ),
        (
            "argus research upgrades Equifax to buy",
            ("argus research", "upgrades", "Equifax", "buy", None, 0.0),
        ),
        (
            "argus research downgrades Alcoa to hold",
            ("argus research", "downgrades", "Alcoa", "hold", None, 0.0),
        ),
    ],
)
def test_parse_analyst_extended_headlines(
    headline: str,
    expected: tuple[str, str, str, str, str | None, float],
) -> None:
    """Parse supported analyst headlines with punctuated names and ratings."""
    result = parse_analyst(headline)

    assert (result.firm, result.action, result.stock, result.guidance) == expected[:4]
    assert (result.price_target_action, result.price_target) == expected[4:]


@pytest.mark.parametrize(
    ("rating", "suffix"),
    [
        ("Buy", " Rating"),
        ("Hold", ""),
        ("Sell", ""),
        ("Neutral", ""),
        ("Overweight", ""),
        ("Underweight", ""),
        ("Equal-Weight", ""),
        ("Equal Weight", ""),
        ("Outperform", ""),
        ("Underperform", ""),
        ("Market Outperform", ""),
        ("Market Perform", ""),
        ("Market Underperform", ""),
        ("Sector Outperform", ""),
        ("Sector Perform", ""),
        ("Sector Underperform", ""),
        ("Strong Buy", ""),
        ("Strong Sell", ""),
        ("Positive", ""),
        ("Negative", ""),
    ],
)
def test_parse_analyst_accepts_supported_targetless_ratings(rating: str, suffix: str) -> None:
    """Recognize only supported targetless rating phrases and optional Rating suffix."""
    result = parse_analyst(f"Argus Research Upgrades Equifax to {rating}{suffix}")

    assert (result.firm, result.action, result.stock, result.guidance) == (
        "Argus Research",
        "Upgrades",
        "Equifax",
        rating,
    )


@pytest.mark.parametrize(
    "headline",
    [
        "News: Argus Research Upgrades Equifax to Buy",
        "Acme: Upgrades Equifax to Buy",
        "Acme Upgrades Software to Version 2",
        "Acme Assumes Control of Operations at Noon",
        "Argus Research Upgrades Equifax to",
        "Argus Research Upgrades to Buy",
        "Argus Research Upgrades Equifax",
        "Teslato Hold",
    ],
)
def test_parse_analyst_rejects_unrecognized_targetless_headlines(headline: str) -> None:
    """Reject prefixed, malformed, and non-rating targetless headlines."""
    result = parse_analyst(headline)

    assert (result.firm, result.action, result.guidance, result.stock) == ("", "", "", "")


def test_parse_analyst_requires_action_at_start_of_headline() -> None:
    """Leave analyst fields empty when the headline does not start with an action."""
    expected_fields = ("", "", "", "")
    expected_target_and_action = (223.0, "Raises")
    result = parse_analyst("News: Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223")

    assert (result.firm, result.action, result.guidance, result.stock) == expected_fields
    assert (result.price_target, result.price_target_action) == expected_target_and_action
