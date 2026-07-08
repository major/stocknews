use regex::Regex;
use std::sync::LazyLock;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PriceTargetAction {
    Lowers,
    Raises,
    Announces,
    Maintains,
}

impl PriceTargetAction {
    fn from_captured_str(s: &str) -> Option<Self> {
        match s {
            "Lowers" => Some(Self::Lowers),
            "Raises" => Some(Self::Raises),
            "Announces" => Some(Self::Announces),
            "Maintains" => Some(Self::Maintains),
            _ => None,
        }
    }
}

static MAINTAINS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^([\w\s]+) (Maintains|Reiterates) (.*) on (.+),").unwrap());
static ACTION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^([\w\s]+) (Downgrades|Upgrades|Initiates Coverage) (?:on\s)*([\w\s]+) (?:with|to) (.+),").unwrap()
});
static PRICE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\$([\d\.]+)").unwrap());
static PRICE_ACTION_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r", (Lowers|Maintains|Raises|Announces)").unwrap());

#[derive(Debug, Clone, PartialEq)]
pub struct AnalystNews {
    pub headline: String,
    pub firm: String,
    pub action: String,
    pub guidance: String,
    pub stock: String,
    pub price_target_action: Option<PriceTargetAction>,
    pub price_target: f64,
}

fn parse_analyst_action(headline: &str) -> (String, String, String, String) {
    if let Some(captures) = MAINTAINS_RE.captures(headline) {
        let firm = captures[1].to_string();
        let action = captures[2].to_string();
        let guidance = captures[3].trim_end_matches("Rating").trim().to_string();
        let stock = captures[4].to_string();
        return (firm, action, guidance, stock);
    }

    if let Some(captures) = ACTION_RE.captures(headline) {
        let firm = captures[1].to_string();
        let action = captures[2].to_string();
        let stock = captures[3].to_string();
        let guidance = captures[4].trim_end_matches("Rating").trim().to_string();
        return (firm, action, guidance, stock);
    }

    (String::new(), String::new(), String::new(), String::new())
}

fn extract_value(headline: &str, regex: &Regex) -> String {
    regex
        .captures(headline)
        .map(|captures| captures[1].to_string())
        .unwrap_or_default()
}

impl AnalystNews {
    pub fn new(headline: impl Into<String>) -> Self {
        let headline = headline.into();
        let (firm, action, guidance, stock) = parse_analyst_action(&headline);
        let price_target = extract_value(&headline, &PRICE_RE)
            .parse::<f64>()
            .unwrap_or_default();
        let price_target_action = PRICE_ACTION_RE
            .captures(&headline)
            .and_then(|captures| PriceTargetAction::from_captured_str(&captures[1]));
        Self {
            headline,
            firm,
            action,
            guidance,
            stock,
            price_target,
            price_target_action,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_maintains_headline() {
        let news =
            AnalystNews::new("Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223");
        assert_eq!(news.firm, "Goldman Sachs");
        assert_eq!(news.action, "Maintains");
        assert_eq!(news.guidance, "Buy");
        assert_eq!(news.stock, "Apple");
        assert_eq!(news.price_target_action, Some(PriceTargetAction::Raises));
        assert_eq!(news.price_target, 223.0);
    }

    #[test]
    fn parse_upgrades_headline() {
        let news = AnalystNews::new(
            "Morgan Stanley Upgrades Tesla to Overweight, Raises Price Target to $400",
        );
        assert_eq!(news.firm, "Morgan Stanley");
        assert_eq!(news.action, "Upgrades");
        assert_eq!(news.stock, "Tesla");
        assert_eq!(news.guidance, "Overweight");
        assert_eq!(news.price_target_action, Some(PriceTargetAction::Raises));
        assert_eq!(news.price_target, 400.0);
    }

    #[test]
    fn parse_downgrades_headline() {
        let news = AnalystNews::new(
            "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135",
        );
        assert_eq!(news.firm, "JPMorgan");
        assert_eq!(news.action, "Downgrades");
        assert_eq!(news.stock, "Amazon");
        assert_eq!(news.guidance, "Neutral");
        assert_eq!(news.price_target_action, Some(PriceTargetAction::Lowers));
        assert_eq!(news.price_target, 135.0);
    }

    #[test]
    fn parse_initiates_headline() {
        let news = AnalystNews::new(
            "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850",
        );
        assert_eq!(news.firm, "Piper Sandler");
        assert_eq!(news.action, "Initiates Coverage");
        assert_eq!(news.stock, "Nvidia");
        assert_eq!(news.guidance, "Overweight");
        assert_eq!(news.price_target_action, Some(PriceTargetAction::Announces));
        assert_eq!(news.price_target, 850.0);
    }

    #[test]
    fn no_price_target_action() {
        let news = AnalystNews::new(
            "Goldman Sachs Maintains Buy on Apple, Says Fundamentals Remain Strong",
        );
        assert_eq!(news.firm, "Goldman Sachs");
        assert_eq!(news.stock, "Apple");
        assert_eq!(news.price_target_action, None);
    }

    #[test]
    fn strip_rating_suffix() {
        let news = AnalystNews::new(
            "UBS Downgrades Microsoft to Neutral Rating, Lowers Price Target to $275",
        );
        assert_eq!(news.guidance, "Neutral");
    }
}
