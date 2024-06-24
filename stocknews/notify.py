"""Send Discord notifications on stock news."""

import logging

from discord_webhook import DiscordEmbed, DiscordWebhook

from stocknews.config import DISCORD_EARNINGS_WEBHOOK, STOCK_LOGO, TRANSPARENT_PNG
from stocknews.utils import get_company_name, get_earnings_notification_description

log = logging.getLogger(__name__)


def get_username(symbols: list) -> str:
    """Get the username for the webhook."""
    if symbols:
        return f"{', '.join(symbols)}"

    return "🗞️"


def send_to_discord(symbols: list, headline: str, webhook_url: str) -> None:
    """Send a news item to a Discord webhook."""
    webhook = DiscordWebhook(
        url=webhook_url,
        content=headline,
        rate_limit_retry=True,
        username=get_username(symbols),
    )
    webhook.execute()


def send_earnings_to_discord(symbols: list, headline: str) -> None:
    """Send a news item to a Discord webhook."""
    symbol = symbols[0]
    company_name = get_company_name(headline)

    webhook = DiscordWebhook(
        url=DISCORD_EARNINGS_WEBHOOK,
        rate_limit_retry=True,
    )

    embed = DiscordEmbed(
        title=f"{symbol}: {company_name}",
        description=get_earnings_notification_description(headline),
    )
    embed.set_image(url=TRANSPARENT_PNG)
    embed.set_thumbnail(url=STOCK_LOGO % symbol)
    embed.set_footer(text=f"Raw news: {headline}")

    webhook.add_embed(embed)

    webhook.execute()
