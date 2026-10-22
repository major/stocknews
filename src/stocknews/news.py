"""Filter and classify incoming news messages."""

from html import unescape
from typing import TYPE_CHECKING, Literal

from stocknews.analyst import parse_analyst
from stocknews.earnings import is_earnings_news

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stocknews.models import NewsItem

NewsClassification = Literal[
    "earnings",
    "analyst",
    "news",
    "non_us_exchange",
    "unapproved_author",
]


def classify_news(item: NewsItem) -> NewsClassification:
    """Return the category or first rejection reason for a news item."""
    headline = unescape(item.headline)
    normalized_symbols = us_symbols(item.symbols)
    has_nonblank_symbol = any(symbol.strip() for symbol in item.symbols)
    if has_nonblank_symbol and not normalized_symbols:
        return "non_us_exchange"
    if item.author != "Benzinga Newsdesk":
        return "unapproved_author"

    if is_earnings_news(headline):
        return "earnings"
    parsed = parse_analyst(headline)
    if parsed.action and parsed.stock:
        return "analyst"
    return "news"


def us_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    """Return trimmed symbols that are nonblank and not exchange-qualified."""
    normalized: list[str] = []
    for symbol in symbols:
        trimmed = symbol.strip()
        if trimmed and ":" not in trimmed:
            normalized.append(trimmed)
    return tuple(normalized)
