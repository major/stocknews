"""Behavioral tests for command startup validation."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import signal
import sys
from io import StringIO
from pathlib import Path

import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport

from stocknews.__main__ import main, run_application
from stocknews.alpaca import AlpacaStreamError, StreamHandle
from stocknews.models import Config, Trade


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

    assert error.value.code == 2
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(records) == 2
    assert records[0]["msg"] == "starting stocknews"
    assert records[0]["commit"] == expected_commit
    assert records[1]["level"] == "ERROR"
    assert records[1]["msg"] == "invalid configuration"
    assert records[1]["error"] == "ALPACA_API_KEY is required"


@pytest.mark.parametrize(
    ("stock_completion", "expect_trade"),
    [
        ("completed-with-queued-trade", True),
        ("pending-empty", False),
        ("pending-with-queued-trade", True),
    ],
    ids=["precompleted-buffer", "normal-eof-while-reading", "queued-event-races-eof"],
)
def test_run_application_drains_queued_news_when_stock_stream_reaches_eof(
    stock_completion: str,
    expect_trade: bool,
) -> None:
    """Verify stock EOF drains queued news and any final trade."""

    async def scenario() -> None:
        delivery_started = asyncio.Event()
        release_delivery = asyncio.Event()
        news_disconnected = asyncio.Event()
        finish_stock = asyncio.Event()
        trade_logged = asyncio.Event()
        delivered: list[dict[str, object]] = []
        stock_queues: list[asyncio.Queue[Trade]] = []
        stock_tasks: list[asyncio.Task[None]] = []

        class ObservedTradeQueue(asyncio.Queue[Trade]):
            def __init__(self) -> None:
                super().__init__()
                self.consumer_waiting = asyncio.Event()

            async def get(self) -> Trade:
                self.consumer_waiting.set()
                return await super().get()

        async def alpaca_app(scope, receive, send) -> None:
            assert scope["path"] == "/v1beta1/news"
            await receive()
            await send({"type": "websocket.accept"})

            async def send_json(payload: object) -> None:
                await send({"type": "websocket.send", "text": json.dumps(payload)})

            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    news_disconnected.set()
                    return

                request = json.loads(message["text"])
                if request["action"] == "auth":
                    await send_json({"T": "success", "msg": "authenticated"})
                else:
                    await send_json({"T": "subscription", "news": ["*"]})
                    await send_json(
                        {
                            "T": "n",
                            "symbols": ["AAPL"],
                            "author": "Benzinga Newsdesk",
                            "headline": "Apple launches a phone",
                        }
                    )

        async def discord_handler(request: httpx2.Request) -> httpx2.Response:
            delivery_started.set()
            await release_delivery.wait()
            delivered.append(json.loads(request.content))
            return httpx2.Response(204)

        async def stock_starter(
            _client,
            *,
            base_url,
            api_key,
            api_secret,
            stop_event,
            on_terminated,
        ) -> StreamHandle[Trade]:
            events = ObservedTradeQueue()
            stock_queues.append(events)

            if stock_completion == "completed-with-queued-trade":
                await delivery_started.wait()
                events.put_nowait(Trade(symbol="SPY", price=500.25, size=100))

                async def completed() -> None:
                    return

                task = asyncio.create_task(completed())
                await task
            else:

                async def wait_for_eof() -> None:
                    await finish_stock.wait()

                task = asyncio.create_task(wait_for_eof())

            stock_tasks.append(task)
            return StreamHandle(events=events, task=task)

        config = Config(
            alpaca_api_key="startup-test-key",
            alpaca_api_secret="startup-test-secret",
            alpaca_news_stream_url="ws://news.test/v1beta1/news",
            alpaca_stock_stream_url="wss://stocks.test/v2",
            discord_analyst_webhooks=(),
            discord_earnings_webhooks=(),
            discord_news_webhooks=("https://discord.test/webhook/private-token",),
            stock_logo="https://example.test/%s.webp",
            transparent_png="https://example.test/transparent.png",
            blocked_phrases=(),
        )
        output = StringIO()
        logger = logging.Logger("startup-stream-test", level=logging.INFO)
        logger.addHandler(logging.StreamHandler(output))

        class TradeLogObserver(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.getMessage() == "stock trade":
                    trade_logged.set()

        logger.addHandler(TradeLogObserver())
        stop_event = asyncio.Event()

        async with ASGIWebSocketTransport(alpaca_app) as websocket_transport:
            mounts = {
                "ws://news.test": websocket_transport,
                "https://discord.test": httpx2.MockTransport(discord_handler),
            }
            async with httpx2.AsyncClient(mounts=mounts, timeout=10) as client:
                application = asyncio.create_task(
                    run_application(config, client, logger, stop_event, stock_starter=stock_starter)
                )
                try:
                    await asyncio.wait_for(delivery_started.wait(), timeout=2)
                    if stock_completion != "completed-with-queued-trade":
                        events = stock_queues[0]
                        await asyncio.wait_for(events.consumer_waiting.wait(), timeout=2)
                        if stock_completion == "pending-with-queued-trade":
                            # Queue a final trade after the reader's completion callback is registered.
                            stock_tasks[0].add_done_callback(
                                lambda _task: events.put_nowait(Trade(symbol="SPY", price=500.25, size=100))
                            )
                        finish_stock.set()
                        await asyncio.wait_for(stock_tasks[0], timeout=2)

                    if expect_trade:
                        await asyncio.wait_for(trade_logged.wait(), timeout=2)
                    assert not application.done()
                    release_delivery.set()
                    await asyncio.wait_for(application, timeout=2)
                    await asyncio.wait_for(news_disconnected.wait(), timeout=2)
                finally:
                    release_delivery.set()
                    finish_stock.set()
                    if not application.done():
                        application.cancel()
                    await asyncio.gather(application, return_exceptions=True)

        assert stop_event.is_set()
        assert len(delivered) == 1
        assert delivered[0]["embeds"][0]["title"] == "AAPL: Apple launches a phone"
        assert ("stock trade" in output.getvalue()) is expect_trade

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
                            {"T": "error", "code": 403, "msg": "rejected startup-test-secret"},
                        ]
                    )

        config = Config(
            alpaca_api_key="startup-test-key",
            alpaca_api_secret="startup-test-secret",
            alpaca_news_stream_url="ws://news.test/v1beta1/news",
            alpaca_stock_stream_url="wss://stocks.test/v2",
            discord_analyst_webhooks=(),
            discord_earnings_webhooks=(),
            discord_news_webhooks=(),
            stock_logo="https://example.test/%s.webp",
            transparent_png="https://example.test/transparent.png",
            blocked_phrases=(),
        )
        stop_event = asyncio.Event()
        logger = logging.Logger("startup-terminal-test")

        async with ASGIWebSocketTransport(alpaca_app) as websocket_transport:
            async with httpx2.AsyncClient(mounts={"ws://news.test": websocket_transport}, timeout=10) as client:
                with pytest.raises(RuntimeError, match="alpaca news stream terminated") as error:
                    await run_application(config, client, logger, stop_event, stock_starter=None)

        assert "startup-test-secret" not in str(error.value)
        assert stop_event.is_set()
        assert disconnected.is_set()

    asyncio.run(scenario())


def test_run_application_fails_on_terminated_stock_handle_while_news_is_open() -> None:
    """Verify a terminated stock handle fails and closes the active news stream."""

    async def scenario() -> None:
        news_subscribed = asyncio.Event()
        news_disconnected = asyncio.Event()
        news_app_task = None
        stock_tasks: list[asyncio.Task[None]] = []
        webhook_requests: list[httpx2.Request] = []
        failure_message = "Alpaca trades stream connection failed: WebSocketNetworkError"
        stock_failure = AlpacaStreamError(failure_message)

        async def alpaca_app(scope, receive, send) -> None:
            nonlocal news_app_task
            news_app_task = asyncio.current_task()
            assert scope["path"] == "/v1beta1/news"
            await receive()
            await send({"type": "websocket.accept"})

            async def send_json(payload: object) -> None:
                await send({"type": "websocket.send", "text": json.dumps(payload)})

            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    news_disconnected.set()
                    return

                request = json.loads(message["text"])
                if request["action"] == "auth":
                    await send_json({"T": "success", "msg": "authenticated"})
                else:
                    await send_json({"T": "subscription", "news": ["*"]})
                    news_subscribed.set()

        async def discord_handler(request: httpx2.Request) -> httpx2.Response:
            webhook_requests.append(request)
            return httpx2.Response(204)

        async def stock_starter(
            _client,
            *,
            base_url,
            api_key,
            api_secret,
            stop_event,
            on_terminated,
        ) -> StreamHandle[Trade]:
            await news_subscribed.wait()

            async def completed() -> None:
                return

            task = asyncio.create_task(completed())
            await task
            assert not news_disconnected.is_set()
            on_terminated(stock_failure)
            stock_tasks.append(task)
            return StreamHandle(events=asyncio.Queue(), task=task)

        config = Config(
            alpaca_api_key="startup-test-key",
            alpaca_api_secret="startup-test-secret",
            alpaca_news_stream_url="ws://news.test/v1beta1/news",
            alpaca_stock_stream_url="wss://stocks.test/v2",
            discord_analyst_webhooks=(),
            discord_earnings_webhooks=(),
            discord_news_webhooks=("https://discord.test/webhook/startup-test-token",),
            stock_logo="https://example.test/%s.webp",
            transparent_png="https://example.test/transparent.png",
            blocked_phrases=(),
        )
        stop_event = asyncio.Event()
        logger = logging.Logger("startup-stock-termination-test")

        async with ASGIWebSocketTransport(alpaca_app) as websocket_transport:
            mounts = {
                "ws://news.test": websocket_transport,
                "https://discord.test": httpx2.MockTransport(discord_handler),
            }
            async with httpx2.AsyncClient(mounts=mounts, timeout=10) as client:
                # Context-owned tasks belong to the baseline. The ASGI server task
                # created for this request is awaited before checking for new leaks.
                tasks_before_application = asyncio.all_tasks()
                with pytest.raises(RuntimeError, match="alpaca stock stream terminated") as error:
                    await asyncio.wait_for(
                        run_application(config, client, logger, stop_event, stock_starter=stock_starter),
                        timeout=2,
                    )

                assert str(error.value) == f"alpaca stock stream terminated: {failure_message}"
                assert "startup-test-secret" not in str(error.value)
                assert stop_event.is_set()
                assert len(stock_tasks) == 1 and stock_tasks[0].done()
                await asyncio.wait_for(news_disconnected.wait(), timeout=2)
                assert news_app_task is not None
                await asyncio.wait_for(news_app_task, timeout=2)

                new_pending_tasks = asyncio.all_tasks() - tasks_before_application
                current_task = asyncio.current_task()
                if current_task is not None:
                    new_pending_tasks.discard(current_task)
                assert not new_pending_tasks, f"pending tasks after stock termination: {new_pending_tasks!r}"
                assert webhook_requests == []

    asyncio.run(scenario())


@pytest.mark.allow_hosts(["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    "stop_during_authentication",
    [False, True],
    ids=["after-subscription", "during-authentication"],
)
def test_sigterm_stops_real_news_and_trade_streams_cleanly(stop_during_authentication: bool) -> None:
    """Verify SIGTERM stops both streams during authentication or after subscription."""

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
