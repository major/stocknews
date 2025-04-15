"""Send Discord notifications on stock news."""

import logging

import structlog
from discord_webhook import DiscordEmbed, DiscordWebhook
from mastodon import Mastodon

from stocknews.analyst import AnalystNews
from stocknews.config import (
    settings,
)
from stocknews.utils import get_company_name, get_earnings_notification_description

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

logger = structlog.get_logger()


def get_username(symbols: list) -> str:
    """Get the username for the webhook."""
    if symbols:
        return f"{', '.join(symbols)}"

    return "🗞️"


def send_earnings_to_mastodon(symbols: list, headline: str) -> None:
    """Send an earnings report to a Mastodon account."""
    symbol = symbols[0]
    company_name = get_company_name(headline)
    description = get_earnings_notification_description(headline)

    if not description:
        logger.warning("No earnings description found for %s", headline)
        return

    mastodon = Mastodon(
        access_token=settings.mastodon_server_token,
        api_base_url=settings.mastodon_server_url,
    )

    mastodon.status_post(
        f"{symbol}: {company_name}\n\n{description}\n\n#stocks #earnings #{symbol}"
    )


def send_earnings_to_discord(symbols: list, headline: str) -> None:
    """Send an earnings report to a Discord webhook."""
    symbol = symbols[0]
    company_name = get_company_name(headline)
    description = get_earnings_notification_description(headline)

    if not description:
        logger.warning("No earnings description found for %s", headline)
        return

    webhook = DiscordWebhook(
        url=settings.discord_earnings_webhook,
        rate_limit_retry=True,
    )
    embed = DiscordEmbed(
        title=f"{symbol}: {company_name}",
        description=description,
    )
    embed.set_image(url=settings.transparent_png)
    embed.set_thumbnail(url=settings.stock_logo % symbol.lower())
    # embed.set_footer(text=f"Raw news: {headline}")

    webhook.add_embed(embed)

    webhook.execute()


def send_rating_change_to_discord(symbols: list, headline: str) -> None:
    """Send an analyst ratings change to a Discord webhook"""
    symbol = symbols[0]

    # Sorry, Canada. 🇨🇦
    if symbol.startswith("TSX:"):
        return None

    report = AnalystNews(headline)

    notify_for_status = True
    match report.price_target_action.lower():
        case "announces" | "maintains":
            emoji = "📢"
            notification_color = "FFFFFF"
            notify_for_status = False
        case "lowers":
            emoji = "️💔"
            notification_color = "D42020"
        case "raises":
            emoji = "💚"
            notification_color = "4caf50"
        case _:
            emoji = "🤷‍♂️"
            notification_color = "000000"

    if not notify_for_status:
        return None

    price_target = f"${report.price_target:.2f}"

    webhook = DiscordWebhook(
        url=settings.discord_analyst_webhook, rate_limit_retry=True
    )
    embed = DiscordEmbed(
        title=f"{emoji} {symbol}: {report.stock} {price_target}",
        description=headline,
        color=notification_color,
    )
    embed.set_image(url=settings.transparent_png)
    embed.set_thumbnail(url=settings.stock_logo % symbol.lower())

    webhook.add_embed(embed)

    webhook.execute()
