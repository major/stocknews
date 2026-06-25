use crate::config::Settings;
use crate::discord::{analyst_payload, earnings_payload, news_payload, send_payload};
use crate::news::{NewsItem, NewsKind, accepted_symbol, classify};
use futures_util::{SinkExt, StreamExt};
use html_escape::decode_html_entities;
use serde_json::{Value, json};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, info, warn};

pub type AppResult<T> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

pub async fn stream_news(settings: &Settings) -> AppResult<()> {
    let (ws, _) = connect_async(&settings.alpaca_news_stream_url).await?;
    let (mut write, mut read) = ws.split();

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
    while let Some(message) = read.next().await {
        let Message::Text(text) = message? else {
            continue;
        };
        for item in serde_json::from_str::<Vec<NewsItem>>(&text)? {
            handle_message(&client, settings, item).await?;
        }
    }
    Ok(())
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
    if response
        .first()
        .and_then(|item| item.get("T"))
        .and_then(Value::as_str)
        == Some("success")
    {
        Ok(())
    } else {
        Err(format!("{action} failed").into())
    }
}

async fn handle_message(
    client: &reqwest::Client,
    settings: &Settings,
    mut item: NewsItem,
) -> AppResult<()> {
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
                send_payload(client, &settings.discord_earnings_webhooks, &payload).await?;
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
                send_payload(client, &settings.discord_analyst_webhooks, &payload).await?;
            }
        }
        Some(NewsKind::News) => {
            warn!(%symbol, headline = %item.headline, "unknown news type");
            if let Some(payload) =
                news_payload(&item, &settings.stock_logo, &settings.transparent_png)
            {
                send_payload(client, &settings.discord_news_webhooks, &payload).await?;
            }
        }
        None => {}
    }
    Ok(())
}
