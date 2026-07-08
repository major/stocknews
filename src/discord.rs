use crate::analyst::{AnalystNews, PriceTargetAction};
use crate::earnings::{company_name, earnings_description};
use crate::news::NewsItem;
use serde::Serialize;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct WebhookPayload {
    pub embeds: Vec<Embed>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Embed {
    pub title: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub color: Option<u32>,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub url: String,
    pub image: Image,
    pub thumbnail: Image,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Image {
    pub url: String,
}

pub fn earnings_payload(
    symbol: &str,
    headline: &str,
    stock_logo: &str,
    transparent_png: &str,
) -> Option<WebhookPayload> {
    let description = earnings_description(headline);
    (!description.is_empty()).then(|| WebhookPayload {
        embeds: vec![Embed {
            title: format!("{symbol}: {}", company_name(headline)),
            description,
            color: None,
            url: String::new(),
            image: Image {
                url: transparent_png.to_string(),
            },
            thumbnail: Image {
                url: logo_url(stock_logo, symbol),
            },
        }],
    })
}

pub fn analyst_payload(
    symbol: &str,
    headline: &str,
    stock_logo: &str,
    transparent_png: &str,
) -> Option<WebhookPayload> {
    let report = AnalystNews::new(headline);
    let (emoji, color) = match report.price_target_action {
        Some(PriceTargetAction::Lowers) => ("💔", 0xd42020),
        Some(PriceTargetAction::Raises) => ("💚", 0x4caf50),
        Some(PriceTargetAction::Announces | PriceTargetAction::Maintains) => return None,
        None => ("❓", 0),
    };

    Some(WebhookPayload {
        embeds: vec![Embed {
            title: format!(
                "{emoji} {symbol}: {} ${:.2}",
                report.stock, report.price_target
            ),
            description: headline.to_string(),
            color: Some(color),
            url: String::new(),
            image: Image {
                url: transparent_png.to_string(),
            },
            thumbnail: Image {
                url: logo_url(stock_logo, symbol),
            },
        }],
    })
}

pub fn news_payload(
    item: &NewsItem,
    stock_logo: &str,
    transparent_png: &str,
) -> Option<WebhookPayload> {
    let symbol = item.symbols.first()?;
    Some(WebhookPayload {
        embeds: vec![Embed {
            title: format!("{symbol}: {}", item.headline),
            description: item.summary.clone(),
            color: None,
            url: item.url.clone(),
            image: Image {
                url: transparent_png.to_string(),
            },
            thumbnail: Image {
                url: logo_url(stock_logo, symbol),
            },
        }],
    })
}

pub async fn send_payload(
    client: &reqwest::Client,
    webhook_urls: &[String],
    payload: &WebhookPayload,
) -> Result<(), reqwest::Error> {
    for url in webhook_urls {
        client
            .post(url)
            .json(payload)
            .send()
            .await?
            .error_for_status()?;
    }
    Ok(())
}

fn logo_url(template: &str, symbol: &str) -> String {
    template.replace("%s", &symbol.to_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const LOGO: &str = "https://static.stocktitan.net/company-logo/%s.webp";
    const TRANSPARENT: &str = "https://major.io/transparent.png";

    #[test]
    fn builds_earnings_payload() {
        let payload = earnings_payload(
            "AAPL",
            "Apple Q1 EPS $2.00 beat $1.80 Estimate",
            LOGO,
            TRANSPARENT,
        )
        .unwrap();
        assert_eq!(
            serde_json::to_value(payload).unwrap(),
            json!({
                "embeds": [{
                    "title": "AAPL: Apple",
                    "description": "💚 EPS: $2.00 vs. $1.80 est.",
                    "image": {"url": TRANSPARENT},
                    "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"}
                }]
            })
        );
    }

    #[test]
    fn skips_empty_earnings_payload() {
        assert!(earnings_payload("AAPL", "Apple launches product", LOGO, TRANSPARENT).is_none());
    }

    #[test]
    fn builds_analyst_payload_for_raises() {
        let payload = analyst_payload(
            "AAPL",
            "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
            LOGO,
            TRANSPARENT,
        )
        .unwrap();
        assert_eq!(payload.embeds[0].title, "💚 AAPL: Apple $223.00");
        assert_eq!(payload.embeds[0].color, Some(0x4caf50));
    }

    #[test]
    fn builds_analyst_payload_for_unknown_action() {
        let payload = analyst_payload(
            "AAPL",
            "Goldman Sachs Maintains Buy on Apple, Says Fundamentals Remain Strong",
            LOGO,
            TRANSPARENT,
        )
        .unwrap();
        assert_eq!(payload.embeds[0].color, Some(0));
    }

    #[test]
    fn skips_analyst_payload_for_announces() {
        assert!(analyst_payload(
            "NVDA",
            "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850",
            LOGO,
            TRANSPARENT,
        )
        .is_none());
    }

    #[test]
    fn builds_news_payload() {
        let item = NewsItem {
            symbols: vec!["AAPL".to_string()],
            author: "Benzinga Newsdesk".to_string(),
            headline: "Apple releases new iPhone".to_string(),
            summary: "summary".to_string(),
            url: "https://example.test".to_string(),
        };
        let payload = news_payload(&item, LOGO, TRANSPARENT).unwrap();
        assert_eq!(payload.embeds[0].title, "AAPL: Apple releases new iPhone");
        assert_eq!(payload.embeds[0].description, "summary");
        assert_eq!(payload.embeds[0].url, "https://example.test");
    }
}
