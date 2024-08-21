"""Utilities for quick actions on things."""

import logging
import re
from hashlib import sha256
from typing import Optional

from redis import Redis

from stocknews.config import BLOCKED_PHRASES, REDIS_HOST, REDIS_PORT

logger = logging.getLogger(__name__)

REDIS_CONN = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def article_in_cache(symbols: list, headline: str) -> bool:
    """Check if the article is already in the cache."""
    article_string = f"{','.join(symbols)}: {headline}"
    article_key = sha256(article_string.encode()).hexdigest()

    if REDIS_CONN.exists(article_key):
        logger.debug(f"Article '{article_string}' already in cache.")
        return True

    else:
        logger.debug(f"Article '{article_string}' not in cache -- adding it.")
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


def extract_earnings_data(headline: str) -> dict:
    """Extract earnings data from a headline."""
    # Regex101: https://regex101.com/r/gzWfqo/1
    regex = r"(EPS|Sales) ([\d\.()$KMBT]+) (\w*) ([\d\.()$KMBT]+) Est(?:imate|\.)"
    matches = re.findall(regex, headline, re.IGNORECASE)

    if not matches:
        logger.info(f"No earnings data found in headline: {headline}")
        return {}

    earnings_data = {}

    for match in matches:
        result_type = "EPS" if match[0].lower() == "eps" else "Sales"
        earnings_data[result_type] = {
            "actual": match[1],
            "estimate": match[3],
            "beat": parse_earnings_result(match[2]),
        }

    return earnings_data


def parse_earnings_result(raw_result: str) -> bool:
    """Parse the earnings result from a headline."""
    if "beat" in raw_result.lower():
        return True

    return False


def get_company_name(headline: str) -> str:
    """Extract the company name from a headline."""
    regex = r"^(.*?) (?=Q[1-4])"
    matches = re.findall(regex, headline)
    return str(matches[0]) if matches else ""


def boolean_to_emoji(value: bool) -> str:
    """Convert a boolean value to an emoji."""
    return "💚" if value else "💔"


def get_earnings_notification_description(headline: str) -> str:
    """Get the earnings notification description."""
    result = extract_earnings_data(headline)
    description = []

    for key, value in result.items():
        emoji = boolean_to_emoji(value["beat"])
        description.append(
            f"{emoji} {key}: {value['actual']} vs. {value['estimate']} est."
        )

    return "\n".join(description)


def has_blocked_phrases(headline: str) -> bool:
    """Check if the headline contains blocked phrases."""
    return any(x in headline.lower() for x in BLOCKED_PHRASES)
