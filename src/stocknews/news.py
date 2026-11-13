"""Filter and classify incoming news messages."""

from html import unescape
from importlib.resources import files
from typing import TYPE_CHECKING, Literal

from stocknews.analyst import parse_analyst
from stocknews.earnings import analyze_earnings

_EARNINGS_SYMBOLS = frozenset(
    files("stocknews").joinpath("earnings_symbols.txt").read_text(encoding="utf-8").splitlines()
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stocknews.models import NewsItem

NewsClassification = Literal[
    "earnings",
    "unlisted_earnings_symbol",
    "guidance_only",
    "incomplete_earnings",
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

    earnings = analyze_earnings(headline)
    if earnings.kind in {"guidance_only", "incomplete"}:
        return "guidance_only" if earnings.kind == "guidance_only" else "incomplete_earnings"
    if earnings.kind == "structured":
        return (
            "earnings"
            if normalized_symbols and normalized_symbols[0].upper() in _EARNINGS_SYMBOLS
            else "unlisted_earnings_symbol"
        )
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
