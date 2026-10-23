"""Behavioral tests for command startup validation."""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import pytest

from stocknews.__main__ import main

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_CONFIGURATION_ERROR_EXIT_CODE = 2
EXPECTED_STARTUP_LOG_RECORD_COUNT = 2
DUMMY_FAILURE_CREDENTIALS = ("startup-failure-key", "startup-failure-secret")
_DUMMY_FAILURE_API_KEY, DUMMY_FAILURE_API_SECRET = DUMMY_FAILURE_CREDENTIALS
STARTUP_TEST_LOGGER_NAMES = ("startup-terminal-test",)
EXPECTED_STOCKNEWS_BANNER = r"""     _             _
 ___| |_ ___   ___| | ___ __   _____      _____
/ __| __/ _ \ / __| |/ / '_ \ / _ \ \ /\ / / __|
\__ \ || (_) | (__|   <| | | |  __/\ V  V /\__ \
|___/\__\___/ \___|_|\_\_| |_|\___| \_/\_/ |___/"""


@pytest.fixture(autouse=True)
def isolate_startup_test_loggers() -> Iterator[None]:
    """Isolate dedicated startup loggers and restore their shared state."""
    loggers = [logging.getLogger(name) for name in STARTUP_TEST_LOGGER_NAMES]
    states = [
        (logger.handlers[:], logger.filters[:], logger.level, logger.propagate, logger.disabled) for logger in loggers
    ]
    for logger in loggers:
        logger.handlers.clear()
        logger.filters.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = False
        logger.disabled = False

    yield

    for logger, (handlers, filters, level, propagate, disabled) in zip(loggers, states, strict=True):
        logger.handlers[:] = handlers
        logger.filters[:] = filters
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


@pytest.mark.parametrize(
    ("git_sha", "expected_commit"),
    [
        ("0123456789abcdef0123456789abcdef01234567", "0123456"),
        ("abc1234", "abc1234"),
        (None, "unknown"),
        ("", "unknown"),
    ],
    ids=["deployed-commit", "short-commit", "unset-commit", "empty-commit"],
)
@pytest.mark.parametrize(
    "build_date",
    ["2026-10-22T16:09:06Z", None, ""],
    ids=["provided-build-date", "unset-build-date", "empty-build-date"],
)
def test_startup_logs_commit_before_missing_configuration(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    git_sha: str | None,
    expected_commit: str,
    build_date: str | None,
) -> None:
    """Verify startup logs the commit before reporting missing configuration."""
    run_asyncio = asyncio.run
    stderr_before_startup: list[str] = []

    def run_after_banner(coroutine: Any) -> None:
        """Record standard error before running startup and its logs."""
        stderr_before_startup.append(capsys.readouterr().err)
        run_asyncio(coroutine)

    monkeypatch.setattr("stocknews.__main__.asyncio.run", run_after_banner)
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    if git_sha is None:
        monkeypatch.delenv("GIT_SHA", raising=False)
    else:
        monkeypatch.setenv("GIT_SHA", git_sha)
    if build_date is None:
        monkeypatch.delenv("BUILD_DATE", raising=False)
    else:
        monkeypatch.setenv("BUILD_DATE", build_date)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == EXPECTED_CONFIGURATION_ERROR_EXIT_CODE
    captured = capsys.readouterr()
    assert stderr_before_startup == [EXPECTED_STOCKNEWS_BANNER + "\n\n"]
    assert captured.err == ""
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert len(records) == EXPECTED_STARTUP_LOG_RECORD_COUNT
    assert records[0]["msg"] == "starting stocknews"
    assert records[0]["commit"] == expected_commit
    assert records[0]["build_date"] == (build_date or "unknown")
    assert records[0]["version"] == "dev"
    assert records[1]["level"] == "ERROR"
    assert records[1]["msg"] == "invalid configuration"
    assert records[1]["error"] == "ALPACA_API_KEY is required"


def test_cli_logs_sanitized_failure_without_secret_bearing_exception_chain(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify startup logs omit secrets and tracebacks from raw exception chains."""
    stderr_before_failure: list[str] = []

    def fail_to_run(coroutine) -> None:
        """Raise a sanitized runner failure after closing its supplied coroutine."""
        stderr_before_failure.append(capsys.readouterr().err)
        coroutine.close()
        cause = RuntimeError(DUMMY_FAILURE_API_SECRET)
        error_message = "sanitized startup failure"
        raise RuntimeError(error_message) from cause

    monkeypatch.setattr("stocknews.__main__.asyncio.run", fail_to_run)

    with caplog.at_level(logging.ERROR, logger="stocknews"), pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 1
    failure_record = next(record for record in caplog.records if record.name == "stocknews")
    assert failure_record.getMessage() == "stocknews failed"
    assert failure_record.error == "sanitized startup failure"
    assert DUMMY_FAILURE_API_SECRET not in caplog.text
    assert "Traceback" not in caplog.text
    assert stderr_before_failure == [EXPECTED_STOCKNEWS_BANNER + "\n\n"]
