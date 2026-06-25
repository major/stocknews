use regex::Regex;
use std::sync::LazyLock;

static MAINTAINS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^([\w\s]+) (Maintains|Reiterates) (.*) on (.+),").unwrap());
static ACTION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^([\w\s]+) (Downgrades|Upgrades|Initiates Coverage) (?:on\s)*([\w\s]+) (?:with|to) (.+),").unwrap()
});
static PRICE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\$([\d\.]+)").unwrap());
static PRICE_ACTION_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r", (Lowers|Maintains|Raises|Announces)").unwrap());

#[derive(Debug, Default, Clone, PartialEq)]
pub struct AnalystNews {
    pub headline: String,
    pub firm: String,
    pub action: String,
    pub guidance: String,
    pub stock: String,
    pub price_target_action: String,
    pub price_target: f64,
}

impl AnalystNews {
    pub fn new(headline: impl Into<String>) -> Self {
        let mut news = Self {
            headline: headline.into(),
            ..Self::default()
        };
        news.parse();
        news
    }

    fn parse(&mut self) {
        self.parse_analyst_action();
        self.price_target = self
            .extract_value(&PRICE_RE)
            .parse::<f64>()
            .unwrap_or_default();
        self.price_target_action = self.extract_value(&PRICE_ACTION_RE);
    }

    fn parse_analyst_action(&mut self) {
        if let Some(captures) = MAINTAINS_RE.captures(&self.headline) {
            self.firm = captures[1].to_string();
            self.action = captures[2].to_string();
            self.guidance = captures[3].trim_end_matches("Rating").trim().to_string();
            self.stock = captures[4].to_string();
            return;
        }

        if let Some(captures) = ACTION_RE.captures(&self.headline) {
            self.firm = captures[1].to_string();
            self.action = captures[2].to_string();
            self.stock = captures[3].to_string();
            self.guidance = captures[4].trim_end_matches("Rating").trim().to_string();
        }
    }

    fn extract_value(&self, regex: &Regex) -> String {
        regex
            .captures(&self.headline)
            .map(|captures| captures[1].to_string())
            .unwrap_or_default()
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
        assert_eq!(news.price_target_action, "Raises");
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
        assert_eq!(news.price_target_action, "Raises");
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
        assert_eq!(news.price_target_action, "Lowers");
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
        assert_eq!(news.price_target_action, "Announces");
        assert_eq!(news.price_target, 850.0);
    }

    #[test]
    fn strip_rating_suffix() {
        let news = AnalystNews::new(
            "UBS Downgrades Microsoft to Neutral Rating, Lowers Price Target to $275",
        );
        assert_eq!(news.guidance, "Neutral");
    }
}
