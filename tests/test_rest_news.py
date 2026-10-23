"""Tests for polling news from the Alpaca REST API."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from stocknews.models import Config, NewsItem
from stocknews.news_state import NewsState
from stocknews.rest_news import poll_news

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

_NOW = datetime(2026, 10, 23, 12, 30, tzinfo=UTC)
_FIRST_REQUEST = 1
_SECOND_REQUEST = 2
_THIRD_REQUEST = 3
_EXPECTED_RETRY_REQUEST_COUNT = 4
_WAIT_TEST_ARTICLE_ID = 7


def _config(path: Path, **changes: Any) -> Config:
    """Build a small REST configuration for tests."""
    values: dict[str, Any] = {
        "alpaca_api_key": "test-api-key",
        "alpaca_api_secret": "test-api-secret",
        "alpaca_news_stream_url": "wss://news.test",
        "alpaca_news_rest_url": "https://news.test/v1/news",
        "news_poll_interval_seconds": 17,
        "news_poll_overlap_seconds": 60,
        "news_state_path": str(path),
        "discord_analyst_webhooks": (),
        "discord_earnings_webhooks": (),
        "discord_news_webhooks": (),
        "stock_logo": "https://logo.test/%s.webp",
        "transparent_png": "https://image.test/transparent.png",
    }
    values.update(changes)
    return Config(**values)


def _article(article_id: int, headline: str = "Headline") -> dict[str, Any]:
    """Return a valid Alpaca REST article payload."""
    return {
        "id": article_id,
        "symbols": ["ACME"],
        "author": "Benzinga Newsdesk",
        "headline": headline,
        "summary": "Summary",
        "url": "https://article.test/story",
        "source": "benzinga",
        "content": "Full content",
    }


def _response(news: list[dict[str, Any]], next_page_token: str | None = None) -> httpx2.Response:
    """Create a valid paginated Alpaca response."""
    return httpx2.Response(200, json={"news": news, "next_page_token": next_page_token})


async def _collect_until_stopped(
    config: Config,
    client: httpx2.AsyncClient,
    stop_event: asyncio.Event,
) -> list[NewsItem]:
    """Collect news items until the poller observes its stop event."""
    logger = logging.getLogger("stocknews.test.rest_news")
    return [item async for item in poll_news(config, client, logger, stop_event)]


def test_first_poll_authenticates_and_uses_inclusive_window_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send the first request immediately with required authentication and query values."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    requests: list[httpx2.Request] = []
    stop_event = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Record the initial request and return one article before stopping."""
        requests.append(request)
        stop_event.set()
        return _response([_article(123)])

    async def scenario() -> list[NewsItem]:
        """Collect the initial poll using the mock REST transport."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await _collect_until_stopped(_config(tmp_path / "state.sqlite3"), client, stop_event)

    items = asyncio.run(scenario())

    assert len(requests) == 1
    request = requests[0]
    assert request.url == httpx2.URL(
        "https://news.test/v1/news?start=2026-10-23T12%3A29%3A00Z&end=2026-10-23T12%3A30%3A00Z&sort=asc&limit=50&include_content=true"
    )
    assert request.headers["APCA-API-KEY-ID"] == "test-api-key"
    assert request.headers["APCA-API-SECRET-KEY"] == "test-api-secret"
    assert items == [
        NewsItem(
            article_id=123,
            symbols=("ACME",),
            author="Benzinga Newsdesk",
            headline="Headline",
            summary="Summary",
            url="https://article.test/story",
            source="benzinga",
            content="Full content",
        )
    ]
    state = NewsState(str(tmp_path / "state.sqlite3"))
    assert state.checkpoint() == _NOW
    state.close()


def test_pagination_deduplicates_ids_and_persists_across_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Follow page tokens, deduplicate repeated IDs, and retain claims after restart."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    database = tmp_path / "state.sqlite3"
    requests: list[httpx2.Request] = []
    stop_event = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Serve two pages containing repeated article identifiers."""
        requests.append(request)
        if request.url.params.get("page_token") == "page-2":
            stop_event.set()
            return _response([_article(1, "Changed headline"), _article(2), _article(2, "Duplicate in page")])
        return _response([_article(1)], "page-2")

    async def scenario() -> list[NewsItem]:
        """Collect both pages into the persistent state database."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await _collect_until_stopped(_config(database), client, stop_event)

    first_run = asyncio.run(scenario())
    assert [item.article_id for item in first_run] == [1, 2]
    assert [request.url.params.get("page_token") for request in requests] == [None, "page-2"]

    restart_stop_event = asyncio.Event()

    async def restart_handler(_request: httpx2.Request) -> httpx2.Response:
        """Return a previously claimed article after the poller restarts."""
        restart_stop_event.set()
        return _response([_article(1, "Changed again")])

    async def restart_scenario() -> list[NewsItem]:
        """Collect a restart poll using the same durable state."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(restart_handler)) as client:
            return await _collect_until_stopped(_config(database), client, restart_stop_event)

    assert asyncio.run(restart_scenario()) == []


def test_failed_later_page_retries_from_old_checkpoint_without_duplicate_yields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the old checkpoint through a later-page outage and catch up on retry."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    monkeypatch.setattr("stocknews.rest_news.asyncio.wait_for", _timeout_wait)
    database = tmp_path / "state.sqlite3"
    old_checkpoint = _NOW - timedelta(minutes=10)
    state = NewsState(str(database))
    state.set_checkpoint(old_checkpoint)
    state.close()
    requests: list[httpx2.Request] = []
    attempts = 0
    stop_event = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Fail the later page once, then return the remaining article."""
        nonlocal attempts
        requests.append(request)
        if request.url.params.get("page_token") != "later":
            return _response([_article(10)], "later")
        attempts += 1
        if attempts == 1:
            return httpx2.Response(503)
        stop_event.set()
        return _response([_article(11)])

    async def scenario() -> list[NewsItem]:
        """Collect articles through the later-page failure and retry."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await _collect_until_stopped(_config(database), client, stop_event)

    items = asyncio.run(scenario())

    assert [item.article_id for item in items] == [10, 11]
    assert len(requests) == _EXPECTED_RETRY_REQUEST_COUNT
    assert all(request.url.params["start"] == "2026-10-23T12:19:00Z" for request in requests)
    state = NewsState(str(database))
    assert state.checkpoint() == _NOW
    state.close()


def test_failed_initial_poll_preserves_window_start_across_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the first poll's overlap start after a failed request and process restart."""
    current_time = [_NOW]
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: current_time[0])
    database = tmp_path / "state.sqlite3"
    first_stop_event = asyncio.Event()
    first_requests: list[httpx2.Request] = []

    async def fail_first_request(request: httpx2.Request) -> httpx2.Response:
        """Record the initial window and stop after a service failure."""
        first_requests.append(request)
        first_stop_event.set()
        return httpx2.Response(503)

    async def first_run() -> list[NewsItem]:
        """Run the failed initial poll against a new state database."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail_first_request)) as client:
            return await _collect_until_stopped(_config(database), client, first_stop_event)

    assert asyncio.run(first_run()) == []
    current_time[0] += timedelta(minutes=10)
    restart_stop_event = asyncio.Event()
    restart_requests: list[httpx2.Request] = []

    async def recover_after_restart(request: httpx2.Request) -> httpx2.Response:
        """Record the restart window and complete it without articles."""
        restart_requests.append(request)
        restart_stop_event.set()
        return _response([])

    async def restart() -> list[NewsItem]:
        """Restart polling with the persisted initial baseline."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(recover_after_restart)) as client:
            return await _collect_until_stopped(_config(database), client, restart_stop_event)

    assert asyncio.run(restart()) == []

    expected_start = "2026-10-23T12:29:00Z"
    assert first_requests[0].url.params["start"] == expected_start
    assert restart_requests[0].url.params["start"] == expected_start
    state = NewsState(str(database))
    assert state.checkpoint() == current_time[0]
    state.close()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"news": "not a list", "next_page_token": None},
        {"news": [], "next_page_token": ""},
        {"news": []},
        {"news": [None], "next_page_token": None},
        {"news": [{**_article(5), "id": True}], "next_page_token": None},
        {"news": [{**_article(5), "symbols": ["ACME", 3]}], "next_page_token": None},
        {"news": [{**_article(5), "author": None}], "next_page_token": None},
        {"news": [{**_article(5), "content": None}], "next_page_token": None},
    ],
)
def test_invalid_response_schema_is_retried_without_advancing_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    """Reject malformed response and article fields without committing the poll end."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    monkeypatch.setattr("stocknews.rest_news.asyncio.wait_for", _timeout_wait)
    stop_event = asyncio.Event()
    calls = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Return a malformed page before completing the retry."""
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(200, json=payload)
        stop_event.set()
        return _response([])

    async def scenario() -> list[NewsItem]:
        """Collect through schema rejection and a successful retry."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await _collect_until_stopped(_config(tmp_path / "state.sqlite3"), client, stop_event)

    assert asyncio.run(scenario()) == []
    assert calls == _SECOND_REQUEST


def test_malformed_json_and_repeated_page_token_fail_the_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry malformed JSON and reject pagination loops instead of repeating requests forever."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    monkeypatch.setattr("stocknews.rest_news.asyncio.wait_for", _timeout_wait)
    requests: list[httpx2.Request] = []
    stop_event = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Serve malformed JSON and a pagination loop before recovery."""
        requests.append(request)
        if len(requests) == _FIRST_REQUEST:
            return httpx2.Response(200, content=b"{")
        if len(requests) == _SECOND_REQUEST:
            return _response([], "loop")
        if len(requests) == _THIRD_REQUEST:
            return _response([], "loop")
        stop_event.set()
        return _response([])

    async def scenario() -> list[NewsItem]:
        """Poll through malformed JSON and repeated-token failures."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await _collect_until_stopped(_config(tmp_path / "state.sqlite3"), client, stop_event)

    assert asyncio.run(scenario()) == []
    assert len(requests) == _EXPECTED_RETRY_REQUEST_COUNT
    assert [request.url.params.get("page_token") for request in requests] == [None, None, "loop", None]


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_is_fatal_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    """Raise a clear fatal error on Alpaca authentication failures."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    requests = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Reject authentication with a response body that must stay private."""
        nonlocal requests
        requests += 1
        return httpx2.Response(status, text="sensitive response body")

    async def scenario() -> None:
        """Read until the poller propagates the authentication failure."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            generator = poll_news(_config(tmp_path / "state.sqlite3"), client, logging.getLogger(), asyncio.Event())
            await anext(generator)

    with pytest.raises(RuntimeError, match=f"authentication failed with status {status}"):
        asyncio.run(scenario())
    assert requests == 1


def test_transport_failure_logs_no_credentials_urls_or_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log a safe transport error type and omit secrets, URL, and exception text."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    monkeypatch.setattr("stocknews.rest_news.asyncio.wait_for", _timeout_wait)
    caplog.set_level(logging.WARNING, logger="stocknews.test.rest_news")
    calls = 0
    stop_event = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        """Raise a transport error containing secrets before recovering."""
        nonlocal calls
        calls += 1
        if calls == _FIRST_REQUEST:
            error_message = "test-api-secret https://private.test/path"
            raise httpx2.ConnectError(error_message, request=request)
        stop_event.set()
        return _response([])

    async def scenario() -> list[NewsItem]:
        """Collect through a transport failure while recording safe logs."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await _collect_until_stopped(_config(tmp_path / "state.sqlite3"), client, stop_event)

    assert asyncio.run(scenario()) == []
    assert "ConnectError" in caplog.text
    assert "test-api-secret" not in caplog.text
    assert "private.test" not in caplog.text


def test_cancellation_during_request_propagates_and_keeps_initial_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate request cancellation and retain the initialized baseline checkpoint."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    entered_request = asyncio.Event()
    never_release = asyncio.Event()
    config = _config(tmp_path / "state.sqlite3")

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Hold the REST request until its task is cancelled."""
        entered_request.set()
        await never_release.wait()
        return _response([])

    async def scenario() -> None:
        """Cancel the pending request after the baseline is persisted."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            generator = poll_news(config, client, logging.getLogger(), asyncio.Event())
            request_task = asyncio.create_task(anext(generator))
            await entered_request.wait()
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task

    asyncio.run(scenario())
    state = NewsState(config.news_state_path)
    assert state.checkpoint() == _NOW
    state.close()


def test_cancellation_during_wait_propagates_after_completed_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate cancellation while waiting between polls after committing the completed window."""
    monkeypatch.setattr("stocknews.rest_news._utc_now", lambda: _NOW)
    config = _config(tmp_path / "state.sqlite3")

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        """Return one article before the poller enters its interval wait."""
        return _response([_article(7)])

    async def scenario() -> None:
        """Consume the article and cancel the subsequent interval wait."""
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            generator = poll_news(config, client, logging.getLogger(), asyncio.Event())
            assert (await anext(generator)).article_id == _WAIT_TEST_ARTICLE_ID
            wait_task = asyncio.create_task(anext(generator))
            await asyncio.sleep(0)
            wait_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wait_task

    asyncio.run(scenario())
    state = NewsState(config.news_state_path)
    assert state.checkpoint() == _NOW
    state.close()


def _timeout_wait(awaitable: Awaitable[Any], timeout: float) -> Awaitable[None]:
    """Simulate an elapsed poll interval without waiting in real time."""
    del timeout
    close = getattr(awaitable, "close", None)
    if close is not None:
        close()

    return _raise_timeout()


async def _raise_timeout() -> None:
    """Raise the timeout exception expected from ``asyncio.wait_for``."""
    raise TimeoutError
