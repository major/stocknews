"""Tests for JSON log formatting."""

import json
import logging
from io import StringIO

import pytest

from stocknews.logging import JSONFormatter


@pytest.mark.parametrize("value", [None, True, 3, 2.5], ids=["none", "bool", "int", "float"])
def test_json_logs_keep_scalar_extra_fields_as_json_scalars(value: object) -> None:
    """Keep None, booleans, and numbers as JSON scalars instead of strings.

    Args:
        value: Scalar extra field value to log.
    """
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JSONFormatter())
    logger = logging.getLogger("stocknews.test.scalars")
    logger.addHandler(handler)
    try:
        logger.warning("scalar fields", extra={"value": value})
    finally:
        logger.removeHandler(handler)

    record = json.loads(output.getvalue())
    assert record["value"] == value
    assert type(record["value"]) is type(value)
