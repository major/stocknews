use crate::config::Settings;
use crate::discord::{
    WebhookPayload, analyst_payload, earnings_payload, news_payload, send_payload,
};
use crate::news::{NewsItem, NewsKind, SkipReason, accepted_symbol, classify, skip_reason};
use futures_util::{SinkExt, StreamExt};
use html_escape::decode_html_entities;
use serde_json::{Value, json};
use std::time::Duration;
use tokio::time::{Instant, MissedTickBehavior, interval, sleep};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, info, warn};

pub type AppResult<T> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

/// How often we proactively ping the server. This also flushes any
/// automatic pong replies tungstenite queued on our behalf, since those
/// only go out when the write half is polled.
const PING_INTERVAL: Duration = Duration::from_secs(30);
/// How long we tolerate silence (no message, ping tick, or pong) before
/// treating the connection as dead and forcing a reconnect. A live feed
/// legitimately goes quiet for hours outside market hours, but a dead TCP
/// connection with no FIN/RST looks identical to `read.next()` forever,
/// so this timeout is the only way to detect it. Reset only when a frame
/// actually arrives, not on every loop iteration.
const READ_TIMEOUT: Duration = Duration::from_secs(120);
/// Backoff between reconnect attempts so a persistent outage doesn't spin
/// the CPU or hammer Alpaca with connection attempts.
const RECONNECT_DELAY: Duration = Duration::from_secs(5);

/// Reconnects to the news stream whenever it errors out or ends, instead of
/// letting a single disconnect (or a stalled connection) stop the whole
/// process from sending Discord alerts.
pub async fn run(settings: &Settings) {
    loop {
        if let Err(error) = stream_news(settings).await {
            warn!(%error, "news stream disconnected, reconnecting");
        }
        sleep(RECONNECT_DELAY).await;
    }
}

async fn stream_news(settings: &Settings) -> AppResult<()> {
    let (ws, _) = connect_async(&settings.alpaca_news_stream_url).await?;
    let (mut write, mut read) = ws.split();

    expect_success(&mut read, "connection").await?;

    write
        .send(Message::Text(
            json!({
                "action": "auth",
                "key": settings.alpaca_api_key,
                "secret": settings.alpaca_api_secret,
            })
            .to_string()
            .into(),
        ))
        .await?;
    expect_success(&mut read, "authentication").await?;
    info!("authenticated successfully");

    write
        .send(Message::Text(
            json!({"action": "subscribe", "news": ["*"]})
                .to_string()
                .into(),
        ))
        .await?;
    expect_success(&mut read, "subscription").await?;
    info!("subscribed to news successfully");

    let client = reqwest::Client::new();
    let mut ping_interval = interval(PING_INTERVAL);
    ping_interval.set_missed_tick_behavior(MissedTickBehavior::Delay);
    ping_interval.tick().await; // first tick fires immediately; skip it

    let read_deadline = sleep(READ_TIMEOUT);
    tokio::pin!(read_deadline);

    loop {
        tokio::select! {
            _ = ping_interval.tick() => {
                write.send(Message::Ping(Vec::new().into())).await?;
            }
            () = &mut read_deadline => {
                return Err("no messages received within timeout, reconnecting".into());
            }
            message = read.next() => {
                read_deadline.as_mut().reset(Instant::now() + READ_TIMEOUT);
                let Some(message) = message else {
                    return Err("websocket closed".into());
                };
                let Message::Text(text) = message? else {
                    continue;
                };
                for item in news_items_from_frame(&text) {
                    handle_message(&client, settings, item).await?;
                }
            }
        }
    }
}

async fn expect_success<S>(read: &mut S, action: &str) -> AppResult<()>
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    let Some(message) = read.next().await else {
        return Err(format!("{action} failed: websocket closed").into());
    };
    let Message::Text(text) = message? else {
        return Err(format!("{action} failed: unexpected websocket message").into());
    };
    let response: Vec<Value> = serde_json::from_str(&text)?;
    debug!(?response);
    let kind = response
        .first()
        .and_then(|item| item.get("T"))
        .and_then(Value::as_str);
    match (action, kind) {
        ("subscription", Some("subscription"))
        | ("authentication", Some("authenticated"))
        | (_, Some("success")) => Ok(()),
        _ => Err(format!("{action} failed").into()),
    }
}

fn news_items_from_frame(text: &str) -> Vec<NewsItem> {
    let values: Vec<Value> = match serde_json::from_str(text) {
        Ok(values) => values,
        Err(error) => {
            warn!(%error, "skipping malformed websocket frame");
            return Vec::new();
        }
    };
    if values
        .first()
        .and_then(|item| item.get("T"))
        .and_then(Value::as_str)
        != Some("n")
    {
        debug!(?values, "skipping non-news websocket frame");
        return Vec::new();
    }

    values
        .into_iter()
        .filter_map(|value| match serde_json::from_value(value) {
            Ok(item) => Some(item),
            Err(error) => {
                warn!(%error, "skipping malformed news item");
                None
            }
        })
        .collect()
}

async fn handle_message(
    client: &reqwest::Client,
    settings: &Settings,
    mut item: NewsItem,
) -> AppResult<()> {
    if let Some(reason) = skip_reason(&item) {
        log_skip(reason, &item);
        return Ok(());
    }
    let Some(symbol) = accepted_symbol(&item).map(str::to_string) else {
        return Ok(());
    };

    item.headline = decode_html_entities(&item.headline).into_owned();
    match classify(&item) {
        Some(NewsKind::Earnings) => {
            info!(%symbol, headline = %item.headline, "earnings news");
            if let Some(payload) = earnings_payload(
                &symbol,
                &item.headline,
                &settings.stock_logo,
                &settings.transparent_png,
            ) {
                send_or_log(client, &settings.discord_earnings_webhooks, &payload).await;
            }
        }
        Some(NewsKind::Analyst) => {
            info!(%symbol, headline = %item.headline, "analyst rating change");
            if let Some(payload) = analyst_payload(
                &symbol,
                &item.headline,
                &settings.stock_logo,
                &settings.transparent_png,
            ) {
                send_or_log(client, &settings.discord_analyst_webhooks, &payload).await;
            }
        }
        Some(NewsKind::News) => {
            info!(%symbol, headline = %item.headline, "general news");
            if let Some(payload) =
                news_payload(&item, &settings.stock_logo, &settings.transparent_png)
            {
                send_or_log(client, &settings.discord_news_webhooks, &payload).await;
            }
        }
        None => {}
    }
    Ok(())
}

fn log_skip(reason: SkipReason, item: &NewsItem) {
    let symbol = item.symbols.first().map(String::as_str).unwrap_or("");
    match reason {
        SkipReason::SymbolCount => {
            info!(symbols = ?item.symbols, author = %item.author, headline = %item.headline, "skipping news without exactly one symbol")
        }
        SkipReason::EmptySymbol => {
            info!(author = %item.author, headline = %item.headline, "skipping news with empty symbol")
        }
        SkipReason::NonUsExchange => {
            info!(%symbol, author = %item.author, headline = %item.headline, "skipping non-US exchange")
        }
        SkipReason::UnapprovedAuthor => {
            info!(%symbol, author = %item.author, headline = %item.headline, "skipping non-approved author")
        }
    }
}

async fn send_or_log(client: &reqwest::Client, webhooks: &[String], payload: &WebhookPayload) {
    if let Err(error) = send_payload(client, webhooks, payload).await {
        warn!(%error, "failed to send Discord webhook");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn accepts_subscription_acknowledgment() {
        let mut stream = futures_util::stream::iter([Ok(Message::Text(
            r#"[{"T":"subscription","news":["*"]}]"#.into(),
        ))]);
        expect_success(&mut stream, "subscription").await.unwrap();
    }

    #[test]
    fn skips_non_news_frames() {
        assert!(news_items_from_frame(r#"[{"T":"success","msg":"subscribed"}]"#).is_empty());
    }

    #[test]
    fn skips_malformed_news_frames() {
        assert!(news_items_from_frame(r#"[{"T":"n","symbols":"AAPL"}]"#).is_empty());
    }

    #[test]
    fn parses_news_frames() {
        let items = news_items_from_frame(
            r#"[{"T":"n","symbols":["AAPL"],"author":"Benzinga Newsdesk","headline":"hi"}]"#,
        );
        assert_eq!(items[0].symbols, ["AAPL"]);
        assert_eq!(items[0].headline, "hi");
    }
}
