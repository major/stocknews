"""Build Discord embeds and deliver them to configured webhooks."""

import asyncio
import json
import re
from typing import TYPE_CHECKING, NotRequired, TypedDict

import httpx2

from stocknews.analyst import parse_analyst
from stocknews.earnings import company_name as earnings_company_name
from stocknews.earnings import describe_earnings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stocknews.analyst import AnalystReport
    from stocknews.models import NewsItem

_RAISES_COLOR = 0x4CAF50
_LOWERS_COLOR = 0xD42020
_HTTP_SUCCESS_STATUS_MIN = 200
_HTTP_SUCCESS_STATUS_MAX_EXCLUSIVE = 300
_WEBHOOK_DEADLINE_SECONDS = 10.0
_TITLE_LIMIT = 256
_DESCRIPTION_LIMIT = 4096
_DISPLAY_PRICE_RE = re.compile(r"\$(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


class EmbedImage(TypedDict):
    """An image URL used by a Discord embed."""

    url: str


class Embed(TypedDict):
    """A Discord embed with optional title, media, description, color, and URL."""

    title: NotRequired[str]
    image: NotRequired[EmbedImage]
    thumbnail: NotRequired[EmbedImage]
    description: NotRequired[str]
    color: NotRequired[int]
    url: NotRequired[str]


class WebhookPayload(TypedDict):
    """JSON body accepted by a Discord webhook."""

    embeds: list[Embed]
    username: NotRequired[str]


def earnings_payload(
    symbol: str,
    headline: str,
    stock_logo: str,
    transparent_png: str,
) -> WebhookPayload | None:
    """Build an earnings payload, or return None when no earnings data exists."""
    description = describe_earnings(headline)
    if not description:
        return None

    title = f"{symbol}: {earnings_company_name(headline)}" if symbol else earnings_company_name(headline) or headline
    embed = _new_embed(title, stock_logo, symbol, transparent_png)
    embed["description"] = description
    return {"embeds": [embed]}


def analyst_payload(
    symbol: str,
    headline: str,
    stock_logo: str,
    transparent_png: str,
    company_name: str | None = None,
) -> WebhookPayload | None:
    """Build a compact analyst embed, keeping rating changes without a target."""
    report = parse_analyst(headline)
    rating_action = report.action.casefold()
    if _suppress_analyst_report(report, rating_action):
        return None

    title = _analyst_title(symbol, company_name, report, headline)
    embed: Embed = {"title": _limit_embed_text(title, _TITLE_LIMIT), "color": _analyst_color(report, rating_action)}
    if symbol:
        embed["thumbnail"] = {"url": stock_logo.replace("%s", symbol.lower())}

    description = _analyst_description(report, rating_action)
    if description:
        embed["description"] = _limit_embed_text(description, _DESCRIPTION_LIMIT)
    return {"username": "Analyst Wire", "embeds": [embed]}


def _suppress_analyst_report(report: AnalystReport, rating_action: str) -> bool:
    """Return whether an announced or maintained target has no rating change."""
    return report.price_target_action in {"Announces", "Maintains"} and rating_action not in {
        "upgrades",
        "downgrades",
    }


def _analyst_title(
    symbol: str,
    company_name: str | None,
    report: AnalystReport,
    headline: str,
) -> str:
    """Choose a ticker, parsed stock, or headline as the analyst embed title."""
    if symbol and company_name and company_name.strip():
        return f"{symbol} · {company_name.strip()}"
    return symbol or report.stock or headline


def _analyst_color(report: AnalystReport, rating_action: str) -> int:
    """Choose the existing directional color for an analyst report."""
    if rating_action == "downgrades":
        return _LOWERS_COLOR
    if rating_action == "upgrades":
        return _RAISES_COLOR
    if report.price_target_action == "Lowers":
        return _LOWERS_COLOR
    if report.price_target_action == "Raises":
        return _RAISES_COLOR
    return 0


def _analyst_description(report: AnalystReport, rating_action: str) -> str:
    """Format target, rating, and firm details in stable display order."""
    if not report.firm.strip():
        return report.headline
    parts = [
        _analyst_target(report),
        _analyst_rating(report, rating_action),
        report.firm.strip(),
    ]
    return " · ".join(part for part in parts if part)


def _analyst_target(report: AnalystReport) -> str:
    """Format a positive target while preserving grouped digits from the headline."""
    if report.price_target <= 0:
        return ""
    match = _DISPLAY_PRICE_RE.search(report.headline)
    price = match.group(1) if match else f"{report.price_target:g}"
    integer, decimal_point, fraction = price.partition(".")
    price = f"{int(integer.replace(',', '')):,}{decimal_point}{fraction}"
    if report.price_target_action == "Raises":
        return f"Target raised to ${price}"
    if report.price_target_action == "Lowers":
        return f"Target lowered to ${price}"
    return f"Target ${price}"


def _analyst_rating(report: AnalystReport, rating_action: str) -> str:
    """Format a rating action without changing the parser's guidance text."""
    if not report.guidance:
        return ""
    if rating_action == "upgrades":
        return f"Upgraded to {report.guidance}"
    if rating_action == "downgrades":
        return f"Downgraded to {report.guidance}"
    if rating_action == "maintains":
        return f"{report.guidance} maintained"
    return report.guidance


def _limit_embed_text(value: str, limit: int) -> str:
    """Keep embed text within its Discord character limit, marking truncation."""
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}…"


def news_payload(
    item: NewsItem,
    stock_logo: str,
    transparent_png: str,
) -> WebhookPayload:
    """Build a headline-only general-news payload with a symbol thumbnail."""
    symbol = next(iter(item.symbols), "")
    title = f"{symbol}: {item.headline}" if symbol else item.headline
    embed: Embed = {
        "title": title,
        "thumbnail": {"url": stock_logo.replace("%s", symbol.lower()) if symbol else transparent_png},
    }
    if item.url:
        embed["url"] = item.url
    return {"embeds": [embed]}


async def send_payload(
    client: httpx2.AsyncClient,
    webhooks: Sequence[str],
    payload: WebhookPayload,
    *,
    deadline_seconds: float = _WEBHOOK_DEADLINE_SECONDS,
) -> None:
    """Post to each webhook in order with a per-webhook deadline and sanitized failures."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    headers = {"Content-Type": "application/json"}
    errors: list[str] = []

    for webhook in webhooks:
        try:
            async with asyncio.timeout(deadline_seconds):
                try:
                    request = client.build_request("POST", webhook, content=body, headers=headers)
                except httpx2.InvalidURL:
                    errors.append("build webhook request: invalid webhook URL")
                    continue
                if not request.url.is_absolute_url or not request.url.host:
                    errors.append("build webhook request: invalid webhook URL")
                    continue

                response = await client.send(request, stream=True)
                try:
                    if not _HTTP_SUCCESS_STATUS_MIN <= response.status_code < _HTTP_SUCCESS_STATUS_MAX_EXCLUSIVE:
                        errors.append(f"post webhook: unexpected status {response.status_code}")
                finally:
                    await response.aclose()
        except TimeoutError:
            errors.append("post webhook: deadline exceeded")
        except httpx2.RequestError, httpx2.InvalidURL:
            errors.append("post webhook: request failed")

    if errors:
        raise RuntimeError("; ".join(errors))


def _new_embed(title: str, stock_logo: str, symbol: str, transparent_png: str) -> Embed:
    """Build an embed with the supplied title and symbol-specific thumbnail."""
    embed: Embed = {
        "title": title,
        "image": {"url": transparent_png},
        "thumbnail": {"url": stock_logo.replace("%s", symbol.lower()) if symbol else transparent_png},
    }
    return embed
