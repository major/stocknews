"""Durable storage for seen news articles and poll checkpoints."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class NewsState:
    """Store news processing state in a SQLite database."""

    def __init__(self, path: str) -> None:
        """Open the database and initialize its tables."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("CREATE TABLE IF NOT EXISTS seen_articles (article_id INTEGER PRIMARY KEY)")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                checkpoint TEXT
            )
            """
        )
        self._connection.execute("INSERT OR IGNORE INTO poll_state (id, checkpoint) VALUES (1, NULL)")
        self._connection.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._connection.close()

    def claim(self, article_id: int) -> bool:
        """Record an article identifier, returning whether it was new."""
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO seen_articles (article_id) VALUES (?)",
            (article_id,),
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def checkpoint(self) -> datetime | None:
        """Return the stored poll checkpoint as an aware UTC datetime."""
        row = self._connection.execute("SELECT checkpoint FROM poll_state WHERE id = 1").fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0]).astimezone(UTC)

    def set_checkpoint(self, value: datetime) -> None:
        """Store a timezone-aware checkpoint normalized to UTC."""
        if value.utcoffset() is None:
            msg = "checkpoint must be timezone-aware"
            raise ValueError(msg)

        checkpoint = value.astimezone(UTC).isoformat()
        self._connection.execute(
            "UPDATE poll_state SET checkpoint = ? WHERE id = 1",
            (checkpoint,),
        )
        self._connection.commit()
