import pytest

from stocknews.analyst import AnalystNews


@pytest.fixture
def maintains_headline():
    return "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223"


@pytest.fixture
def upgrades_headline():
    return "Morgan Stanley Upgrades Tesla to Overweight, Raises Price Target to $400"


@pytest.fixture
def downgrades_headline():
    return "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135"


@pytest.fixture
def initiates_headline():
    return "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850"


def test_parse_maintains_headline(maintains_headline):
    news = AnalystNews(maintains_headline)
    assert news.firm == "Goldman Sachs"
    assert news.action == "Maintains"
    assert news.guidance == "Buy"
    assert news.stock == "Apple"
    assert news.price_target_action == "Raises"
    assert news.price_target == 223.0


def test_parse_upgrades_headline(upgrades_headline):
    news = AnalystNews(upgrades_headline)
    assert news.firm == "Morgan Stanley"
    assert news.action == "Upgrades"
    assert news.stock == "Tesla"
    assert news.guidance == "Overweight"
    assert news.price_target_action == "Raises"
    assert news.price_target == 400.0


def test_parse_downgrades_headline(downgrades_headline):
    news = AnalystNews(downgrades_headline)
    assert news.firm == "JPMorgan"
    assert news.action == "Downgrades"
    assert news.stock == "Amazon"
    assert news.guidance == "Neutral"
    assert news.price_target_action == "Lowers"
    assert news.price_target == 135.0


def test_parse_initiates_headline(initiates_headline):
    news = AnalystNews(initiates_headline)
    assert news.firm == "Piper Sandler"
    assert news.action == "Initiates Coverage"
    assert news.stock == "Nvidia"
    assert news.guidance == "Overweight"
    assert news.price_target_action == "Announces"
    assert news.price_target == 850.0


def test_strip_rating_suffix():
    headline = "UBS Downgrades Microsoft to Neutral Rating, Lowers Price Target to $275"
    news = AnalystNews(headline)
    assert news.guidance == "Neutral"
    assert not news.guidance.endswith("Rating")


def test_extract_value():
    news = AnalystNews("Fake headline with $123.45")
    result = news.extract_value(r"\$([\d\.]+)")
    assert result == "123.45"

    result = news.extract_value(r"nonexistent pattern")
    assert result == ""


def test_assign_groups():
    news = AnalystNews("Doesn't matter")
    values = ("Value1", "Value2", "Value3")
    attributes = ["firm", "action", "guidance"]
    news.assign_groups(values, attributes)

    assert news.firm == "Value1"
    assert news.action == "Value2"
    assert news.guidance == "Value3"
