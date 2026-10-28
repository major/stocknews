"""Recognize and format earnings headlines."""

import re
from dataclasses import dataclass
from html import unescape
from typing import Literal

_EARNINGS_DATA_RE = re.compile(
    r"(EPS|Sales) ([\d\.()$KMBT]+) (\w+) ([\d\.()$KMBT]+)(?: Est(?:imate|\.)?)?",
    re.IGNORECASE | re.ASCII,
)
_COMPANY_RE = re.compile(r"^(.*?) Q[1-4]")
_PERIOD = r"(?:Q[1-4](?:\s+20\d{2})?|FY(?:20\d{2})?|(?:20\d{2}\s+)?(?:First|Second|Third|Fourth) Quarter)"
_BASIS = r"(?:GAAP|Adj\.?|Adjusted|non-GAAP)"
_AMOUNT = r"~?-?(?:[A-Z]{1,3})?\$(?:\(?-?\d+(?:\.\d+)?\)?)[KMBT]?"
_RANGE = rf"{_AMOUNT}(?:\s*[-\u2013]\s*{_AMOUNT})?"
_METRIC = r"(?:EPS|Sales|Revenue)"
_GUIDANCE_RE = re.compile(
    rf"(?:Sees\s+(?:{_PERIOD}\s+)?(?:{_BASIS}\s+)?{_METRIC}\s+{_RANGE}"
    rf"|(?:Raises|Lowers|Affirms|Narrows)\s+(?:{_PERIOD}\s+)?(?:{_BASIS}\s+)?"
    rf"{_METRIC}\s+Guidance\s+(?:from\s+{_RANGE}\s+to\s+|(?:of|at)\s+)?{_RANGE}"
    rf"|{_PERIOD}\s+(?:{_BASIS}\s+)?{_METRIC}\s+expected\s+to\s+be\s+"
    rf"(?:(?:more|less)\s+than\s+)?{_RANGE})"
    rf"(?:\s+vs\.?\s+{_AMOUNT}\s+Est(?:imate)?\.?)?\.?",
    re.IGNORECASE,
)
_UNSAFE_PREFIX_RE = re.compile(
    rf"[,;:$]|\b(?:{_METRIC}|earnings|reports?|reported|results?|preliminary|"
    rf"guidance|sees|expects|expected|forecast|beats?|miss(?:es|ed)?|"
    rf"swings?|profits?|loss(?:es)?|income|{_PERIOD})\b",
    re.IGNORECASE,
)
_CANDIDATE_RE = re.compile(rf"\b{_PERIOD}\s+(?:{_BASIS}\s+)?{_METRIC}\s+(?:of\s+)?{_AMOUNT}", re.IGNORECASE)
_EXPECTED_CANDIDATE_RE = re.compile(
    rf"\b{_PERIOD}\s+(?:{_BASIS}\s+)?{_METRIC}\s+expected\s+to\s+be\s+"
    rf"(?:(?:more|less)\s+than\s+)?{_AMOUNT}",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    rf"\b(?:EPS|Sales)\s+{_AMOUNT}\s+(?:beats?|miss(?:es|ed)?|vs\.?)\s+{_AMOUNT}", re.IGNORECASE
)
_REPORTED_RE = re.compile(
    rf"\b(?:reports?|reported|posts?|posted|announces?|announced)\b[^;]*?"
    rf"\b(?:earnings|results|preliminary)\b[^;]*?\b{_METRIC}\s+(?:of\s+)?{_AMOUNT}",
    re.IGNORECASE,
)
_EXCLUDED_RE = re.compile(
    r"\b(?:analysts?\b[^;]*\b(?:estimates?|forecast)|"
    r"(?:raises?|lowers?|cuts?|revises?)\s+(?:its\s+)?(?:price target|(?:EPS|sales|revenue) estimates?)|"
    r"(?:to|will|plans? to|scheduled to)\s+(?:report|announce|release)\b)",
    re.IGNORECASE,
)
_SIMPLE_AMOUNT = r"\$\d+(?:\.\d+)?[KMBT]?"
_SIMPLE_FIELD = rf"(?:EPS|Sales)\s+{_SIMPLE_AMOUNT}\s+(?:beats?|miss(?:es|ed)?)\s+{_SIMPLE_AMOUNT}\s+Est(?:imate)?\.?"
_SIMPLE_RE = re.compile(rf"(?:{_SIMPLE_FIELD})(?:\s*[,;]\s*{_SIMPLE_FIELD})?", re.IGNORECASE)
_FORECAST_WORD_RE = re.compile(
    r"\b(?:sees|expects?|expected|forecast|guidance|raises|lowers|affirms|narrows)\b", re.IGNORECASE
)
_OUTLOOK_WORD_RE = re.compile(r"\b(?:outlook|guidance)\b", re.IGNORECASE)
_RESULT_WORD_RE = re.compile(
    r"\b(?:reports?|reported|results?|beats?|miss(?:es|ed)?|profits?|loss(?:es)?|earnings|income)\b",
    re.IGNORECASE,
)
_EDITORIAL_PREFIX_RE = re.compile(r"^(?:correction|update|conference call):\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class EarningsAnalysis:
    """Classification and safe text for an earnings headline."""

    kind: Literal["guidance_only", "structured", "headline", "other"]
    text: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class EarningsResult:
    """Actual and estimated values for one earnings field."""

    actual: str
    estimate: str
    beat: bool


def _guidance_clause(clause: str) -> bool:
    """Recognize a complete reviewed forecast, with at most a safe company prefix."""
    match = _GUIDANCE_RE.search(clause)
    if not match or match.end() != len(clause):
        return False
    prefix = clause[: match.start()].strip()
    return not prefix or not _UNSAFE_PREFIX_RE.search(prefix)


def _outlook_clause(clause: str) -> bool:
    """Recognize free-form outlook text that accompanies reviewed guidance.

    Callers apply this only after another clause matched reviewed guidance, so
    a lone "outlook" mention never hides an otherwise unexplained headline.
    """
    return bool(_OUTLOOK_WORD_RE.search(clause)) and not _RESULT_WORD_RE.search(clause) and not _candidate(clause)


def _candidate(clause: str) -> bool:
    """Find financial actual candidates without treating announcements as results."""
    patterns = (_CANDIDATE_RE, _EXPECTED_CANDIDATE_RE, _COMPARISON_RE, _REPORTED_RE, _GUIDANCE_RE)
    matches = [match for pattern in patterns if (match := pattern.search(clause))]
    if not matches:
        return False
    first_candidate = min(match.start() for match in matches)
    exclusion = _EXCLUDED_RE.search(clause)
    return not exclusion or exclusion.start() > first_candidate


def _structured(headline: str) -> bool:
    """Allow formatting only when simple comparisons cover all financial text."""
    match = _SIMPLE_RE.search(headline)
    if not match or match.end() != len(headline):
        return False
    prefix = headline[: match.start()].strip()
    if prefix:
        period = re.search(rf"\b{_PERIOD}$", prefix, re.IGNORECASE)
        if not period or _UNSAFE_PREFIX_RE.search(prefix[: period.start()]):
            return False
    fields = re.findall(r"\b(?:EPS|Sales)\b", match.group(), re.IGNORECASE)
    return len(fields) == len({field.lower() for field in fields})


def analyze_earnings(headline: str) -> EarningsAnalysis:
    """Classify decoded news conservatively and retain safe original actuals."""
    headline = unescape(headline)
    clauses = [clause.strip() for clause in headline.split(";")]
    guidance_clauses = clauses.copy()
    guidance_clauses[0] = _EDITORIAL_PREFIX_RE.sub("", guidance_clauses[0], count=1)
    guidance = [_guidance_clause(clause) for clause in guidance_clauses]
    # Guidance-only Sees headlines need a company; a mixed headline may
    # instead identify the company in its self-contained actual clause.
    first_sees = re.search(r"\bsees\b", guidance_clauses[0], re.IGNORECASE)
    if (
        guidance[0]
        and first_sees
        and (
            (not guidance_clauses[0][: first_sees.start()].strip() and all(guidance))
            or not re.match(rf"\s+{_PERIOD}\b", guidance_clauses[0][first_sees.end() :], re.IGNORECASE)
        )
    ):
        guidance[0] = False
    if any(guidance):
        guidance = [
            is_guidance or _outlook_clause(clause) for clause, is_guidance in zip(clauses, guidance, strict=True)
        ]
    if all(guidance):
        return EarningsAnalysis("guidance_only")

    candidates = [_candidate(clause) for clause in clauses]
    if not any(candidates):
        # An unexplained clause prevents a reviewed forecast from being dropped.
        return EarningsAnalysis("headline", headline) if any(guidance) else EarningsAnalysis("other")

    # Strip only when every remaining clause independently identifies an actual.
    if any(guidance) and all(
        is_guidance or (candidate and _CANDIDATE_RE.search(clause) and not _FORECAST_WORD_RE.search(clause))
        for clause, is_guidance, candidate in zip(clauses, guidance, candidates, strict=True)
    ):
        actuals = "; ".join(clause for clause, is_guidance in zip(clauses, guidance, strict=True) if not is_guidance)
        return EarningsAnalysis("headline", actuals)

    if not any(guidance) and _structured(headline):
        return EarningsAnalysis("structured", headline)
    return EarningsAnalysis("headline", headline)


def is_earnings_news(headline: str) -> bool:
    """Return whether the headline contains deliverable earnings."""
    return analyze_earnings(headline).kind in {"structured", "headline"}


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
    """Format safe comparisons or preserve the original earnings headline."""
    analysis = analyze_earnings(headline)
    if analysis.kind != "structured":
        return analysis.text
    lines = [
        f"{beat_emoji(result.beat)} {kind}: {result.actual} vs. {result.estimate} est."
        for kind, result in extract_earnings(analysis.text).items()
    ]
    return "\n".join(sorted(lines))
