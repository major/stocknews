from string import ascii_lowercase

from hypothesis import given
from hypothesis import strategies as st

from stocknews.earnings import extract_earnings
from stocknews.models import NewsItem
from stocknews.news import classify_news


@given(
    blocked_phrase=st.text(alphabet=ascii_lowercase, min_size=1, max_size=20),
    rejection=st.sampled_from(("symbol_count", "empty_symbol", "non_us_exchange", "unapproved_author")),
)
def test_html_entities_and_case_do_not_override_blocked_phrase_precedence(
    blocked_phrase: str,
    rejection: str,
) -> None:
    encoded_headline = "".join(f"&#{ord(character)};" for character in blocked_phrase)
    rejection_fields = {
        "symbol_count": ((), "Someone else"),
        "empty_symbol": ((" ",), "Someone else"),
        "non_us_exchange": (("TSX:ACME",), "Someone else"),
        "unapproved_author": (("ACME",), "Someone else"),
    }
    symbols, author = rejection_fields[rejection]
    item = NewsItem(symbols=symbols, author=author, headline=encoded_headline)

    assert classify_news(item, (blocked_phrase.upper(),)) == "blocked_phrase"


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
    headline = "; ".join(
        f"{kind} {actual} {outcome} {estimate} Estimate" for kind, actual, outcome, estimate in clauses
    )
    last_clause_by_kind = {
        kind: next(clause for clause in reversed(clauses) if clause[0] == kind) for kind in ("EPS", "Sales")
    }
    expected = {
        kind: (actual, estimate, outcome == "beat") for kind, actual, outcome, estimate in last_clause_by_kind.values()
    }

    extracted = extract_earnings(headline)
    actual = {kind: (result.actual, result.estimate, result.beat) for kind, result in extracted.items()}

    assert actual == expected
