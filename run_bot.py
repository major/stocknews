#!/usr/bin/env python
"""Run the news bot."""

import logging

import structlog

from stocknews import news_realtime

# Setup our shared logger.
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

logger = structlog.get_logger()

news_realtime.main()
