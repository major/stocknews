#!/usr/bin/env python
"""Run the news bot."""

import logging

from stocknews import news_realtime

# Setup our shared logger.
log = logging.getLogger(__name__)

news_realtime.main()
