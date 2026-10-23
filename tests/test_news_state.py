"""Tests for durable Alpaca news state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from stocknews.news_state import NewsState

if TYPE_CHECKING:
    from pathlib import Path


def test_claim_returns_true_only_for_the_first_article_id(tmp_path: Path) -> None:
    """Claim each article identifier once per database."""
    state = NewsState(str(tmp_path / "news.sqlite3"))

    assert state.claim(123) is True
    assert state.claim(123) is False
    assert state.claim(456) is True

    state.close()


def test_claim_and_checkpoint_are_durable_after_reopening(tmp_path: Path) -> None:
    """Persist seen article identifiers and poll checkpoints across instances."""
    database_path = tmp_path / "news.sqlite3"
    checkpoint = datetime(2026, 10, 23, 12, 30, tzinfo=UTC)
    state = NewsState(str(database_path))
    state.claim(123)
    state.set_checkpoint(checkpoint)
    state.close()

    reopened_state = NewsState(str(database_path))

    assert reopened_state.claim(123) is False
    assert reopened_state.checkpoint() == checkpoint

    reopened_state.close()


def test_checkpoint_is_none_when_not_set(tmp_path: Path) -> None:
    """Return no checkpoint for a new state database."""
    state = NewsState(str(tmp_path / "news.sqlite3"))

    assert state.checkpoint() is None

    state.close()


def test_constructor_creates_nested_parent_directories(tmp_path: Path) -> None:
    """Create missing parent directories before opening the database."""
    database_path = tmp_path / "nested" / "state" / "news.sqlite3"

    state = NewsState(str(database_path))

    assert database_path.is_file()

    state.close()


def test_independent_instances_atomically_claim_the_same_id(tmp_path: Path) -> None:
    """Allow only one independent connection to claim an article identifier."""
    database_path = str(tmp_path / "news.sqlite3")
    first_state = NewsState(database_path)
    second_state = NewsState(database_path)

    assert first_state.claim(123) is True
    assert second_state.claim(123) is False

    first_state.close()
    second_state.close()


def test_checkpoint_is_normalized_to_utc(tmp_path: Path) -> None:
    """Store checkpoints in UTC regardless of the supplied timezone."""
    state = NewsState(str(tmp_path / "news.sqlite3"))
    checkpoint = datetime(2026, 10, 23, 12, 30, tzinfo=timezone(timedelta(hours=-3)))

    state.set_checkpoint(checkpoint)

    assert state.checkpoint() == datetime(2026, 10, 23, 15, 30, tzinfo=UTC)

    state.close()
