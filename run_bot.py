#!/usr/bin/env python
"""Run the news bot."""

from stocknews import news_realtime
from stocknews.logging_config import logger  # noqa: F401

news_realtime.main()
