"""Configuration values for the stocknews package."""

from pydantic_settings import BaseSettings


#####################################################################
# ✨ NEW STYLE SETTINGS USING PYDANTIC SETTINGS ✨
#####################################################################
class Settings(BaseSettings):
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_news_stream_url: str = "wss://stream.data.alpaca.markets/v1beta1/news"

    redis_host: str = "localhost"
    redis_port: int = 6379

    discord_analyst_webhook: str = ""
    discord_earnings_webhook: str = ""
    discord_news_webhook: str = ""

    mastodon_server_url: str = ""
    mastodon_server_token: str = ""

    stock_logo: str = "https://static.stocktitan.net/company-logo/%s.webp"
    transparent_png: str = "https://major.io/transparent.png"
    blocked_phrases: list = ["if you invested", "you would have", "would be worth"]

    sentry_dsn: str = ""
    sentry_debug: bool = False

    sp1500_stocks_file: str = "sp1500_stocks.json"


settings = Settings()
