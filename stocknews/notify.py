"""Send Discord notifications on stock news."""

import logging

from discord_webhook import DiscordEmbed, DiscordWebhook
from mastodon import Mastodon

from stocknews.config import (
    DISCORD_EARNINGS_WEBHOOK,
    DISCORD_NEWS_WEBHOOK,
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
        f"{symbol}: {company_name}\n\n{description}\n\n#stocks #markets #finance #earnings #{symbol}"
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
