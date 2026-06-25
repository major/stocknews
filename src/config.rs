use std::env;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Settings {
    pub alpaca_api_key: String,
    pub alpaca_api_secret: String,
    pub alpaca_news_stream_url: String,
    pub discord_analyst_webhooks: Vec<String>,
    pub discord_earnings_webhooks: Vec<String>,
    pub discord_news_webhooks: Vec<String>,
    pub stock_logo: String,
    pub transparent_png: String,
    pub blocked_phrases: Vec<String>,
    pub sentry_dsn: String,
    pub sentry_debug: bool,
}

impl Settings {
    pub fn from_env() -> Self {
        Self {
            alpaca_api_key: env_string("ALPACA_API_KEY", ""),
            alpaca_api_secret: env_string("ALPACA_API_SECRET", ""),
            alpaca_news_stream_url: env_string(
                "ALPACA_NEWS_STREAM_URL",
                "wss://stream.data.alpaca.markets/v1beta1/news",
            ),
            discord_analyst_webhooks: csv_values(&env_string("DISCORD_ANALYST_WEBHOOKS", "")),
            discord_earnings_webhooks: csv_values(&env_string("DISCORD_EARNINGS_WEBHOOKS", "")),
            discord_news_webhooks: csv_values(&env_string("DISCORD_NEWS_WEBHOOKS", "")),
            stock_logo: env_string(
                "STOCK_LOGO",
                "https://static.stocktitan.net/company-logo/%s.webp",
            ),
            transparent_png: env_string("TRANSPARENT_PNG", "https://major.io/transparent.png"),
            blocked_phrases: csv_values(&env_string(
                "BLOCKED_PHRASES",
                "if you invested,you would have,would be worth",
            )),
            sentry_dsn: env_string("SENTRY_DSN", ""),
            sentry_debug: env::var("SENTRY_DEBUG")
                .is_ok_and(|value| matches!(value.as_str(), "1" | "true" | "TRUE")),
        }
    }
}

fn env_string(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn csv_values(value: &str) -> Vec<String> {
    value
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn csv_values_strip_empty_values() {
        assert_eq!(
            csv_values("spam, scam,, legit "),
            vec!["spam", "scam", "legit"]
        );
    }
}
