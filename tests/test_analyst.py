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


def test_parse_analyst_requires_action_at_start_of_headline() -> None:
    """Leave analyst fields empty when the headline does not start with an action."""
    expected_fields = ("", "", "", "")
    expected_target_and_action = (223.0, "Raises")
    result = parse_analyst("News: Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223")

    assert (result.firm, result.action, result.guidance, result.stock) == expected_fields
    assert (result.price_target, result.price_target_action) == expected_target_and_action
