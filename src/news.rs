use crate::earnings::{is_analyst_rating_change, is_earnings_news};
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct NewsItem {
    #[serde(default)]
    pub symbols: Vec<String>,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub headline: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub url: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewsKind {
    Earnings,
    Analyst,
    News,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipReason {
    SymbolCount,
    EmptySymbol,
    NonUsExchange,
    UnapprovedAuthor,
}

pub fn accepted_symbol(item: &NewsItem) -> Option<&str> {
    let [symbol] = item.symbols.as_slice() else {
        return None;
    };
    (!symbol.trim().is_empty() && !symbol.contains(':')).then_some(symbol.as_str())
}

pub fn skip_reason(item: &NewsItem) -> Option<SkipReason> {
    let [symbol] = item.symbols.as_slice() else {
        return Some(SkipReason::SymbolCount);
    };
    if symbol.trim().is_empty() {
        return Some(SkipReason::EmptySymbol);
    }
    if symbol.contains(':') {
        return Some(SkipReason::NonUsExchange);
    }
    (!is_allowed_author(item)).then_some(SkipReason::UnapprovedAuthor)
}

pub fn is_allowed_author(item: &NewsItem) -> bool {
    item.author == "Benzinga Newsdesk"
}

pub fn classify(item: &NewsItem) -> Option<NewsKind> {
    accepted_symbol(item)?;
    is_allowed_author(item).then_some(())?;

    if is_earnings_news(&item.symbols, &item.headline) {
        return Some(NewsKind::Earnings);
    }

    if is_analyst_rating_change(&item.headline) {
        return Some(NewsKind::Analyst);
    }

    Some(NewsKind::News)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(symbols: &[&str], author: &str, headline: &str) -> NewsItem {
        NewsItem {
            symbols: symbols.iter().map(|value| value.to_string()).collect(),
            author: author.to_string(),
            headline: headline.to_string(),
            summary: String::new(),
            url: String::new(),
        }
    }

    #[test]
    fn accepts_single_us_symbol() {
        let news = item(&["AAPL"], "Benzinga Newsdesk", "headline");
        assert_eq!(accepted_symbol(&news), Some("AAPL"));
    }

    #[test]
    fn rejects_multiple_and_non_us_symbols() {
        assert_eq!(
            accepted_symbol(&item(&["AAPL", "MSFT"], "Benzinga Newsdesk", "headline")),
            None
        );
        assert_eq!(
            accepted_symbol(&item(&["TSX:SHOP"], "Benzinga Newsdesk", "headline")),
            None
        );
        assert_eq!(
            accepted_symbol(&item(&[""], "Benzinga Newsdesk", "headline")),
            None
        );
    }

    #[test]
    fn explains_skipped_items() {
        assert_eq!(
            skip_reason(&item(&["AAPL", "MSFT"], "Benzinga Newsdesk", "headline")),
            Some(SkipReason::SymbolCount)
        );
        assert_eq!(
            skip_reason(&item(&[""], "Benzinga Newsdesk", "headline")),
            Some(SkipReason::EmptySymbol)
        );
        assert_eq!(
            skip_reason(&item(&["TSX:SHOP"], "Benzinga Newsdesk", "headline")),
            Some(SkipReason::NonUsExchange)
        );
        assert_eq!(
            skip_reason(&item(
                &["AAPL"],
                "Someone Else",
                "AAPL Q1 EPS $2.00 vs $1.80 est."
            )),
            Some(SkipReason::UnapprovedAuthor)
        );
    }

    #[test]
    fn rejects_unapproved_author() {
        assert_eq!(
            classify(&item(
                &["AAPL"],
                "Someone Else",
                "AAPL Q1 EPS $2.00 vs $1.80 est."
            )),
            None
        );
    }

    #[test]
    fn classifies_earnings() {
        assert_eq!(
            classify(&item(
                &["AAPL"],
                "Benzinga Newsdesk",
                "AAPL Q1 EPS $2.00 vs $1.80 est."
            )),
            Some(NewsKind::Earnings)
        );
    }

    #[test]
    fn classifies_analyst() {
        assert_eq!(
            classify(&item(
                &["AAPL"],
                "Benzinga Newsdesk",
                "Apple price target raised to $200"
            )),
            Some(NewsKind::Analyst)
        );
    }

    #[test]
    fn classifies_general_news() {
        assert_eq!(
            classify(&item(
                &["AAPL"],
                "Benzinga Newsdesk",
                "Apple releases new iPhone"
            )),
            Some(NewsKind::News)
        );
    }

    #[test]
    fn deserializes_missing_fields_as_empty() {
        let item: NewsItem = serde_json::from_str(r#"{"headline":"hi"}"#).unwrap();
        assert_eq!(item.headline, "hi");
        assert!(item.symbols.is_empty());
    }
}
