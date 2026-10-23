"""Build Discord embeds and deliver them to configured webhooks."""

import asyncio
import json
from typing import TYPE_CHECKING, NotRequired, TypedDict

import httpx2

from stocknews.analyst import parse_analyst
from stocknews.earnings import company_name, describe_earnings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stocknews.analyst import AnalystReport
    from stocknews.models import NewsItem

_RAISES_COLOR = 0x4CAF50
_LOWERS_COLOR = 0xD42020
_HTTP_SUCCESS_STATUS_MIN = 200
_HTTP_SUCCESS_STATUS_MAX_EXCLUSIVE = 300
_WEBHOOK_DEADLINE_SECONDS = 10.0
_AUTHOR_NAME_LIMIT = 256
_USERNAME_LIMIT = 80


class EmbedImage(TypedDict):
    """An image URL used by a Discord embed."""

    url: str


class EmbedAuthor(TypedDict):
    """An embed author line with an optional compact icon."""

    name: str
    icon_url: NotRequired[str]


class Embed(TypedDict):
    """A Discord embed with optional title, media, and author fields."""

    title: NotRequired[str]
    image: NotRequired[EmbedImage]
    thumbnail: NotRequired[EmbedImage]
    author: NotRequired[EmbedAuthor]
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

    title = f"{symbol}: {company_name(headline)}" if symbol else company_name(headline) or headline
    embed = _new_embed(title, stock_logo, symbol, transparent_png)
    embed["description"] = description
    return {"embeds": [embed]}


def analyst_payload(
    symbol: str,
    headline: str,
    stock_logo: str,
    transparent_png: str,
) -> WebhookPayload | None:
    """Build an analyst embed, keeping rating changes even without a target."""
    report = parse_analyst(headline)
    firm = report.firm.strip()
    username = f"Analyst Wire · {firm}" if firm else "Analyst Wire"
    action = report.price_target_action
    rating_action = report.action.casefold()
    is_rating_change = rating_action in {"upgrades", "downgrades"}
    if action in {"Announces", "Maintains"} and not is_rating_change:
        return None

    if rating_action == "downgrades":
        emoji, color = "💔", _LOWERS_COLOR
    elif rating_action == "upgrades":
        emoji, color = "💚", _RAISES_COLOR
    elif action == "Lowers":
        emoji, color = "💔", _LOWERS_COLOR
    elif action == "Raises":
        emoji, color = "💚", _RAISES_COLOR
    else:
        emoji, color = "❓", 0

    if report.firm:
        author_name = _analyst_author_name(report, symbol, emoji)
    elif symbol:
        parts = [f"{emoji} {symbol}", headline]
        author_name = " · ".join(part for part in parts if part)
    else:
        author_name = f"{emoji} {headline}".rstrip()

    author_name, was_truncated = _limit_author_name(author_name)
    author: EmbedAuthor = {"name": author_name}
    if symbol:
        author["icon_url"] = stock_logo.replace("%s", symbol.lower())
    embed: Embed = {"author": author}
    if was_truncated and headline:
        embed["description"] = headline
    embed["color"] = color
    return {"username": username[:_USERNAME_LIMIT], "embeds": [embed]}


def _analyst_author_name(report: AnalystReport, symbol: str, emoji: str) -> str:
    """Format a parsed analyst report as one compact author line."""
    subject = symbol or report.stock
    parts = [f"{emoji} {subject}".strip(), report.firm]
    rating_action = report.action.casefold()
    if report.guidance:
        if rating_action == "upgrades":
            guidance = f"Upgraded to {report.guidance}"
        elif rating_action == "downgrades":
            guidance = f"Downgraded to {report.guidance}"
        else:
            guidance = report.guidance
        parts.append(guidance)
    if report.price_target > 0:
        target = f"Target ${report.price_target:g}"
        if report.price_target_action == "Raises":
            target = f"Target ↑ ${report.price_target:g}"
        elif report.price_target_action == "Lowers":
            target = f"Target ↓ ${report.price_target:g}"
        parts.append(target)
    return " · ".join(part for part in parts if part)


def _limit_author_name(name: str) -> tuple[str, bool]:
    """Keep an embed author within Discord's 256-character name limit."""
    if len(name) <= _AUTHOR_NAME_LIMIT:
        return name, False
    return f"{name[: _AUTHOR_NAME_LIMIT - 1]}…", True


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
