use regex::Regex;
use std::collections::HashMap;
use std::sync::LazyLock;

static EARNINGS_NEWS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(EPS|Sales) (~*\$[\d\.\(\)\-\$\~]+[KMB]*) [\w\s]+ (\$[\d\.\(\)\-\$\~]+[KMB]*)")
        .unwrap()
});
static EARNINGS_DATA_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(EPS|Sales) ([\d\.()$KMBT]+) (\w+) ([\d\.()$KMBT]+)(?: Est(?:imate|\.)?)?")
        .unwrap()
});
static COMPANY_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(.*?) Q[1-4]").unwrap());

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EarningsResult {
    pub actual: String,
    pub estimate: String,
    pub beat: bool,
}

pub fn is_earnings_news(symbols: &[String], headline: &str) -> bool {
    symbols.len() == 1
        && !["up from", "down from"]
            .iter()
            .any(|phrase| headline.to_lowercase().contains(phrase))
        && EARNINGS_NEWS_RE.is_match(headline)
}

pub fn extract_earnings_data(headline: &str) -> HashMap<String, EarningsResult> {
    EARNINGS_DATA_RE
        .captures_iter(headline)
        .map(|captures| {
            let result_type = if captures[1].eq_ignore_ascii_case("eps") {
                "EPS"
            } else {
                "Sales"
            };
            (
                result_type.to_string(),
                EarningsResult {
                    actual: captures[2].to_string(),
                    estimate: captures[4].to_string(),
                    beat: parse_earnings_result(&captures[3]),
                },
            )
        })
        .collect()
}

pub fn parse_earnings_result(raw_result: &str) -> bool {
    raw_result.to_lowercase().contains("beat")
}

pub fn company_name(headline: &str) -> String {
    COMPANY_RE
        .captures(headline)
        .map(|captures| captures[1].to_string())
        .unwrap_or_default()
}

pub fn beat_emoji(value: bool) -> &'static str {
    if value { "💚" } else { "💔" }
}

pub fn earnings_description(headline: &str) -> String {
    let mut lines = extract_earnings_data(headline)
        .into_iter()
        .map(|(key, value)| {
            format!(
                "{} {key}: {} vs. {} est.",
                beat_emoji(value.beat),
                value.actual,
                value.estimate
            )
        })
        .collect::<Vec<_>>();
    lines.sort();
    lines.join("\n")
}

pub fn has_blocked_phrases(headline: &str, blocked_phrases: &[String]) -> bool {
    let headline = headline.to_lowercase();
    blocked_phrases
        .iter()
        .any(|phrase| headline.contains(&phrase.to_lowercase()))
}

pub fn is_analyst_rating_change(headline: &str) -> bool {
    headline.to_lowercase().contains("price target")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn symbols(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn detects_earnings_news() {
        assert!(is_earnings_news(
            &symbols(&["AAPL"]),
            "AAPL Q1 EPS $2.00 vs $1.80 est."
        ));
        assert!(!is_earnings_news(
            &symbols(&["AAPL", "MSFT"]),
            "AAPL Q1 EPS $2.00 vs $1.80 est."
        ));
    }

    #[test]
    fn extracts_earnings_data() {
        let result = extract_earnings_data("AAPL Q1 EPS $2.00 beat $1.80 Estimate");
        assert_eq!(
            result.get("EPS"),
            Some(&EarningsResult {
                actual: "$2.00".to_string(),
                estimate: "$1.80".to_string(),
                beat: true,
            })
        );
    }

    #[test]
    fn extracts_earnings_data_without_estimate_word() {
        let result = extract_earnings_data("AAPL Q1 EPS $2.00 missed $2.10");
        assert_eq!(
            result.get("EPS"),
            Some(&EarningsResult {
                actual: "$2.00".to_string(),
                estimate: "$2.10".to_string(),
                beat: false,
            })
        );
    }

    #[test]
    fn parses_earnings_result() {
        assert!(parse_earnings_result("beat"));
        assert!(!parse_earnings_result("miss"));
    }

    #[test]
    fn extracts_company_name() {
        assert_eq!(company_name("Apple Q1 Earnings Report"), "Apple");
        assert_eq!(company_name("Q1 Earnings Report"), "");
    }

    #[test]
    fn converts_beat_to_emoji() {
        assert_eq!(beat_emoji(true), "💚");
        assert_eq!(beat_emoji(false), "💔");
    }

    #[test]
    fn builds_earnings_description() {
        assert_eq!(
            earnings_description("AAPL Q1 EPS $2.00 beat $1.80 Estimate"),
            "💚 EPS: $2.00 vs. $1.80 est."
        );
    }

    #[test]
    fn detects_blocked_phrases() {
        let blocked = symbols(&["spam", "scam"]);
        assert!(has_blocked_phrases("This is a spam headline", &blocked));
        assert!(!has_blocked_phrases("This is a normal headline", &blocked));
    }

    #[test]
    fn detects_analyst_rating_changes() {
        assert!(is_analyst_rating_change(
            "Apple price target raised to $200"
        ));
        assert!(!is_analyst_rating_change("Apple releases new iPhone"));
    }

    #[test]
    fn returns_empty_data_without_matches() {
        assert!(extract_earnings_data("Apple announces new product launch").is_empty());
    }

    #[test]
    fn rejects_blocked_earnings_headlines() {
        for headline in [
            "AAPL Q1 EPS $2.00 up from $1.80 estimate",
            "MSFT Q2 Sales $50B down from $48B estimate",
        ] {
            assert!(!is_earnings_news(&symbols(&["AAPL"]), headline));
        }
    }
}
