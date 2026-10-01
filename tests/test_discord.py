"""Behavioral tests for Discord embeds and webhook delivery."""

import asyncio
import json

import httpx2
import pytest

from stocknews.discord import (
    analyst_payload,
    earnings_payload,
    news_payload,
    send_payload,
)
from stocknews.models import NewsItem

STOCK_LOGO = "https://static.stocktitan.net/company-logo/%s.webp"
TRANSPARENT_PNG = "https://major.io/transparent.png"


def test_earnings_payload_omits_empty_optional_fields_and_sorts_lines() -> None:
    """Format earnings embeds and send them as JSON to the webhook."""
    payload = earnings_payload(
        "AAPL",
        "Apple Q1 EPS $2.00 beat $1.80 Estimate, Sales $100B miss $105B Estimate",
        STOCK_LOGO,
        TRANSPARENT_PNG,
    )

    assert payload == {
        "embeds": [
            {
                "title": "AAPL: Apple",
                "description": "💔 Sales: $100B vs. $105B est.\n💚 EPS: $2.00 vs. $1.80 est.",
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
            }
        ]
    }

    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert json.loads(request.content) == payload
        return httpx2.Response(204)

    async def send() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            await send_payload(client, ["https://discord.test/webhook/earnings"], payload)

    asyncio.run(send())
    assert len(requests) == 1
    assert earnings_payload("AAPL", "Apple launches a product", STOCK_LOGO, TRANSPARENT_PNG) is None


@pytest.mark.parametrize(
    ("headline", "title", "color"),
    [
        (
            "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
            "💚 AAPL: Apple $223.00",
            0x4CAF50,
        ),
        (
            "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135",
            "💔 AMZN: Amazon $135.00",
            0xD42020,
        ),
        (
            "Baird Upgrades Apple to Outperform, Confirms positive outlook",
            "❓ AAPL: Apple $0.00",
            0,
        ),
    ],
)
def test_analyst_payload_formats_target_action_and_unknown_fallback(
    headline: str,
    title: str,
    color: int,
) -> None:
    """Format analyst targets, colors, and fallback titles in webhook embeds."""
    symbol = "AAPL" if "Apple" in headline else "AMZN"

    payload = analyst_payload(symbol, headline, STOCK_LOGO, TRANSPARENT_PNG)

    expected = {
        "embeds": [
            {
                "title": title,
                "description": headline,
                "color": color,
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": f"https://static.stocktitan.net/company-logo/{symbol.lower()}.webp"},
            }
        ]
    }
    assert payload == expected

    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert json.loads(request.content) == expected
        return httpx2.Response(204)

    async def send() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            await send_payload(client, ["https://discord.test/webhook/analyst"], payload)

    asyncio.run(send())
    assert len(requests) == 1


@pytest.mark.parametrize(
    "headline",
    [
        "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850",
        "Goldman Sachs Maintains Buy on Apple, Maintains Price Target at $223",
    ],
)
def test_analyst_payload_suppresses_announced_or_maintained_targets(headline: str) -> None:
    """Omit analyst embeds for announced or maintained price targets."""
    assert analyst_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG) is None


def test_analyst_payload_omits_empty_headline_description() -> None:
    """Leave the description field out when the analyst headline is empty."""
    payload = analyst_payload("AAPL", "", STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    assert "description" not in payload["embeds"][0]


def test_news_payload_uses_first_symbol_and_omits_empty_optional_fields() -> None:
    """Use the first symbol and omit empty news fields from the embed."""
    item = NewsItem(
        symbols=("AAPL", "MSFT"),
        author="Benzinga Newsdesk",
        headline="Apple releases new iPhone",
        summary="Company announcement",
        url="https://example.test/news",
    )

    payload = news_payload(item, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload == {
        "embeds": [
            {
                "title": "AAPL: Apple releases new iPhone",
                "description": "Company announcement",
                "url": "https://example.test/news",
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
            }
        ]
    }

    no_optional_fields = NewsItem(
        symbols=("AAPL",),
        author="Benzinga Newsdesk",
        headline="Apple headline",
        summary="",
        url="",
    )
    assert news_payload(no_optional_fields, STOCK_LOGO, TRANSPARENT_PNG) == {
        "embeds": [
            {
                "title": "AAPL: Apple headline",
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
            }
        ]
    }
    assert (
        news_payload(
            NewsItem(symbols=(), author="", headline="No symbol", summary="", url=""),
            STOCK_LOGO,
            TRANSPARENT_PNG,
        )
        is None
    )


def test_send_payload_posts_json_sequentially_and_accepts_all_2xx() -> None:
    """Post the JSON payload to each webhook in order and accept all 2xx statuses."""
    requested: list[str] = []
    statuses = {"one": 200, "two": 201, "three": 204, "four": 299}
    item = NewsItem(
        symbols=("NVDA",),
        author="Benzinga Newsdesk",
        headline="Nvidia announces a product",
        summary="Product announcement",
        url="https://example.test/news",
    )
    payload = news_payload(item, STOCK_LOGO, TRANSPARENT_PNG)
    assert payload is not None

    async def handler(request: httpx2.Request) -> httpx2.Response:
        endpoint = request.url.path.rsplit("/", maxsplit=1)[-1]
        requested.append(endpoint)
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == payload
        return httpx2.Response(statuses[endpoint])

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            await send_payload(
                client,
                [f"https://discord.test/webhooks/{name}" for name in statuses],
                payload,
            )

    asyncio.run(run())
    assert requested == list(statuses)


def test_send_payload_isolates_failures_and_sanitizes_errors() -> None:
    """Continue webhook fanout after failures and omit private URLs from errors."""
    requested: list[str] = []
    secret_paths = {
        "status-secret": "status",
        "network-secret": "network",
        "success": "success",
    }

    async def handler(request: httpx2.Request) -> httpx2.Response:
        endpoint = request.url.path.rsplit("/", maxsplit=1)[-1]
        requested.append(endpoint)
        if endpoint == "status-secret":
            return httpx2.Response(503)
        if endpoint == "network-secret":
            raise httpx2.ConnectError("network failed", request=request)
        return httpx2.Response(204)

    async def run() -> str:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError) as error:
                await send_payload(
                    client,
                    [f"https://discord.test/webhooks/{path}" for path in secret_paths],
                    {"embeds": [{"title": "hello", "image": {"url": ""}, "thumbnail": {"url": ""}}]},
                )
            return str(error.value)

    error = asyncio.run(run())

    assert requested == list(secret_paths)
    assert error == "post webhook: unexpected status 503; post webhook: request failed"
    assert all(secret not in error for secret in secret_paths)


@pytest.mark.parametrize(
    ("bad_webhook", "secret_values"),
    [
        ("invalid-secret-token", ("invalid-secret-token",)),
        (
            "https://discord.test:not-a-port/webhook/private-token",
            ("not-a-port", "private-token"),
        ),
    ],
)
def test_send_payload_sanitizes_invalid_webhooks_and_continues(
    bad_webhook: str,
    secret_values: tuple[str, ...],
) -> None:
    """Skip malformed webhook URLs, sanitize their errors, and continue fanout."""
    requested: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(request.url.path)
        return httpx2.Response(200)

    async def run() -> str:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError) as error:
                await send_payload(
                    client,
                    [
                        bad_webhook,
                        "https://discord.test/webhook/ok-one",
                        "https://discord.test/webhook/ok-two",
                    ],
                    {"embeds": []},
                )
            return str(error.value)

    error = asyncio.run(run())

    assert requested == ["/webhook/ok-one", "/webhook/ok-two"]
    assert error == "build webhook request: invalid webhook URL"
    assert all(secret not in error for secret in secret_values)


def test_send_payload_serializes_before_sending() -> None:
    """Reject unserializable payloads before making any webhook request."""
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(TypeError):
                await send_payload(
                    client,
                    ["https://discord.test/webhook"],
                    {"embeds": [object()]},  # type: ignore[list-item]
                )

    asyncio.run(run())
    assert requests == []


def test_send_payload_does_not_hide_programming_errors() -> None:
    """Propagate unexpected errors raised by the transport handler."""

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        raise ValueError("transport handler bug")

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="transport handler bug"):
                await send_payload(
                    client,
                    ["https://discord.test/webhook"],
                    {"embeds": []},
                )

    asyncio.run(run())


async def _read_request_path(reader: asyncio.StreamReader) -> str | None:
    request_line = await reader.readline()
    if not request_line:
        return None

    headers: dict[str, str] = {}
    while line := await reader.readline():
        if line == b"\r\n":
            break
        name, value = line.decode().split(":", maxsplit=1)
        headers[name.lower()] = value.strip()

    await reader.readexactly(int(headers.get("content-length", "0")))
    return request_line.split()[1].decode()


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_send_payload_closes_unfinished_response_and_continues() -> None:
    """Close an unfinished response before continuing to the next webhook."""

    async def run() -> None:
        requested: list[str] = []
        first_response_closed = asyncio.Event()
        first_connection_end: list[bytes] = []

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                path = await _read_request_path(reader)
                if path is None:
                    return
                requested.append(path)

                if path == "/stalled":
                    writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n")
                    await writer.drain()
                    first_connection_end.append(await reader.read())
                    first_response_closed.set()
                else:
                    writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        address = server.sockets[0].getsockname()
        base_url = f"http://127.0.0.1:{address[1]}"
        try:
            async with httpx2.AsyncClient(timeout=0.5) as client:
                await send_payload(client, [f"{base_url}/stalled", f"{base_url}/next"], {"embeds": []})
                async with asyncio.timeout(1):
                    await first_response_closed.wait()
        finally:
            server.close()
            await server.wait_closed()

        assert requested == ["/stalled", "/next"]
        assert first_connection_end == [b""]

    asyncio.run(run())


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_send_payload_deadline_is_sanitized_and_fanout_continues() -> None:
    """Sanitize a request deadline error and continue sending to later webhooks."""

    async def run() -> None:
        requested: list[str] = []
        first_request_closed = asyncio.Event()
        first_connection_end: list[bytes] = []

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                path = await _read_request_path(reader)
                if path is None:
                    return
                requested.append(path)

                if path == "/private-token":
                    first_connection_end.append(await reader.read())
                    first_request_closed.set()
                else:
                    writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        address = server.sockets[0].getsockname()
        base_url = f"http://127.0.0.1:{address[1]}"
        try:
            async with httpx2.AsyncClient(timeout=None) as client:
                with pytest.raises(RuntimeError) as error:
                    await send_payload(
                        client,
                        [f"{base_url}/private-token", f"{base_url}/next"],
                        {"embeds": []},
                        deadline_seconds=0.25,
                    )
                async with asyncio.timeout(1):
                    await first_request_closed.wait()
        finally:
            server.close()
            await server.wait_closed()

        assert requested == ["/private-token", "/next"]
        assert first_connection_end == [b""]
        assert str(error.value) == "post webhook: deadline exceeded"
        assert "private-token" not in str(error.value)

    asyncio.run(run())


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_send_payload_propagates_external_cancellation() -> None:
    """Propagate caller cancellation without sending to later webhooks."""

    async def run() -> None:
        requested: list[str] = []
        first_request_received = asyncio.Event()
        release_request = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                path = await _read_request_path(reader)
                if path is None:
                    return
                requested.append(path)

                if path == "/slow":
                    first_request_received.set()
                    await release_request.wait()
                else:
                    writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        address = server.sockets[0].getsockname()
        base_url = f"http://127.0.0.1:{address[1]}"
        try:
            async with httpx2.AsyncClient(timeout=None) as client:
                task = asyncio.create_task(
                    send_payload(
                        client,
                        [f"{base_url}/slow", f"{base_url}/next"],
                        {"embeds": []},
                        deadline_seconds=10,
                    )
                )
                async with asyncio.timeout(1):
                    await first_request_received.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            release_request.set()
            server.close()
            await server.wait_closed()

        assert requested == ["/slow"]

    asyncio.run(run())
