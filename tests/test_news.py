import fakeredis
import httpx

from stocknews import news


def test_get_time_params():
    time_params = news.get_time_params()
    assert "start" in time_params
    assert "end" in time_params
    assert time_params["start"] < time_params["end"]


def test_get_all_news_single_page(httpx_mock):
    fake_news = {"news": [{"symbols": ["AAPL"], "title": "Apple stock is up 10%"}]}
    httpx_mock.add_response(json=fake_news)

    news.REDIS_CONN = fakeredis.FakeStrictRedis(decode_responses=True)

    with httpx.Client() as client:
        articles = news.get_all_news()
        assert len(articles) == 1
        assert articles[0]["symbols"] == ["AAPL"]
        assert articles[0]["title"] == "Apple stock is up 10%"


def test_get_all_news_multi_page(httpx_mock):
    # First page
    fake_news = {
        "news": [{"symbols": ["AAPL"], "title": "Apple stock is up 10%"}],
        "next_page_token": "foo",
    }
    httpx_mock.add_response(json=fake_news)

    # Second page
    fake_news = {"news": [{"symbols": ["TSLA"], "title": "Tesla stock is down 10%"}]}
    httpx_mock.add_response(json=fake_news)

    news.REDIS_CONN = fakeredis.FakeStrictRedis(decode_responses=True)

    with httpx.Client() as client:
        articles = news.get_all_news()
        assert len(articles) == 2
        assert articles[0]["symbols"] == ["AAPL"]
        assert articles[0]["title"] == "Apple stock is up 10%"
        assert articles[1]["symbols"] == ["TSLA"]
        assert articles[1]["title"] == "Tesla stock is down 10%"
