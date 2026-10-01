"""Configure JSON logs for the stocknews command."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_WEBHOOK_URL = re.compile(r"(?i)https?://[^\s\"'<>]*/webhooks?/[^\s\"'<>]*")


def _redact_webhook_urls(value: str) -> str:
    return _WEBHOOK_URL.sub("[redacted webhook URL]", value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_webhook_urls(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_webhook_urls(str(value))


class JSONFormatter(logging.Formatter):
    """Format one log record as a JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        fields: dict[str, Any] = {
            "time": timestamp,
            "level": record.levelname,
            "msg": _redact_webhook_urls(record.getMessage()),
        }
        fields.update(
            {
                key: _redact_value(value)
                for key, value in record.__dict__.items()
                if key not in _STANDARD_FIELDS and not key.startswith("_")
            }
        )
        if record.exc_info:
            fields["exception"] = _redact_webhook_urls(self.formatException(record.exc_info))
        return json.dumps(fields, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging(stream: TextIO | None = None) -> logging.Logger:
    """Send structured info-level logs to standard output."""
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    return logging.getLogger("stocknews")
