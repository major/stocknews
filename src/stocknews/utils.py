"""Utilities for quick actions on things."""

import re
from typing import Optional

from stocknews.config import settings
from stocknews.logging_config import logger


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
