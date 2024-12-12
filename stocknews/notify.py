"""Send Discord notifications on stock news."""

import logging

from discord_webhook import DiscordEmbed, DiscordWebhook
from mastodon import Mastodon

from stocknews.analyst import AnalystNews
from stocknews.config import (
    DISCORD_ANALYST_WEBHOOK,
    DISCORD_EARNINGS_WEBHOOK,
    MASTODON_SERVER_TOKEN,
    MASTODON_SERVER_URL,
    STOCK_LOGO,
    TRANSPARENT_PNG,
)
from stocknews.utils import get_company_name, get_earnings_notification_description

log = logging.getLogger(__name__)


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
        log.warning("No earnings description found for %s", headline)
        return

    mastodon = Mastodon(
        access_token=MASTODON_SERVER_TOKEN, api_base_url=MASTODON_SERVER_URL
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
        log.warning("No earnings description found for %s", headline)
        return

    webhook = DiscordWebhook(
        url=DISCORD_EARNINGS_WEBHOOK,
        rate_limit_retry=True,
    )
    embed = DiscordEmbed(
        title=f"{symbol}: {company_name}",
        description=description,
    )
    embed.set_image(url=TRANSPARENT_PNG)
    embed.set_thumbnail(url=STOCK_LOGO % symbol)
    # embed.set_footer(text=f"Raw news: {headline}")

    webhook.add_embed(embed)

    webhook.execute()


def send_rating_change_to_discord(symbols: list, headline: str) -> None:
    """Send an analyst ratings change to a Discord webhook"""
    symbol = symbols[0]
    report = AnalystNews(headline)

    match report.price_target_action.lower():
        case "announces":
            emoji = "📢"
            notification_color = "FFFFFF"
        case "lowers":
            emoji = "️💔"
            notification_color = "D42020"
        case "raises":
            emoji = "💚"
            notification_color = "080000"

    price_target = f"${report.price_target:.2f}"

    webhook = DiscordWebhook(
        url=DISCORD_ANALYST_WEBHOOK, rate_limit_retry=True, color=notification_color
    )
    embed = DiscordEmbed(
        title=f"{emoji} {symbol}: {report.stock} {price_target}",
        description=headline,
    )
    embed.set_image(url=TRANSPARENT_PNG)
    embed.set_thumbnail(url=STOCK_LOGO % symbol)
    # embed.set_footer(text=f"Raw news: {headline}")

    webhook.add_embed(embed)

    webhook.execute()
