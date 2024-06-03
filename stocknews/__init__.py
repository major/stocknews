"""Top-level package for stocknews."""

import logging
import sys

logging.basicConfig(
    stream=sys.stdout,
    level=logging.WARNING,
    format="%(asctime)s;%(levelname)s;%(message)s",
)
