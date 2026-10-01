"""Recognize and format earnings headlines."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

_EARNINGS_NEWS_RE = re.compile(
    r"(EPS|Sales) (~*\$[\d\.\(\)\-\$\~]+[KMB]*) [\w\s]+ (\$[\d\.\(\)\-\$\~]+[KMB]*)",
    re.ASCII,
)
_EARNINGS_DATA_RE = re.compile(
    r"(EPS|Sales) ([\d\.()$KMBT]+) (\w+) ([\d\.()$KMBT]+)(?: Est(?:imate|\.)?)?",
    re.IGNORECASE | re.ASCII,
)
_COMPANY_RE = re.compile(r"^(.*?) Q[1-4]")


@dataclass(frozen=True, slots=True, kw_only=True)
class EarningsResult:
    """Actual and estimated values for one earnings field."""

    actual: str
    estimate: str
    beat: bool


def is_earnings_news(headline: str) -> bool:
    """Return whether the headline has an earnings format suitable for routing."""

    headline_lower = headline.lower()
    return (
        "up from" not in headline_lower
        and "down from" not in headline_lower
        and bool(_EARNINGS_NEWS_RE.search(headline))
    )


def extract_earnings(headline: str) -> dict[str, EarningsResult]:
    """Extract EPS and Sales values, keeping the last match for each field."""

    results: dict[str, EarningsResult] = {}
    for match in _EARNINGS_DATA_RE.finditer(headline):
        kind = "Sales" if match.group(1).upper() == "SALES" else "EPS"
        results[kind] = EarningsResult(
            actual=match.group(2),
            estimate=match.group(4),
            beat=parse_result(match.group(3)),
        )
    return results


def parse_result(raw: str) -> bool:
    """Return whether the result text indicates that the company beat estimates."""

    return "beat" in raw.lower()


def company_name(headline: str) -> str:
    """Return the prefix before a Q1 through Q4 marker, if present."""

    match = _COMPANY_RE.search(headline)
    return match.group(1) if match else ""


def beat_emoji(value: bool) -> str:
    """Return the display marker for a beat or miss."""

    return "💚" if value else "💔"


def describe_earnings(headline: str) -> str:
    """Format extracted earnings fields in deterministic sorted order."""

    lines = [
        f"{beat_emoji(result.beat)} {kind}: {result.actual} vs. {result.estimate} est."
        for kind, result in extract_earnings(headline).items()
    ]
    return "\n".join(sorted(lines))


def has_blocked_phrases(headline: str, blocked_phrases: Sequence[str]) -> bool:
    """Return whether the headline contains a blocked phrase, ignoring case."""

    headline_lower = headline.lower()
    return any(phrase.lower() in headline_lower for phrase in blocked_phrases)
