"""Build Discord embeds and deliver them to configured webhooks."""

import asyncio
import json
from collections.abc import Sequence
from typing import NotRequired, TypedDict

import httpx2

from stocknews.analyst import parse_analyst
from stocknews.earnings import company_name, extract_earnings
from stocknews.models import NewsItem

_RAISES_COLOR = 0x4CAF50
_LOWERS_COLOR = 0xD42020
_WEBHOOK_DEADLINE_SECONDS = 10.0


class EmbedImage(TypedDict):
    """An image URL used by a Discord embed."""

    url: str


class Embed(TypedDict):
    """A Discord embed with the required image and thumbnail fields."""

    title: str
    image: EmbedImage
    thumbnail: EmbedImage
    description: NotRequired[str]
    color: NotRequired[int]
    url: NotRequired[str]


class WebhookPayload(TypedDict):
    """JSON body accepted by a Discord webhook."""

    embeds: list[Embed]


def earnings_payload(
    symbol: str,
    headline: str,
    stock_logo: str,
    transparent_png: str,
) -> WebhookPayload | None:
    """Build an earnings payload, or return None when no earnings data exists."""
    earnings = extract_earnings(headline)
    lines = sorted(
        f"{'💚' if result.beat else '💔'} {kind}: {result.actual} vs. {result.estimate} est."
        for kind, result in earnings.items()
    )
    if not lines:
        return None

    embed = _new_embed(f"{symbol}: {company_name(headline)}", stock_logo, symbol, transparent_png)
    embed["description"] = "\n".join(lines)
    return {"embeds": [embed]}


def analyst_payload(
    symbol: str,
    headline: str,
    stock_logo: str,
    transparent_png: str,
) -> WebhookPayload | None:
    """Build an analyst payload, suppressing newly announced and maintained targets."""
    report = parse_analyst(headline)
    action = report.price_target_action
    if action in {"Announces", "Maintains"}:
        return None

    if action == "Lowers":
        emoji, color = "💔", _LOWERS_COLOR
    elif action == "Raises":
        emoji, color = "💚", _RAISES_COLOR
    else:
        emoji, color = "❓", 0

    embed = _new_embed(
        f"{emoji} {symbol}: {report.stock} ${report.price_target:.2f}",
        stock_logo,
        symbol,
        transparent_png,
    )
    if headline:
        embed["description"] = headline
    embed["color"] = color
    return {"embeds": [embed]}


def news_payload(
    item: NewsItem,
    stock_logo: str,
    transparent_png: str,
) -> WebhookPayload | None:
    """Build a general-news payload, or return None when no symbol is present."""
    if not item.symbols:
        return None

    symbol = item.symbols[0]
    embed = _new_embed(f"{symbol}: {item.headline}", stock_logo, symbol, transparent_png)
    if item.summary:
        embed["description"] = item.summary
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
                    if not 200 <= response.status_code < 300:
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
    embed: Embed = {
        "title": title,
        "image": {"url": transparent_png},
        "thumbnail": {"url": stock_logo.replace("%s", symbol.lower())},
    }
    return embed
