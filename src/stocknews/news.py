"""Filter and classify incoming news messages."""

from collections.abc import Sequence
from html import unescape
from typing import Literal

from stocknews.analyst import parse_analyst
from stocknews.earnings import has_blocked_phrases, is_earnings_news
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

    if len(item.symbols) != 1:
        return "symbol_count"
    symbol = item.symbols[0].strip()
    if not symbol:
        return "empty_symbol"
    if ":" in symbol:
        return "non_us_exchange"
    if item.author != "Benzinga Newsdesk":
        return "unapproved_author"

    if is_earnings_news(headline):
        return "earnings"
    parsed = parse_analyst(headline)
    if parsed.action and parsed.stock:
        return "analyst"
    return "news"
