"""Recognize and format earnings headlines."""

import re
from dataclasses import dataclass
from html import unescape
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

_EARNINGS_METRIC = r"Normalized\s+FFO|AFFO|FFO|EPS|Sales|Revenue"
_EARNINGS_AMOUNT = r"[\d\.()$KMBT~\-]+"
# Matched metric words are normalized to these canonical field keys.
_METRIC_KINDS = {
    "eps": "EPS",
    "sales": "Sales",
    "revenue": "Revenue",
    "ffo": "FFO",
    "affo": "AFFO",
    "normalized ffo": "Normalized FFO",
}

_EARNINGS_DATA_RE = re.compile(
    rf"\b(?P<metric>{_EARNINGS_METRIC})\b\s+(?:of\s+)?(?P<actual>{_EARNINGS_AMOUNT})"
    rf"(?:\s*[,;]?\s+(?:"
    rf"(?P<result>Beats?|Miss(?:es|ed)?)\s+(?P<result_estimate>{_EARNINGS_AMOUNT})(?:\s+Est(?:imate)?\.?)?"
    rf"|(?P<direction>Up|Down)\s+From\s+(?P<prior>{_EARNINGS_AMOUNT})"
    rf"(?:\s+(?P<prior_basis>YoY|Y/Y|Est(?:imate)?\.?))?"
    rf"|vs\.?\s+(?P<compared>{_EARNINGS_AMOUNT})\s+Est(?:imate)?\.?"
    rf"|(?P<inline>Inline)"
    rf"))?",
    re.IGNORECASE | re.ASCII,
)
_PERIOD = r"(?:Q[1-4](?:\s+20\d{2})?|FY(?:20\d{2})?|(?:20\d{2}\s+)?(?:First|Second|Third|Fourth) Quarter)"
_BASIS = r"(?:GAAP|Adj\.?|Adjusted|non-GAAP)"
# A clause prefix naming the company, period, and accounting basis is context
# the embed title already carries, so the formatter drops it from leftovers.
_CONTEXT_PREFIX_RE = re.compile(rf"^[^;]*?{_PERIOD}(?:\s+{_BASIS})?\s+", re.IGNORECASE)
_COMPANY_RE = re.compile(r"^(.*?) Q[1-4]")
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
_EDITORIAL_PREFIX_RE = re.compile(r"^(?:correction|update|conference call|reported earlier)\s*[:,]\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class EarningsAnalysis:
    """Classification and safe text for an earnings headline."""

    kind: Literal["guidance_only", "structured", "headline", "other"]
    text: str = ""


Outcome = Literal["beat", "miss", "inline", "up", "down", "vs", "bare"]
# The direction group only matches Up or Down, narrowed here for type checkers.
_DIRECTION_OUTCOMES: dict[str, Outcome] = {"up": "up", "down": "down"}


@dataclass(frozen=True, slots=True, kw_only=True)
class EarningsResult:
    """Actual result and optional comparison for one earnings metric."""

    actual: str
    estimate: str = ""
    outcome: Outcome = "bare"
    yoy: bool = False


@dataclass(frozen=True, slots=True)
class _FieldMatch:
    """One extracted metric comparison and its span inside the source text."""

    kind: str
    result: EarningsResult
    start: int
    end: int
    confident: bool = True


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
    """Extract metric comparisons, keeping the last match for each field."""
    results: dict[str, EarningsResult] = {}
    for field in _field_matches(headline):
        results[field.kind] = field.result
    return results


def _field_matches(text: str) -> list[_FieldMatch]:
    """List metric comparisons in text order, keeping raw matched spans."""
    return [
        _FieldMatch(
            kind=_METRIC_KINDS[" ".join(match.group("metric").lower().split())],
            result=_field_result(match),
            start=match.start(),
            end=match.end(),
        )
        for match in _EARNINGS_DATA_RE.finditer(text)
    ]


def _field_result(match: re.Match[str]) -> EarningsResult:
    """Classify one regex match into the outcome it describes."""
    if match.group("result"):
        word = match.group("result").lower()
        return EarningsResult(
            actual=match.group("actual"),
            estimate=match.group("result_estimate"),
            outcome="beat" if "beat" in word else "miss",
        )
    if match.group("direction"):
        prior_basis = (match.group("prior_basis") or "").lower()
        return EarningsResult(
            actual=match.group("actual"),
            estimate=match.group("prior"),
            outcome=_DIRECTION_OUTCOMES[match.group("direction").lower()],
            yoy=prior_basis.startswith("y"),
        )
    if match.group("compared"):
        return EarningsResult(actual=match.group("actual"), estimate=match.group("compared"), outcome="vs")
    if match.group("inline"):
        return EarningsResult(actual=match.group("actual"), outcome="inline")
    return EarningsResult(actual=match.group("actual"))


def _clause_fields(clause: str) -> list[_FieldMatch]:
    """Flag matches that a forecast word introduces as guidance, not actuals."""
    return [
        _FieldMatch(
            kind=field.kind,
            result=field.result,
            start=field.start,
            end=field.end,
            confident=not _FORECAST_WORD_RE.search(clause[: field.start]),
        )
        for field in _field_matches(clause)
    ]


def outcome_emoji(outcome: Outcome) -> str:
    """Return the display marker for one comparison outcome."""
    return {
        "beat": "💚",
        "miss": "💔",
        "inline": "🎯",
        "up": "📈",
        "down": "📉",
        "vs": "⚖️",
        "bare": "📊",
    }[outcome]


def company_name(headline: str) -> str:
    """Return the company prefix before a Q1 through Q4 marker, if present."""
    match = _COMPANY_RE.search(headline)
    if not match:
        return ""
    return _EDITORIAL_PREFIX_RE.sub("", match.group(1), count=1).strip()


def describe_earnings(headline: str) -> str:
    """Format parsed comparisons as marker lines, keeping leftover text intact."""
    analysis = analyze_earnings(headline)
    if analysis.kind not in {"structured", "headline"}:
        return ""
    return _describe_actuals(analysis.text)


def _describe_actuals(text: str) -> str:
    """Render confident comparisons as marker lines and keep remaining text."""
    clause_fields = [(clause, _clause_fields(clause)) for clause in (c.strip() for c in text.split(";"))]
    # Keep only the last confident match per field, mirroring extract_earnings.
    displayed: dict[str, _FieldMatch] = {}
    for _, fields in clause_fields:
        for field in fields:
            if field.confident:
                displayed[field.kind] = field
    if not displayed:
        return text
    lines = sorted(_result_line(displayed[kind]) for kind in displayed)
    remainders: list[str] = []
    for clause, fields in clause_fields:
        shown = [field for field in fields if displayed.get(field.kind) is field]
        if not shown:
            remainders.append(clause)
            continue
        fragment = _remainder(clause, shown)
        if fragment:
            remainders.append(fragment)
    return "\n".join([*lines, *remainders])


def _result_line(field: _FieldMatch) -> str:
    """Render one extracted field as a marker comparison line."""
    result = field.result
    if not result.estimate:
        suffix = " inline" if result.outcome == "inline" else ""
        return f"{outcome_emoji(result.outcome)} {field.kind}: {result.actual}{suffix}"
    comparison = " YoY" if result.yoy else " est."
    return f"{outcome_emoji(result.outcome)} {field.kind}: {result.actual} vs. {result.estimate}{comparison}"


def _remainder(clause: str, shown: Sequence[_FieldMatch]) -> str:
    """Drop displayed spans and the company, period, and basis prefix."""
    fragment = _remove_spans(clause, [(field.start, field.end) for field in shown])
    fragment = _CONTEXT_PREFIX_RE.sub("", fragment, count=1)
    return re.sub(r"\s+", " ", fragment).strip().strip(" ,;:.")


def _remove_spans(text: str, spans: Sequence[tuple[int, int]]) -> str:
    """Remove matched spans from text, keeping everything else in order."""
    parts: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)
