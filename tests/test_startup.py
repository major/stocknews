"""Behavioral tests for command startup validation."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport

from stocknews.__main__ import _cancel_and_wait, main, run_application
from stocknews.models import AlpacaSettings, Config

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_CONFIGURATION_ERROR_EXIT_CODE = 2
EXPECTED_STARTUP_LOG_RECORD_COUNT = 2
DUMMY_ALPACA_CREDENTIALS = ("startup-test-key", "startup-test-secret")
DUMMY_ALPACA_API_KEY, DUMMY_ALPACA_API_SECRET = DUMMY_ALPACA_CREDENTIALS
DUMMY_FAILURE_CREDENTIALS = ("startup-failure-key", "startup-failure-secret")
DUMMY_FAILURE_API_KEY, DUMMY_FAILURE_API_SECRET = DUMMY_FAILURE_CREDENTIALS
DUMMY_REPR_CREDENTIALS = ("repr-test-key", "repr-test-secret")
DUMMY_REPR_API_KEY, DUMMY_REPR_API_SECRET = DUMMY_REPR_CREDENTIALS
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STARTUP_TEST_LOGGER_NAMES = (
    "startup-stream-test",
    "startup-terminal-test",
)


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


def test_alpaca_settings_repr_omits_credentials() -> None:
    """Verify Alpaca settings representations do not expose credentials."""
    settings = AlpacaSettings(
        api_key=DUMMY_REPR_API_KEY,
        api_secret=DUMMY_REPR_API_SECRET,
        news_stream_url="ws://news.test/v1beta1/news",
    )

    representation = repr(settings)

    assert DUMMY_REPR_API_KEY not in representation
    assert DUMMY_REPR_API_SECRET not in representation
    assert "ws://news.test/v1beta1/news" in representation


@pytest.mark.parametrize(
    ("git_sha", "expected_commit"),
    [
        ("0123456789abcdef0123456789abcdef01234567", "0123456789abcdef0123456789abcdef01234567"),
        (None, "unknown"),
        ("", "unknown"),
    ],
    ids=["deployed-commit", "unset-commit", "empty-commit"],
)
def test_startup_logs_commit_before_missing_configuration(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    git_sha: str | None,
    expected_commit: str,
) -> None:
    """Verify startup logs the commit before reporting missing configuration."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    if git_sha is None:
        monkeypatch.delenv("GIT_SHA", raising=False)
    else:
        monkeypatch.setenv("GIT_SHA", git_sha)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == EXPECTED_CONFIGURATION_ERROR_EXIT_CODE
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == EXPECTED_STARTUP_LOG_RECORD_COUNT
    assert records[0]["msg"] == "starting stocknews"
    assert records[0]["commit"] == expected_commit
    assert records[1]["level"] == "ERROR"
    assert records[1]["msg"] == "invalid configuration"
    assert records[1]["error"] == "ALPACA_API_KEY is required"


def test_cli_logs_sanitized_failure_without_secret_bearing_exception_chain(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify startup logs omit secrets and tracebacks from raw exception chains."""

    def fail_to_run(coroutine) -> None:
        """Raise a sanitized runner failure after closing its supplied coroutine."""
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


def test_cancel_and_wait_preserves_mixed_base_exception_group() -> None:
    """Verify cancellation waits preserve all children of a mixed exception group."""

    async def scenario() -> None:
        ordinary_error = ValueError("startup task failed")
        mixed_errors: list[BaseExceptionGroup] = []
        task_started = asyncio.Event()

        async def fail_with_mixed_group_when_cancelled() -> None:
            task_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as cancellation_error:
                mixed_error = BaseExceptionGroup(
                    "mixed startup failure",
                    [ordinary_error, cancellation_error],
                )
                mixed_errors.append(mixed_error)
                raise mixed_error from cancellation_error

        task = asyncio.create_task(fail_with_mixed_group_when_cancelled())
        await task_started.wait()

        with pytest.raises(BaseExceptionGroup) as error:
            await _cancel_and_wait(task)

        assert len(mixed_errors) == 1
        assert error.value is mixed_errors[0]
        assert error.value.exceptions[0] is ordinary_error
        assert isinstance(error.value.exceptions[1], asyncio.CancelledError)
        assert task.exception() is mixed_errors[0]

    asyncio.run(scenario())


def test_cancel_and_wait_swallows_finished_task_exception() -> None:
    """Verify _cancel_and_wait drains a finished task without re-raising its exception."""

    async def scenario() -> None:
        async def raise_value_error() -> None:
            raise ValueError("expected")

        task = asyncio.create_task(raise_value_error())
        # Yield to the loop until the task is observed as done, without re-raising its exception.
        for _ in range(50):
            if task.done():
                break
            await asyncio.sleep(0.01)
        # No exception escapes even though the task ended with a non-cancellation error.
        await _cancel_and_wait(task)

    asyncio.run(scenario())


def test_run_application_shuts_down_on_termination_immediately_after_subscription() -> None:
    """Verify immediate stream termination shuts down without exposing credentials."""

    async def scenario() -> None:
        disconnected = asyncio.Event()

        async def alpaca_app(scope, receive, send) -> None:
            assert scope["path"] == "/v1beta1/news"
            await receive()
            await send({"type": "websocket.accept"})

            async def send_json(payload: object) -> None:
                await send({"type": "websocket.send", "text": json.dumps(payload)})

            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    disconnected.set()
                    return

                request = json.loads(message["text"])
                if request["action"] == "auth":
                    await send_json({"T": "success", "msg": "authenticated"})
                else:
                    await send_json(
                        [
                            {"T": "subscription", "news": ["*"]},
                            {"T": "error", "code": 403, "msg": f"rejected {DUMMY_ALPACA_API_SECRET}"},
                        ]
                    )

        config = Config(
            alpaca_api_key=DUMMY_ALPACA_API_KEY,
            alpaca_api_secret=DUMMY_ALPACA_API_SECRET,
            alpaca_news_stream_url="ws://news.test/v1beta1/news",
            discord_analyst_webhooks=(),
            discord_earnings_webhooks=(),
            discord_news_webhooks=(),
            stock_logo="https://example.test/%s.webp",
            transparent_png="https://example.test/transparent.png",
            blocked_phrases=(),
        )
        stop_event = asyncio.Event()
        logger = logging.getLogger("startup-terminal-test")

        async with (
            ASGIWebSocketTransport(alpaca_app) as websocket_transport,
            httpx2.AsyncClient(mounts={"ws://news.test": websocket_transport}, timeout=10) as client,
        ):
            with pytest.raises(RuntimeError, match="alpaca news stream terminated") as error:
                await run_application(config, client, logger, stop_event)

        assert DUMMY_ALPACA_API_SECRET not in str(error.value)
        assert stop_event.is_set()
        assert disconnected.is_set()

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_cli_logs_shutdown_requested_when_sigterm_idle() -> None:
    """Verify SIGTERM reaches the shutdown handler while the CLI is connected and idle."""

    async def scenario() -> None:
        accepted = asyncio.Event()
        connection_tasks: set[asyncio.Task[None]] = set()
        server = await _start_idle_websocket_server(accepted, connection_tasks)
        process: asyncio.subprocess.Process | None = None
        try:
            port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
            environment = _cli_environment(port)
            process = await _launch_cli(environment)
            await asyncio.wait_for(accepted.wait(), timeout=5)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            assert process.returncode == 0, stderr.decode()
            _assert_shutdown_records(stdout)
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            server.close()
            await server.wait_closed()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)

    asyncio.run(scenario())


async def _accept_idle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    accepted: asyncio.Event,
    connection_tasks: set[asyncio.Task[None]],
) -> None:
    """Accept one loopback connection and complete the WebSocket upgrade, then idle."""
    key = await _read_sec_websocket_key(reader)
    digest = hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(),
        usedforsecurity=False,
    ).digest()
    accept_value = base64.b64encode(digest).decode()
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_value}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    accepted.set()
    task = asyncio.current_task()
    assert task is not None
    connection_tasks.add(task)
    task.add_done_callback(connection_tasks.discard)
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
    except asyncio.IncompleteReadError, ConnectionError:
        pass
    finally:
        writer.close()
        with suppress(ConnectionError):
            await writer.wait_closed()


async def _read_sec_websocket_key(reader: asyncio.StreamReader) -> str:
    """Extract ``Sec-WebSocket-Key`` from a pending HTTP upgrade request."""
    request = await reader.readuntil(b"\r\n\r\n")
    lines = request.decode("latin-1").split("\r\n")
    for line in lines[1:]:
        if not line:
            break
        name, value = line.split(":", 1)
        if name.casefold() == "sec-websocket-key":
            return value.strip()
    error_message = "missing Sec-WebSocket-Key header"
    raise ValueError(error_message)


async def _start_idle_websocket_server(
    accepted: asyncio.Event,
    connection_tasks: set[asyncio.Task[None]],
) -> asyncio.AbstractServer:
    """Start a loopback server that completes the WS upgrade and holds the socket open."""
    return await asyncio.start_server(
        lambda reader, writer: _accept_idle_connection(
            reader,
            writer,
            accepted=accepted,
            connection_tasks=connection_tasks,
        ),
        "127.0.0.1",
        0,
    )


def _cli_environment(port: int) -> dict[str, str]:
    """Build environment variables that point the CLI at the loopback WebSocket."""
    environment = os.environ.copy()
    environment.update(
        {
            "ALPACA_API_KEY": DUMMY_ALPACA_API_KEY,
            "ALPACA_API_SECRET": DUMMY_ALPACA_API_SECRET,
            "ALPACA_NEWS_STREAM_URL": f"ws://127.0.0.1:{port}/v1beta1/news",
            "DISCORD_ANALYST_WEBHOOKS": "",
            "DISCORD_EARNINGS_WEBHOOKS": "",
            "DISCORD_NEWS_WEBHOOKS": "",
            "PYTHONPATH": os.pathsep.join((str(REPOSITORY_ROOT / "src"), environment.get("PYTHONPATH", ""))),
        }
    )
    return environment


async def _launch_cli(environment: dict[str, str]) -> asyncio.subprocess.Process:
    """Launch the stocknews CLI as a subprocess with the given environment."""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "stocknews",
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def _assert_shutdown_records(stdout: bytes) -> None:
    """Verify the CLI logged startup, shutdown, and no leaked credentials."""
    logs = stdout.decode()
    records = [json.loads(line) for line in logs.splitlines()]
    assert any(record["msg"] == "starting stocknews" for record in records)
    assert any(record["msg"] == "shutdown requested" for record in records)
    assert DUMMY_ALPACA_API_KEY not in logs
    assert DUMMY_ALPACA_API_SECRET not in logs


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_cli_reports_initial_alpaca_connection_failure_with_exit_one() -> None:
    """Verify an initial Alpaca connection failure exits with status one."""

    async def scenario() -> None:
        connection_tasks: set[asyncio.Task[None]] = set()

        async def reject_upgrade(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        def accept_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.create_task(reject_upgrade(reader, writer))
            connection_tasks.add(task)
            task.add_done_callback(connection_tasks.discard)

        server = await asyncio.start_server(accept_connection, "127.0.0.1", 0)
        process: asyncio.subprocess.Process | None = None
        try:
            assert server.sockets
            port = server.sockets[0].getsockname()[1]
            environment = os.environ.copy()
            environment.update(
                {
                    "ALPACA_API_KEY": DUMMY_FAILURE_API_KEY,
                    "ALPACA_API_SECRET": DUMMY_FAILURE_API_SECRET,
                    "ALPACA_NEWS_STREAM_URL": f"ws://127.0.0.1:{port}/v1beta1/news",
                    "DISCORD_ANALYST_WEBHOOKS": "",
                    "DISCORD_EARNINGS_WEBHOOKS": "",
                    "DISCORD_NEWS_WEBHOOKS": "",
                    "PYTHONPATH": os.pathsep.join((str(REPOSITORY_ROOT / "src"), environment.get("PYTHONPATH", ""))),
                }
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "stocknews",
                cwd=REPOSITORY_ROOT,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            assert process.returncode == 1
            logs = stdout.decode()
            records = [json.loads(line) for line in logs.splitlines()]
            failure = next(record for record in records if record["msg"] == "stocknews failed")
            assert "connect Alpaca news stream" in failure["error"]
            assert DUMMY_FAILURE_API_KEY not in logs
            assert DUMMY_FAILURE_API_SECRET not in logs
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            server.close()
            await server.wait_closed()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)

    asyncio.run(scenario())
