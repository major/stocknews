"""Utilities for quick actions on things."""

import json
import logging
import re
from hashlib import sha256
from pathlib import Path
from typing import Optional

import pandas as pd
import structlog
from redis import Redis

from stocknews.config import settings

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

logger = structlog.get_logger()

REDIS_CONN = Redis(
    host=settings.redis_host, port=settings.redis_port, decode_responses=True
)


def check_redis() -> None:
    """Check if Redis is reachable."""
    try:
        REDIS_CONN.ping()
        logger.info("Redis is reachable")
    except Exception as e:
        logger.exception("Redis connection error")
        raise Exception(str(e))


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
        REDIS_CONN.set(article_key, article_string)

    return False


def dump_article_cache() -> str:
    """Dump the cache."""
    keys = list(REDIS_CONN.keys())  # type: ignore
    articles = set([REDIS_CONN.get(x) for x in keys])
    return "\n".join(sorted(articles))  # type: ignore


def get_cache_expiration(symbols: list, headline: str) -> Optional[int]:
    """Get the expiration time for the cache."""
    if is_earnings_news(symbols, headline):
        return None

    return 3600


def is_earnings_news(symbols: list, headline: str) -> bool:
    """Check if a headline is earnings related."""
    # Earnings headlines have only one symbol.
    if len(symbols) != 1:
        logger.info(f"Too many symbols: '{headline}'")
        return False

    # Ignore lines that are missing an estimate.
    if any(x in headline.lower() for x in ["up from", "down from"]):
        logger.info(f"Missing estimate in headline: '{headline}'")
        return False

    # https://regex101.com/r/v0kOmQ/
    regex = r"(EPS|Sales) (~*\$[\d\.\(\)\-\$\~]+[K|M|B]*) [\w\s]+ (\$[\d\.\(\)\-\$\~]+[K|M|B]*)"
    matches = re.search(regex, headline)
    return matches is not None


def extract_earnings_data(headline: str) -> dict:
    """Extract earnings data from a headline."""
    # Regex101: https://regex101.com/r/gzWfqo/
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
    return "beat" in raw_result.lower()


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
    return any(x in headline.lower() for x in settings.blocked_phrases)


def is_analyst_rating_change(headline: str) -> bool:
    """Check if the headline is an analyst rating change."""
    return "price target" in headline.lower()


def get_stocks_from_wikipedia(url: str = "") -> list[str]:
    """Get the stocks from a Wikipedia page."""
    tables = pd.read_html(url)
    return list(tables[0]["Symbol"].astype(str).tolist())


def update_sp1500_stocks() -> None:
    """Get the stocks from the S&P 500 Wikipedia page."""
    stocks = []
    for url in [
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
    ]:
        stocks += get_stocks_from_wikipedia(url)

    # Remove warrants.
    stocks = [x for x in stocks if "." not in x]

    stocks = sorted(set(stocks))

    Path(settings.sp1500_stocks_file).write_text(json.dumps(stocks, indent=2))
