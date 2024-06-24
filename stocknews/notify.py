"""Send Discord notifications on stock news."""

import logging

from discord_webhook import DiscordEmbed, DiscordWebhook

from stocknews.config import (
    DISCORD_EARNINGS_WEBHOOK,
    DISCORD_NEWS_WEBHOOK,
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


def send_news_to_discord(symbols: list, headline: str, url: str) -> None:
    """Send a news item to a Discord webhook."""
    symbol = symbols[0]

    webhook = DiscordWebhook(
        url=DISCORD_NEWS_WEBHOOK,
        rate_limit_retry=True,
        username="News Bot",
    )

    embed = DiscordEmbed(
        title=symbol,
        description=f"{headline} [Read more]({url})",
    )
    embed.set_image(url=TRANSPARENT_PNG)
    embed.set_thumbnail(url=STOCK_LOGO % symbol)
    webhook.add_embed(embed)

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
