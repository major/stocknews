"""Configuration values for the stocknews package."""

from pydantic_settings import BaseSettings


#####################################################################
# ✨ NEW STYLE SETTINGS USING PYDANTIC SETTINGS ✨
#####################################################################
class Settings(BaseSettings):
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_news_stream_url: str = "wss://stream.data.alpaca.markets/v1beta1/news"

    # 📢 Discord webhooks - supports multiple webhooks per channel type
    discord_analyst_webhook: str = ""  # Legacy single webhook (deprecated)
    discord_analyst_webhooks: str = ""  # Comma-separated webhook URLs

    discord_earnings_webhook: str = ""  # Legacy single webhook (deprecated)
    discord_earnings_webhooks: str = ""  # Comma-separated webhook URLs

    discord_news_webhook: str = ""  # Legacy single webhook (deprecated)
    discord_news_webhooks: str = ""  # Comma-separated webhook URLs

    mastodon_server_url: str = ""
    mastodon_server_token: str = ""

    stock_logo: str = "https://static.stocktitan.net/company-logo/%s.webp"
    transparent_png: str = "https://major.io/transparent.png"
    blocked_phrases: list = ["if you invested", "you would have", "would be worth"]

    sentry_dsn: str = ""
    sentry_debug: bool = False

    def get_discord_analyst_webhooks(self) -> list[str]:
        """
        📋 Get list of Discord analyst webhook URLs from configuration.

        Combines both the legacy single URL (discord_analyst_webhook) and the new
        comma-separated URLs (discord_analyst_webhooks) into a single list.

        Returns:
            List of Discord webhook URLs (deduplicated and stripped)
        """
        return self._get_webhook_urls(
            self.discord_analyst_webhook, self.discord_analyst_webhooks
        )

    def get_discord_earnings_webhooks(self) -> list[str]:
        """
        📋 Get list of Discord earnings webhook URLs from configuration.

        Combines both the legacy single URL (discord_earnings_webhook) and the new
        comma-separated URLs (discord_earnings_webhooks) into a single list.

        Returns:
            List of Discord webhook URLs (deduplicated and stripped)
        """
        return self._get_webhook_urls(
            self.discord_earnings_webhook, self.discord_earnings_webhooks
        )

    def get_discord_news_webhooks(self) -> list[str]:
        """
        📋 Get list of Discord news webhook URLs from configuration.

        Combines both the legacy single URL (discord_news_webhook) and the new
        comma-separated URLs (discord_news_webhooks) into a single list.

        Returns:
            List of Discord webhook URLs (deduplicated and stripped)
        """
        return self._get_webhook_urls(
            self.discord_news_webhook, self.discord_news_webhooks
        )

    def _get_webhook_urls(self, single_url: str, multi_urls: str) -> list[str]:
        """
        🔧 Internal helper to combine single and multi webhook URLs.

        Args:
            single_url: Legacy single webhook URL
            multi_urls: Comma-separated webhook URLs

        Returns:
            List of webhook URLs (deduplicated and stripped)
        """
        urls: list[str] = []

        # 🔙 Add legacy single URL if present
        if single_url:
            urls.append(single_url.strip())

        # ➕ Add comma-separated URLs if present
        if multi_urls:
            # Split by comma and strip whitespace
            new_urls = [url.strip() for url in multi_urls.split(",")]
            # Filter out empty strings
            new_urls = [url for url in new_urls if url]
            urls.extend(new_urls)

        # 🎯 Deduplicate while preserving order
        seen: set[str] = set()
        deduplicated: list[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                deduplicated.append(url)

        return deduplicated


settings = Settings()
