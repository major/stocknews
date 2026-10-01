"""Behavioral tests for command startup validation."""

import asyncio
import base64
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from stocknews.__main__ import main


def test_startup_reports_missing_credentials_as_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    output = capsys.readouterr().out
    record = json.loads(output)
    assert record["level"] == "ERROR"
    assert record["msg"] == "invalid configuration"
    assert record["error"] == "ALPACA_API_KEY is required"


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    "stop_during_authentication",
    [False, True],
    ids=["after-subscription", "during-authentication"],
)
def test_sigterm_stops_real_news_and_trade_streams_cleanly(stop_during_authentication: bool) -> None:
    async def scenario() -> None:
        subscribed_streams: set[str] = set()
        authentication_streams: set[str] = set()
        disconnected_streams: set[str] = set()
        both_subscribed = asyncio.Event()
        both_authenticated = asyncio.Event()
        both_disconnected = asyncio.Event()
        connection_tasks: set[asyncio.Task[None]] = set()

        def note_disconnected(stream_name: str) -> None:
            disconnected_streams.add(stream_name)
            if len(disconnected_streams) == 2:
                both_disconnected.set()

        async def send_frame(writer: asyncio.StreamWriter, opcode: int, payload: bytes = b"") -> None:
            first = 0x80 | opcode
            length = len(payload)
            if length < 126:
                header = bytes((first, length))
            elif length < 2**16:
                header = bytes((first, 126)) + length.to_bytes(2, "big")
            else:
                header = bytes((first, 127)) + length.to_bytes(8, "big")
            writer.write(header + payload)
            await writer.drain()

        async def send_json(writer: asyncio.StreamWriter, payload: object) -> None:
            await send_frame(writer, 1, json.dumps(payload).encode())

        async def receive_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
            first, second = await reader.readexactly(2)
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(await reader.readexactly(2), "big")
            elif length == 127:
                length = int.from_bytes(await reader.readexactly(8), "big")
            mask = await reader.readexactly(4) if second & 0x80 else b""
            payload = await reader.readexactly(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            return first & 0x0F, payload

        async def serve_websocket(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            path = ""
            try:
                request_line = await reader.readline()
                parts = request_line.decode().split()
                assert len(parts) >= 2
                path = parts[1]
                headers: dict[str, str] = {}
                while line := await reader.readline():
                    if line == b"\r\n":
                        break
                    name, value = line.decode().split(":", maxsplit=1)
                    headers[name.casefold()] = value.strip()
                key = headers["sec-websocket-key"]
                digest = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
                accept = base64.b64encode(digest).decode()
                response = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                )
                writer.write(response.encode())
                await writer.drain()

                stream_name = "news" if path.endswith("/news") else "stock"
                while True:
                    opcode, payload = await receive_frame(reader)
                    if opcode == 8:
                        await send_frame(writer, 8, payload[:125])
                        note_disconnected(stream_name)
                        return
                    if opcode == 9:
                        await send_frame(writer, 10, payload)
                        continue
                    if opcode != 1:
                        continue
                    request = json.loads(payload)
                    if request["action"] == "auth":
                        authentication_streams.add(stream_name)
                        if len(authentication_streams) == 2:
                            both_authenticated.set()
                        if stop_during_authentication and stream_name == "news":
                            while True:
                                opcode, payload = await receive_frame(reader)
                                if opcode == 8:
                                    await send_frame(writer, 8, payload[:125])
                                    note_disconnected(stream_name)
                                    return
                                if opcode == 9:
                                    await send_frame(writer, 10, payload)
                        await send_json(writer, {"T": "success", "msg": "authenticated"})
                    elif stream_name == "news":
                        await send_json(writer, {"T": "subscription", "news": ["*"]})
                        subscribed_streams.add(stream_name)
                    else:
                        await send_json(writer, {"T": "subscription", "trades": ["SPY", "QQQ"]})
                        subscribed_streams.add(stream_name)
                    if len(subscribed_streams) == 2:
                        both_subscribed.set()
            except asyncio.IncompleteReadError, ConnectionError:
                if path:
                    note_disconnected("news" if path.endswith("/news") else "stock")
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

        def accept_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.create_task(serve_websocket(reader, writer))
            connection_tasks.add(task)
            task.add_done_callback(connection_tasks.discard)

        server = await asyncio.start_server(accept_connection, "127.0.0.1", 0)
        process: asyncio.subprocess.Process | None = None
        try:
            assert server.sockets
            port = server.sockets[0].getsockname()[1]
            repository = Path(__file__).resolve().parents[1]
            environment = os.environ.copy()
            environment.update(
                {
                    "ALPACA_API_KEY": "startup-test-key",
                    "ALPACA_API_SECRET": "startup-test-secret",
                    "ALPACA_NEWS_STREAM_URL": f"ws://127.0.0.1:{port}/v1beta1/news",
                    "ALPACA_STOCK_STREAM_URL": f"ws://127.0.0.1:{port}/v2",
                    "DISCORD_ANALYST_WEBHOOKS": "",
                    "DISCORD_EARNINGS_WEBHOOKS": "",
                    "DISCORD_NEWS_WEBHOOKS": "",
                    "PYTHONPATH": os.pathsep.join((str(repository / "src"), environment.get("PYTHONPATH", ""))),
                }
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "stocknews",
                cwd=repository,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if stop_during_authentication:
                await asyncio.wait_for(both_authenticated.wait(), timeout=5)
            else:
                await asyncio.wait_for(both_subscribed.wait(), timeout=5)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            await asyncio.wait_for(both_disconnected.wait(), timeout=2)
            assert process.returncode == 0, stderr.decode()
            logs = stdout.decode()
            records = [json.loads(line) for line in logs.splitlines()]
            assert any(record["msg"] == "starting stocknews" for record in records)
            assert any(record["msg"] == "shutdown requested" for record in records)
            assert "startup-test-key" not in logs
            assert "startup-test-secret" not in logs
            if stop_during_authentication:
                assert "news" not in subscribed_streams
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            server.close()
            await server.wait_closed()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
def test_cli_reports_initial_alpaca_connection_failure_with_exit_one() -> None:
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
            repository = Path(__file__).resolve().parents[1]
            environment = os.environ.copy()
            environment.update(
                {
                    "ALPACA_API_KEY": "startup-failure-key",
                    "ALPACA_API_SECRET": "startup-failure-secret",
                    "ALPACA_NEWS_STREAM_URL": f"ws://127.0.0.1:{port}/v1beta1/news",
                    "ALPACA_STOCK_STREAM_URL": f"ws://127.0.0.1:{port}/v2",
                    "DISCORD_ANALYST_WEBHOOKS": "",
                    "DISCORD_EARNINGS_WEBHOOKS": "",
                    "DISCORD_NEWS_WEBHOOKS": "",
                    "PYTHONPATH": os.pathsep.join((str(repository / "src"), environment.get("PYTHONPATH", ""))),
                }
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "stocknews",
                cwd=repository,
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
            assert "startup-failure-key" not in logs
            assert "startup-failure-secret" not in logs
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            server.close()
            await server.wait_closed()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)

    asyncio.run(scenario())
