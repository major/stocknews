"""Send Discord notifications on stock news."""

import logging

from discord_webhook import DiscordWebhook

log = logging.getLogger(__name__)


def assemble_message(symbols: list, headline: str) -> str:
    """Assemble a message for the news item."""
    if symbols:
        return f"**{', '.join(symbols)}:** {headline}"

    return headline


def get_username(symbols: list) -> str:
    """Get the username for the webhook."""
    if symbols:
        return f"{', '.join(symbols)}"

    return "🗞️"


def send_to_discord(symbols: list, headline: str, webhook_url: str) -> None:
    """Send a news item to a Discord webhook."""
    message = assemble_message(symbols, headline)
    webhook = DiscordWebhook(
        url=webhook_url,
        content=message,
        rate_limit_retry=True,
        username=get_username(symbols),
    )
    webhook.execute()
