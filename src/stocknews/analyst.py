"""Parse analyst rating headlines."""

import re
from dataclasses import dataclass

_MAINTAINS_RE = re.compile(r"^([\w\s]+) (Maintains|Reiterates) (.*) on (.+),", re.IGNORECASE | re.ASCII)
_ACTION_RE = re.compile(
    r"^([\w\s]+) (Downgrades|Upgrades|Initiates Coverage) (?:on\s)*([\w\s]+) (?:with|to) (.+),",
    re.IGNORECASE | re.ASCII,
)
_PRICE_RE = re.compile(r"\$([\d\.]+)", re.ASCII)
_PRICE_ACTION_RE = re.compile(r", (Lowers|Maintains|Raises|Announces)")
_WHITESPACE_RE = re.compile(r"\s+", re.ASCII)


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalystReport:
    """Fields parsed from one analyst headline."""

    headline: str
    firm: str
    action: str
    guidance: str
    stock: str
    price_target_action: str | None
    price_target: float


def parse_analyst(headline: str) -> AnalystReport:
    """Parse an analyst headline and its first dollar-denominated price."""
    maintains_match = _MAINTAINS_RE.search(headline)
    if maintains_match is not None:
        firm, action, raw_guidance, stock = maintains_match.groups()
        guidance = _trim_rating(raw_guidance)
    else:
        action_match = _ACTION_RE.search(headline)
        if action_match is None:
            firm = action = guidance = stock = ""
        else:
            firm, action, stock, raw_guidance = action_match.groups()
            guidance = _trim_rating(raw_guidance)

    price_match = _PRICE_RE.search(headline)
    try:
        price_target = float(price_match.group(1)) if price_match else 0.0
    except ValueError:
        price_target = 0.0

    price_action_match = _PRICE_ACTION_RE.search(headline)
    return AnalystReport(
        headline=headline,
        firm=firm,
        action=action,
        guidance=guidance,
        stock=stock,
        price_target_action=price_action_match.group(1) if price_action_match else None,
        price_target=price_target,
    )


def _trim_rating(value: str) -> str:
    value = value.removesuffix("Rating")
    return _WHITESPACE_RE.sub(" ", value).strip()
