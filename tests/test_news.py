from stocknews.models import NewsItem
from stocknews.news import classify_news


def _item(symbols: tuple[str, ...], author: str, headline: str) -> NewsItem:
    return NewsItem(symbols=symbols, author=author, headline=headline)


def test_news_rejections_follow_symbol_then_author_order() -> None:
    blocked = ()
    assert classify_news(_item(("AAPL", "MSFT"), "Someone else", "headline"), blocked) == "symbol_count"
    assert classify_news(_item(("  ",), "Someone else", "headline"), blocked) == "empty_symbol"
    assert classify_news(_item(("TSX:SHOP",), "Someone else", "headline"), blocked) == "non_us_exchange"
    assert classify_news(_item(("AAPL",), "Someone else", "headline"), blocked) == "unapproved_author"
    assert classify_news(_item(("AAPL",), "Benzinga Newsdesk", "headline"), blocked) == "news"


def test_news_unescapes_encoded_headline_characters_before_blocking_and_classifying() -> None:
    assert (
        classify_news(
            _item(("AAPL",), "Benzinga Newsdesk", "AAPL Q1 EPS $2.00 vs $1.80 est. &amp; sp&#97;m"),
            ("SPAM",),
        )
        == "blocked_phrase"
    )
    assert (
        classify_news(
            _item(("AAPL",), "Benzinga Newsdesk", "AAPL Q1 EPS &#36;2.00 vs &#36;1.80 est."),
            (),
        )
        == "earnings"
    )
    assert (
        classify_news(
            _item(("AAPL",), "Benzinga Newsdesk", "Baird Upgrades Apple to Outperform, Raises Price Target to $200"),
            (),
        )
        == "analyst"
    )


def test_blocked_phrase_rejection_precedes_symbol_rejection() -> None:
    assert classify_news(_item((), "Someone else", "Spam headline"), ("spam",)) == "blocked_phrase"
