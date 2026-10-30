"""Property tests for ticker eligibility and earnings extraction."""

from string import ascii_uppercase

from hypothesis import given
from hypothesis import strategies as st

from stocknews.earnings import extract_earnings
from stocknews.models import NewsItem
from stocknews.news import classify_news, us_symbols


@given(
    us_tickers=st.lists(
        st.text(alphabet=ascii_uppercase, min_size=1, max_size=6),
        min_size=1,
        max_size=8,
    ),
)
def test_us_symbol_normalization_preserves_us_tickers_with_ineligible_entries(
    us_tickers: list[str],
) -> None:
    """Preserve US tickers through normalization amid blanks and foreign symbols."""
    original_symbols = tuple(us_tickers)
    mixed_symbols: list[str] = []
    for ticker in original_symbols:
        mixed_symbols.extend(("  ", f" TSX:{ticker} ", ticker, "\t"))

    original_item = NewsItem(symbols=original_symbols, author="Benzinga Newsdesk", headline="headline")
    mixed_item = NewsItem(symbols=tuple(mixed_symbols), author="Benzinga Newsdesk", headline="headline")
    normalized_symbols = us_symbols(mixed_symbols)

    assert normalized_symbols == original_symbols
    assert us_symbols(normalized_symbols) == normalized_symbols
    assert classify_news(mixed_item) == classify_news(original_item) == "news"


def _money_amount(units: tuple[str, ...]) -> st.SearchStrategy[str]:
    return st.builds(
        lambda dollars, cents, unit: f"${dollars}.{cents:02d}{unit}",
        st.integers(min_value=0, max_value=999_999),
        st.integers(min_value=0, max_value=99),
        st.sampled_from(units),
    )


@st.composite
def _earnings_clauses(draw: st.DrawFn) -> list[tuple[str, str, str, str]]:
    outcomes = st.sampled_from(("beat", "missed"))
    eps_clauses = draw(
        st.lists(
            st.tuples(_money_amount(("",)), outcomes, _money_amount(("",))),
            min_size=2,
            max_size=4,
        )
    )
    sales_clauses = draw(
        st.lists(
            st.tuples(_money_amount(("", "K", "M", "B", "T")), outcomes, _money_amount(("", "K", "M", "B", "T"))),
            min_size=2,
            max_size=4,
        )
    )
    clauses = [("EPS", *clause) for clause in eps_clauses]
    clauses.extend(("Sales", *clause) for clause in sales_clauses)
    return draw(st.permutations(clauses))


@given(clauses=_earnings_clauses())
def test_earnings_extraction_keeps_last_generated_clause_per_field(
    clauses: list[tuple[str, str, str, str]],
) -> None:
    """Extract the last generated earnings clause for each field."""
    headline = "; ".join(
        f"{kind} {actual} {outcome} {estimate} Estimate" for kind, actual, outcome, estimate in clauses
    )
    last_clause_by_kind = {
        kind: next(clause for clause in reversed(clauses) if clause[0] == kind) for kind in ("EPS", "Sales")
    }
    expected = {
        kind: (actual, estimate, "beat" if outcome == "beat" else "miss")
        for kind, actual, outcome, estimate in last_clause_by_kind.values()
    }

    extracted = extract_earnings(headline)
    actual = {kind: (result.actual, result.estimate, result.outcome) for kind, result in extracted.items()}

    assert actual == expected
