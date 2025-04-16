from unittest import mock
from unittest.mock import Mock, patch

import pytest

"""Tests for the real time news feed implementation."""


from stocknews.news_realtime import authenticate, handle_message, stream_news, subscribe


@pytest.fixture
def mock_ws():
    """Create a mock WebSocketSession."""
    mock = Mock()
    mock.receive_json.side_effect = [
        [{"T": "success"}],  # authentication response
        [{"T": "success"}],  # subscription response
        Exception("Test exit"),  # to break the infinite loop
    ]
    return mock


@patch("stocknews.news_realtime.connect_ws")
def test_stream_news(mock_connect_ws, mock_ws):
    """Test the stream_news function."""
    mock_connect_ws.return_value.__enter__.return_value = mock_ws

    with pytest.raises(Exception, match="Test exit"):
        stream_news()

    mock_ws.send_json.assert_any_call({
        "action": "auth",
        "key": mock.ANY,
        "secret": mock.ANY,
    })
    mock_ws.send_json.assert_any_call({"action": "subscribe", "news": ["*"]})


def test_authenticate_success(mock_ws):
    """Test successful authentication."""
    authenticate(mock_ws)

    mock_ws.send_json.assert_called_once()
    mock_ws.receive_json.assert_called_once()


def test_authenticate_failure():
    """Test failed authentication."""
    mock_ws = Mock()
    mock_ws.receive_json.return_value = [{"T": "error", "msg": "Auth failed"}]

    with pytest.raises(Exception, match="Authentication failed"):
        authenticate(mock_ws)


def test_subscribe_success(mock_ws):
    """Test successful subscription."""
    subscribe(mock_ws)

    mock_ws.send_json.assert_called_once()
    mock_ws.receive_json.assert_called_once()


def test_subscribe_failure():
    """Test failed subscription."""
    mock_ws = Mock()
    mock_ws.receive_json.return_value = [{"T": "error", "msg": "Subscription failed"}]

    with pytest.raises(Exception, match="Subscription failed"):
        subscribe(mock_ws)


@patch("stocknews.news_realtime.utils.is_earnings_news")
@patch("stocknews.news_realtime.utils.article_in_cache")
@patch("stocknews.news_realtime.notify.send_earnings_to_discord")
@patch("stocknews.news_realtime.notify.send_earnings_to_mastodon")
def test_handle_message_earnings(
    mock_send_mastodon, mock_send_discord, mock_in_cache, mock_is_earnings
):
    """Test handling an earnings message."""
    mock_is_earnings.return_value = True
    mock_in_cache.return_value = False

    news_item = {"symbols": ["AAPL"], "headline": "Apple Reports Q3 Earnings"}

    handle_message(news_item)

    mock_send_discord.assert_called_once_with(["AAPL"], "Apple Reports Q3 Earnings")
    mock_send_mastodon.assert_called_once_with(["AAPL"], "Apple Reports Q3 Earnings")


@patch("stocknews.news_realtime.utils.is_analyst_rating_change")
@patch("stocknews.news_realtime.utils.article_in_cache")
@patch("stocknews.news_realtime.notify.send_rating_change_to_discord")
def test_handle_message_analyst_rating(
    mock_send_discord, mock_in_cache, mock_is_rating
):
    """Test handling an analyst rating message."""
    mock_is_rating.return_value = True
    mock_in_cache.return_value = False

    news_item = {
        "symbols": ["AAPL"],
        "headline": "JP Morgan Upgrades Apple to Overweight",
    }

    handle_message(news_item)

    mock_send_discord.assert_called_once_with(
        ["AAPL"], "JP Morgan Upgrades Apple to Overweight"
    )


def test_handle_message_multiple_symbols():
    """Test handling a message with multiple symbols."""
    news_item = {"symbols": ["AAPL", "MSFT"], "headline": "Tech Stocks Rally"}

    result = handle_message(news_item)

    assert result is None


@patch("stocknews.news_realtime.utils.is_earnings_news")
@patch("stocknews.news_realtime.utils.article_in_cache")
@patch("stocknews.news_realtime.utils.is_analyst_rating_change")
def test_handle_message_unknown_type(mock_is_rating, mock_in_cache, mock_is_earnings):
    """Test handling an unknown message type."""
    mock_is_earnings.return_value = False
    mock_is_rating.return_value = False

    news_item = {"symbols": ["AAPL"], "headline": "Apple Announces New iPhone"}

    handle_message(news_item)
    # Just verifying it doesn't raise an exception
