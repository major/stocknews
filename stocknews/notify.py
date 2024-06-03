"""Send Discord notifications on stock news."""

import logging

from discord_webhook import DiscordWebhook

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
