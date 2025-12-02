"""Centralized logging configuration using loguru."""

import os
import sys

from loguru import logger

# Remove default handler and configure with env var support
logger.remove()
logger.add(
    sink=sys.stderr,
    level=os.environ.get("LOG_LEVEL", "INFO"),
)

__all__ = ["logger"]
