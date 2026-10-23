"""Verify CLI signal handling with the real REST news poller."""

import asyncio
import contextlib
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path
from typing import Literal

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DUMMY_CREDENTIALS = ("rest-signal-test-key", "rest-signal-test-secret")
_DUMMY_API_KEY, _DUMMY_API_SECRET = _DUMMY_CREDENTIALS
_HTTP_OK_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
_EMPTY_NEWS_PAYLOAD = json.dumps({"news": [], "next_page_token": None}).encode()
_TerminationStage = Literal["pending-request", "after-response"]


class _LocalRestServer:
    """Serve one Alpaca REST request and signal when its response is consumed."""

    def __init__(self, termination_stage: _TerminationStage) -> None:
        """Prepare server events and connection tracking for one test scenario."""
        self.termination_stage = termination_stage
        self.request_received = asyncio.Event()
        self.response_consumed = asyncio.Event()
        self.release_pending_response = asyncio.Event()
        self.request_paths: list[str] = []
        self.connection_tasks: set[asyncio.Task[None]] = set()
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start a loopback-only HTTP server on an available port."""
        self.server = await asyncio.start_server(self._accept_request, "127.0.0.1", 0)

    @property
    def port(self) -> int:
        """Return the port assigned to the loopback server."""
        assert self.server is not None
        assert self.server.sockets
        return int(self.server.sockets[0].getsockname()[1])

    def _accept_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Track each accepted connection until its request handler finishes."""
        task = asyncio.create_task(self._serve_request(reader, writer))
        self.connection_tasks.add(task)
        task.add_done_callback(self.connection_tasks.discard)

    async def _serve_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Read one request and optionally hold its REST response pending."""
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            request_target = request_line.decode().split()[1]
            self.request_paths.append(request_target.split("?", maxsplit=1)[0])
            while line := await reader.readline():
                if line == b"\r\n":
                    break

            self.request_received.set()
            if self.termination_stage == "pending-request":
                await self.release_pending_response.wait()

            await self._send_response(writer)
            if self.termination_stage == "after-response":
                await reader.read()
                self.response_consumed.set()
        except ConnectionError, asyncio.IncompleteReadError:
            return
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    @staticmethod
    async def _send_response(writer: asyncio.StreamWriter) -> None:
        """Send an empty successful Alpaca news response and close the socket."""
        body = _EMPTY_NEWS_PAYLOAD
        writer.write(_HTTP_OK_RESPONSE + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def close(self) -> None:
        """Close the listener and settle all accepted connection handlers."""
        self.release_pending_response.set()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.connection_tasks):
            if not task.done():
                task.cancel()
        if self.connection_tasks:
            await asyncio.gather(*self.connection_tasks, return_exceptions=True)


def _subprocess_environment(port: int, state_path: Path) -> dict[str, str]:
    """Build isolated CLI settings that only target the local REST server."""
    environment = os.environ.copy()
    environment.update(
        {
            "ALPACA_API_KEY": _DUMMY_API_KEY,
            "ALPACA_API_SECRET": _DUMMY_API_SECRET,
            "ALPACA_NEWS_REST_URL": f"http://127.0.0.1:{port}/v1beta1/news",
            "NEWS_POLL_INTERVAL_SECONDS": "3600",
            "NEWS_POLL_OVERLAP_SECONDS": "3600",
            "NEWS_STATE_PATH": str(state_path),
            "DISCORD_ANALYST_WEBHOOKS": "",
            "DISCORD_EARNINGS_WEBHOOKS": "",
            "DISCORD_NEWS_WEBHOOKS": "",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "PYTHONPATH": os.pathsep.join((str(REPOSITORY_ROOT / "src"), environment.get("PYTHONPATH", ""))),
        }
    )
    return environment


async def _start_stocknews_process(port: int, state_path: Path) -> asyncio.subprocess.Process:
    """Launch the actual ``python -m stocknews`` CLI with local test settings."""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "stocknews",
        cwd=REPOSITORY_ROOT,
        env=_subprocess_environment(port, state_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def _assert_clean_shutdown(process: asyncio.subprocess.Process, stdout: bytes, stderr: bytes) -> None:
    """Check successful SIGTERM handling and sanitized CLI output."""
    assert process.returncode == 0, stderr.decode()
    output = stdout.decode()
    combined_output = output + stderr.decode()
    records = [json.loads(line) for line in output.splitlines()]
    assert any(record["msg"] == "starting stocknews" for record in records)
    assert any(record["msg"] == "shutdown requested" for record in records)
    assert _DUMMY_API_KEY not in combined_output
    assert _DUMMY_API_SECRET not in combined_output
    assert "Traceback" not in combined_output


async def _run_sigterm_scenario(tmp_path: Path, termination_stage: _TerminationStage) -> None:
    """Start the real CLI against local REST and request SIGTERM at a chosen stage."""
    server = _LocalRestServer(termination_stage)
    await server.start()
    state_path = tmp_path / "news.sqlite3"
    process: asyncio.subprocess.Process | None = None
    try:
        process = await _start_stocknews_process(server.port, state_path)
        async with asyncio.timeout(5):
            await server.request_received.wait()
            if termination_stage == "after-response":
                await server.response_consumed.wait()

        process.send_signal(signal.SIGTERM)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)

        _assert_clean_shutdown(process, stdout, stderr)
        assert server.request_paths == ["/v1beta1/news"]
        assert state_path.is_file()
        with contextlib.closing(sqlite3.connect(state_path)) as database:
            assert database.execute("SELECT 1 FROM poll_state WHERE id = 1").fetchone() == (1,)
    finally:
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await asyncio.wait_for(process.communicate(), timeout=5)
        await server.close()


@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize(
    "termination_stage",
    ["pending-request", "after-response"],
    ids=["during-rest-request", "after-rest-response"],
)
def test_sigterm_stops_real_rest_cli_cleanly(tmp_path: Path, termination_stage: _TerminationStage) -> None:
    """Verify SIGTERM cleanly stops the CLI during and after a REST request."""
    asyncio.run(_run_sigterm_scenario(tmp_path, termination_stage))
