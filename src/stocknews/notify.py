"""Send Discord notifications on stock news."""

import logging

import structlog
from discord_webhook import DiscordEmbed, DiscordWebhook

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


def send_earnings_to_discord(symbols: list, headline: str) -> None:
    """Send an earnings report to Discord webhook(s)."""
    symbol = symbols[0]
    company_name = get_company_name(headline)
    description = get_earnings_notification_description(headline)

    if not description:
        logger.warning("No earnings description found for %s", headline)
        return

    # 📢 Get all configured earnings webhooks
    webhook_urls = settings.get_discord_earnings_webhooks()
    if not webhook_urls:
        logger.debug("No earnings webhooks configured, skipping Discord notification")
        return

    # 🔄 Send to each configured webhook
    for webhook_url in webhook_urls:
        try:
            webhook = DiscordWebhook(
                url=webhook_url,
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
            logger.debug(
                "Earnings notification sent",
                symbol=symbol,
                webhook_url=webhook_url[:50] + "...",
            )
        except Exception as e:
            logger.error(
                "Failed to send earnings notification",
                symbol=symbol,
                webhook_url=webhook_url[:50] + "...",
                error=str(e),
            )


def send_rating_change_to_discord(symbols: list, headline: str) -> None:
    """Send an analyst ratings change to Discord webhook(s)"""
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
            emoji = "❓"
            notification_color = "000000"

    if not notify_for_status:
        return None

    price_target = f"${report.price_target:.2f}"

    # 📢 Get all configured analyst webhooks
    webhook_urls = settings.get_discord_analyst_webhooks()
    if not webhook_urls:
        logger.debug("No analyst webhooks configured, skipping Discord notification")
        return None

    # 🔄 Send to each configured webhook
    for webhook_url in webhook_urls:
        try:
            webhook = DiscordWebhook(url=webhook_url, rate_limit_retry=True)
            embed = DiscordEmbed(
                title=f"{emoji} {symbol}: {report.stock} {price_target}",
                description=headline,
                color=notification_color,
            )
            embed.set_image(url=settings.transparent_png)
            embed.set_thumbnail(url=settings.stock_logo % symbol.lower())

            webhook.add_embed(embed)
            webhook.execute()
            logger.debug(
                "Analyst rating notification sent",
                symbol=symbol,
                webhook_url=webhook_url[:50] + "...",
            )
        except Exception as e:
            logger.error(
                "Failed to send analyst rating notification",
                symbol=symbol,
                webhook_url=webhook_url[:50] + "...",
                error=str(e),
            )


def send_news_to_discord(symbols: list, headline: str, news_item: dict) -> None:
    """Send other news to Discord webhook(s)."""
    symbol = symbols[0]

    summary = news_item.get("summary", "")

    # 📢 Get all configured news webhooks
    webhook_urls = settings.get_discord_news_webhooks()
    if not webhook_urls:
        logger.debug("No news webhooks configured, skipping Discord notification")
        return

    # 🔄 Send to each configured webhook
    for webhook_url in webhook_urls:
        try:
            webhook = DiscordWebhook(
                url=webhook_url,
                rate_limit_retry=True,
            )
            embed = DiscordEmbed(
                title=f"{symbol}: {headline}",
                description=f"{summary}",
                url=news_item.get("url", ""),
            )
            embed.set_image(url=settings.transparent_png)
            embed.set_thumbnail(url=settings.stock_logo % symbol.lower())

            webhook.add_embed(embed)
            webhook.execute()
            logger.debug(
                "News notification sent",
                symbol=symbol,
                webhook_url=webhook_url[:50] + "...",
            )
        except Exception as e:
            logger.error(
                "Failed to send news notification",
                symbol=symbol,
                webhook_url=webhook_url[:50] + "...",
                error=str(e),
            )
