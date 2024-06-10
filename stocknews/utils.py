"""Utilities for quick actions on things."""

import logging
import re
from hashlib import sha256
from typing import Optional

from redis import Redis

from stocknews.config import BLOCKED_TICKERS, REDIS_HOST, REDIS_PORT

log = logging.getLogger(__name__)

REDIS_CONN = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def article_in_cache(symbols: list, headline: str) -> bool:
    """Check if the article is already in the cache."""
    article_string = f"{','.join(symbols)}: {headline}"
    article_key = sha256(article_string.encode()).hexdigest()

    if REDIS_CONN.exists(article_key):
        logging.info(f"Article '{article_string}' already in cache.")
        return True

    else:
        logging.info(f"Article '{article_string}' not in cache -- adding it.")
        # Expire the cache record after a while to avoid consuming too much memory.
        REDIS_CONN.set(article_key, article_string, ex=3600)

    return False


def get_cache_expiration(symbols: list, headline: str) -> Optional[int]:
    """Get the expiration time for the cache."""
    if is_earnings_news(symbols, headline):
        return None

    return 3600


def is_earnings_news(symbols: list, headline: str) -> bool:
    """Check if a headline is earnings related."""
    # Earnings headlines have only one symbol.
    if len(symbols) != 1:
        return False

    # https://regex101.com/r/v0kOmQ/1
    regex = r"(EPS|Sales) (~*\$[\d\.\(\)\-\$\~]+[K|M|B]*) [\w\s]+ (\$[\d\.\(\)\-\$\~]+[K|M|B]*)"
    matches = re.search(regex, headline)
    return matches is not None


def is_blocked_ticker(symbols: list) -> bool:
    """Check to see if a blocked/excluded ticker is included in the news article."""
    if len(set(BLOCKED_TICKERS).intersection(symbols)) > 0:
        return True

    return False
