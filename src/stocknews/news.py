"""Filter and classify incoming news messages."""

from html import unescape
from typing import TYPE_CHECKING, Literal

from stocknews.analyst import parse_analyst
from stocknews.earnings import has_blocked_phrases, is_earnings_news

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stocknews.models import NewsItem

NewsClassification = Literal[
    "earnings",
    "analyst",
    "news",
    "blocked_phrase",
    "symbol_count",
    "empty_symbol",
    "non_us_exchange",
    "unapproved_author",
]


def classify_news(item: NewsItem, blocked_phrases: Sequence[str]) -> NewsClassification:
    """Return a category or first skip reason, matching the original filter order."""
    headline = unescape(item.headline)
    if has_blocked_phrases(headline, blocked_phrases):
        return "blocked_phrase"

    symbol_rejection = _symbol_eligibility_rejection(item.symbols)
    if symbol_rejection is not None:
        return symbol_rejection
    if item.author != "Benzinga Newsdesk":
        return "unapproved_author"

    if is_earnings_news(headline):
        return "earnings"
    parsed = parse_analyst(headline)
    if parsed.action and parsed.stock:
        return "analyst"
    return "news"


def _symbol_eligibility_rejection(
    symbols: Sequence[str],
) -> Literal["symbol_count", "empty_symbol", "non_us_exchange"] | None:
    """Return the first rejection for symbol count, content, or exchange."""
    if len(symbols) != 1:
        return "symbol_count"
    symbol = symbols[0].strip()
    if not symbol:
        return "empty_symbol"
    if ":" in symbol:
        return "non_us_exchange"
    return None
