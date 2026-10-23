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
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096


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
    assert "username" not in payload

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
    ("company_name", "expected_title"),
    [
        ("  Apple Inc.  ", "AAPL: Apple Inc."),
        (None, "AAPL: Apple"),
        ("", "AAPL: Apple"),
        (" \t ", "AAPL: Apple"),
    ],
)
def test_earnings_payload_prefers_nonblank_company_name_override(
    company_name: str | None,
    expected_title: str,
) -> None:
    """Use a stripped company override while preserving the earnings embed."""
    payload = earnings_payload(
        "AAPL",
        "Apple Q1 EPS $2.00 beat $1.80 Estimate",
        STOCK_LOGO,
        TRANSPARENT_PNG,
        company_name,
    )

    assert payload == {
        "embeds": [
            {
                "title": expected_title,
                "description": "💚 EPS: $2.00 vs. $1.80 est.",
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
            }
        ]
    }


@pytest.mark.parametrize("company_name", [None, "Apple Inc."])
def test_earnings_payload_suppresses_headline_without_earnings_data(
    company_name: str | None,
) -> None:
    """Keep company-name overrides from creating an earnings embed by themselves."""
    assert (
        earnings_payload(
            "AAPL",
            "Apple launches a product",
            STOCK_LOGO,
            TRANSPARENT_PNG,
            company_name,
        )
        is None
    )


def test_earnings_payload_uses_company_name_without_symbol() -> None:
    """Build an earnings embed without a ticker prefix when no symbol is retained."""
    payload = earnings_payload(
        "",
        "Apple Q1 EPS $2.00 beat $1.80 Estimate",
        STOCK_LOGO,
        TRANSPARENT_PNG,
    )

    assert payload == {
        "embeds": [
            {
                "title": "Apple",
                "description": "💚 EPS: $2.00 vs. $1.80 est.",
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": TRANSPARENT_PNG},
            }
        ]
    }
    assert "username" not in payload


def test_earnings_payload_uses_headline_when_company_name_is_missing() -> None:
    """Keep a tickerless earnings title nonempty when no company marker is present."""
    headline = "EPS $2.00 beat $1.80 Estimate"

    payload = earnings_payload("", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    assert payload["embeds"][0]["title"] == headline
    assert payload["embeds"][0]["thumbnail"] == {"url": TRANSPARENT_PNG}


@pytest.mark.parametrize(
    ("symbol", "headline", "description", "color"),
    [
        (
            "AAPL",
            "Goldman Sachs Maintains Buy on Apple, Raises Price Target to $223",
            "Target raised to $223 · Buy maintained · Goldman Sachs",
            0x4CAF50,
        ),
        (
            "AAPL",
            "Goldman Sachs Maintains Buy on Apple, Lowers Price Target to $200",
            "Target lowered to $200 · Buy maintained · Goldman Sachs",
            0xD42020,
        ),
        (
            "NSC",
            "RBC Capital Maintains Sector Perform on Norfolk Southern, Raises Price Target to $350.25",
            "Target raised to $350.25 · Sector Perform maintained · RBC Capital",
            0x4CAF50,
        ),
        (
            "AAPL",
            "Goldman Sachs Maintains Buy on Apple, Confirms positive outlook",
            "Buy maintained · Goldman Sachs",
            0,
        ),
        (
            "AMZN",
            "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135",
            "Target lowered to $135 · Downgraded to Neutral · JPMorgan",
            0xD42020,
        ),
        (
            "AAPL",
            "Baird Upgrades Apple to Outperform, Confirms positive outlook",
            "Upgraded to Outperform · Baird",
            0x4CAF50,
        ),
    ],
)
def test_analyst_payload_formats_description_and_color(
    symbol: str,
    headline: str,
    description: str,
    color: int,
) -> None:
    """Format analyst details in target, rating, and firm order."""
    payload = analyst_payload(symbol, headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload == {
        "embeds": [
            {
                "title": symbol,
                "description": description,
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": STOCK_LOGO.replace("%s", symbol.lower())},
                "color": color,
            }
        ],
        "username": "Analyst Wire",
    }


def test_analyst_payload_uses_company_name_and_stable_username() -> None:
    """Add an optional company name to a ticker title and keep the username stable."""
    payload = analyst_payload(
        "ARGX",
        "Guggenheim Maintains Buy on argenx, Lowers Price Target to $1120",
        STOCK_LOGO,
        TRANSPARENT_PNG,
        company_name="argenx",
    )

    assert payload == {
        "embeds": [
            {
                "title": "ARGX · argenx",
                "description": "Target lowered to $1,120 · Buy maintained · Guggenheim",
                "image": {"url": TRANSPARENT_PNG},
                "thumbnail": {"url": "https://static.stocktitan.net/company-logo/argx.webp"},
                "color": 0xD42020,
            }
        ],
        "username": "Analyst Wire",
    }


@pytest.mark.parametrize(
    ("headline", "symbol", "title", "color"),
    [
        (
            "JP Morgan Upgrades CME Group to Overweight, Announces $367 Price Target",
            "CME",
            "Target $367 · Upgraded to Overweight · JP Morgan",
            0x4CAF50,
        ),
        (
            "Argus Research Downgrades Alcoa to Hold",
            "AA",
            "Downgraded to Hold · Argus Research",
            0xD42020,
        ),
        (
            "Argus Research Upgrades Equifax to Buy",
            "EFX",
            "Upgraded to Buy · Argus Research",
            0x4CAF50,
        ),
        (
            "Citizens Upgrades Omega Healthcare Invts to Market Outperform",
            "OHI",
            "Upgraded to Market Outperform · Citizens",
            0x4CAF50,
        ),
    ],
)
def test_analyst_payload_keeps_rating_changes_without_positive_target(
    headline: str,
    symbol: str,
    title: str,
    color: int,
) -> None:
    """Show rating changes even when the headline has no positive target."""
    payload = analyst_payload(symbol, headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    embed = payload["embeds"][0]
    assert embed["description"] == title
    assert embed["color"] == color
    assert embed["thumbnail"] == {"url": STOCK_LOGO.replace("%s", symbol.lower())}
    assert "author" not in embed
    assert embed["image"] == {"url": TRANSPARENT_PNG}


@pytest.mark.parametrize(
    ("headline", "title", "color"),
    [
        (
            "Firm Upgrades Example Corp to Buy, Maintains Price Target at $100",
            "Target $100 · Upgraded to Buy · Firm",
            0x4CAF50,
        ),
        (
            "Firm Upgrades Example Corp to Buy, Lowers Price Target to $90",
            "Target lowered to $90 · Upgraded to Buy · Firm",
            0x4CAF50,
        ),
        (
            "Firm Upgrades Example Corp to Buy, lowers Price Target to $90",
            "Target $90 · Upgraded to Buy · Firm",
            0x4CAF50,
        ),
        (
            "Firm Downgrades Example Corp to Sell, Raises Price Target to $110",
            "Target raised to $110 · Downgraded to Sell · Firm",
            0xD42020,
        ),
        (
            "Firm upGRADES Example Corp to Buy, Raises Price Target to $110",
            "Target raised to $110 · Upgraded to Buy · Firm",
            0x4CAF50,
        ),
    ],
)
def test_rating_direction_takes_priority_over_target_direction(
    headline: str,
    title: str,
    color: int,
) -> None:
    """Use rating direction for upgraded and downgraded analyst reports."""
    payload = analyst_payload("EX", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    embed = payload["embeds"][0]
    assert embed["description"] == title
    assert embed["color"] == color
    assert embed["thumbnail"] == {"url": STOCK_LOGO.replace("%s", "ex")}
    assert "author" not in embed
    assert embed["image"] == {"url": TRANSPARENT_PNG}


@pytest.mark.parametrize(
    "headline",
    [
        "Piper Sandler Initiates Coverage on Nvidia to Overweight, Announces Price Target to $850",
        "Goldman Sachs Maintains Buy on Apple, Maintains Price Target at $223",
        "Wedbush Assumes Rithm Capital at Outperform, Announces Price Target of $10",
    ],
)
def test_analyst_payload_suppresses_announced_or_maintained_targets(headline: str) -> None:
    """Omit analyst embeds for announced or maintained price targets."""
    assert analyst_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG) is None


def test_analyst_payload_omits_empty_description() -> None:
    """Leave the description field out when no analyst details were parsed."""
    payload = analyst_payload("AAPL", "", STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    assert "description" not in payload["embeds"][0]


def test_analyst_payload_uses_report_stock_without_symbol() -> None:
    """Use the parsed stock as the title when there is no supplied ticker."""
    headline = "JPMorgan Downgrades Amazon with Neutral Rating, Lowers Price Target to $135"

    payload = analyst_payload("", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload == {
        "embeds": [
            {
                "title": "Amazon",
                "description": "Target lowered to $135 · Downgraded to Neutral · JPMorgan",
                "image": {"url": TRANSPARENT_PNG},
                "color": 0xD42020,
            }
        ],
        "username": "Analyst Wire",
    }


@pytest.mark.parametrize(
    ("symbol", "headline", "title", "thumbnail"),
    [
        (
            "AAPL",
            "Headline without a recognized analyst format",
            "AAPL",
            {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
        ),
        (
            "",
            "Headline without a recognized analyst format",
            "Headline without a recognized analyst format",
            None,
        ),
    ],
)
def test_analyst_payload_uses_headline_for_unparsed_report(
    symbol: str,
    headline: str,
    title: str,
    thumbnail: dict[str, str] | None,
) -> None:
    """Keep unknown reports visible through their title fallback."""
    payload = analyst_payload(symbol, headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    embed = payload["embeds"][0]
    assert embed["title"] == title
    assert embed.get("thumbnail") == thumbnail
    assert embed["description"] == headline
    assert "author" not in embed


def test_analyst_payload_limits_unknown_headline_description() -> None:
    """Keep an unparsed headline visible while bounding its description length."""
    headline = "Unrecognized analyst headline " * 200

    payload = analyst_payload("AAPL", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    embed = payload["embeds"][0]
    assert embed["title"] == "AAPL"
    assert embed["description"] == f"{headline[: DESCRIPTION_LIMIT - 1]}…"
    assert len(embed["description"]) == DESCRIPTION_LIMIT


def test_analyst_payload_limits_title_length() -> None:
    """Keep a title with an unusually long company name within Discord's limit."""
    payload = analyst_payload("EX", "Firm Upgrades Example Corp to Buy", STOCK_LOGO, TRANSPARENT_PNG, "C" * 300)

    assert payload is not None
    embed = payload["embeds"][0]
    assert len(embed["title"]) == TITLE_LIMIT
    assert embed["title"].endswith("…")


def test_analyst_payload_limits_description_length() -> None:
    """Keep the combined analyst details within Discord's description limit."""
    firm = "F" * (DESCRIPTION_LIMIT + 100)
    headline = f"{firm} Upgrades Example Corp to Buy, Raises Price Target to $350"

    payload = analyst_payload("EX", headline, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload is not None
    embed = payload["embeds"][0]
    assert len(embed["description"]) == DESCRIPTION_LIMIT
    assert embed["description"].endswith("…")


def test_news_payload_uses_headline_only_with_first_symbol_and_optional_url() -> None:
    """Keep general-news embeds to the headline and optional URL, without body or image."""
    item = NewsItem(
        symbols=("AAPL", "MSFT"),
        author="Benzinga Newsdesk",
        headline="Apple releases new iPhone",
        summary="Company announcement",
        content="Full article body",
        url="https://example.test/news",
    )

    payload = news_payload(item, STOCK_LOGO, TRANSPARENT_PNG)

    assert payload == {
        "embeds": [
            {
                "title": "AAPL: Apple releases new iPhone",
                "url": "https://example.test/news",
                "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
            }
        ]
    }
    assert "username" not in payload

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
                "thumbnail": {"url": "https://static.stocktitan.net/company-logo/aapl.webp"},
            }
        ]
    }
    assert news_payload(
        NewsItem(symbols=(), author="", headline="No symbol", summary="", url=""),
        STOCK_LOGO,
        TRANSPARENT_PNG,
    ) == {
        "embeds": [
            {
                "title": "No symbol",
                "thumbnail": {"url": TRANSPARENT_PNG},
            }
        ]
    }


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
    network_failure_message = "network failed"

    async def handler(request: httpx2.Request) -> httpx2.Response:
        endpoint = request.url.path.rsplit("/", maxsplit=1)[-1]
        requested.append(endpoint)
        if endpoint == "status-secret":
            return httpx2.Response(503)
        if endpoint == "network-secret":
            raise httpx2.ConnectError(network_failure_message, request=request)
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
    transport_handler_error_message = "transport handler bug"

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        raise ValueError(transport_handler_error_message)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match=transport_handler_error_message):
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
