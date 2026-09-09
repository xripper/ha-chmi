"""Tests of the HTTP client: conditional requests and the bounded cache."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus

import aiohttp
import pytest

from custom_components.chmi.api.client import (
    MAX_CACHED_RESPONSES,
    ChmiApiError,
    ChmiClient,
)

URL = "https://opendata.chmi.cz/meteorology/climate/now/data/10m-x-20260909.json"


class FakeResponse:
    """A canned aiohttp response."""

    def __init__(
        self, status: int, body: bytes = b"", headers: dict[str, str] | None = None
    ) -> None:
        """Store the response to serve."""
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def read(self) -> bytes:
        """Return the response body."""
        return self._body

    async def __aenter__(self) -> FakeResponse:
        """Enter the response context."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Leave the response context."""
        return None


class FakeSession:
    """A session serving queued responses and recording request headers."""

    def __init__(self, responses: Iterator[FakeResponse | Exception]) -> None:
        """Store the responses to serve in order."""
        self._responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, headers: dict[str, str], timeout: object):
        """Serve the next queued response."""
        self.requests.append((url, dict(headers)))
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def _client(*responses: FakeResponse | Exception) -> tuple[ChmiClient, FakeSession]:
    """Build a client over a session serving the given responses."""
    session = FakeSession(iter(responses))
    return ChmiClient(session), session


async def test_unchanged_file_is_served_from_the_cache() -> None:
    """The second request is conditional and a 304 reuses the stored body."""
    client, session = _client(
        FakeResponse(
            HTTPStatus.OK,
            b'{"a": 1}',
            {"ETag": '"abc"', "Last-Modified": "Wed, 09 Sep 2026 11:02:03 GMT"},
        ),
        FakeResponse(HTTPStatus.NOT_MODIFIED),
    )

    assert await client.async_get_bytes(URL) == b'{"a": 1}'
    assert session.requests[0][1] == {}

    assert await client.async_get_bytes(URL) == b'{"a": 1}'
    assert session.requests[1][1] == {
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Wed, 09 Sep 2026 11:02:03 GMT",
    }


async def test_forgotten_response_is_requested_unconditionally() -> None:
    """Dropping a cached response clears its validators."""
    client, session = _client(
        FakeResponse(HTTPStatus.OK, b"first", {"ETag": '"1"'}),
        FakeResponse(HTTPStatus.OK, b"second", {"ETag": '"2"'}),
    )

    await client.async_get_bytes(URL)
    client.forget(URL)
    assert await client.async_get_bytes(URL) == b"second"
    assert session.requests[1][1] == {}


async def test_cache_is_bounded_and_evicts_the_least_recently_used() -> None:
    """Dated file names must not grow the cache without bound."""
    count = MAX_CACHED_RESPONSES + 4
    session = FakeSession(
        iter(lambda: FakeResponse(HTTPStatus.OK, b"x", {"ETag": '"e"'}), None)
    )
    client = ChmiClient(session)

    for index in range(count):
        await client.async_get_bytes(f"{URL}?{index}")

    cached = list(client._cache)
    assert len(cached) == MAX_CACHED_RESPONSES
    assert f"{URL}?0" not in cached  # evicted
    assert f"{URL}?{count - 1}" in cached

    # Touching an entry keeps it, the next eviction takes another one.
    oldest = cached[0]
    await client.async_get_bytes(oldest)
    await client.async_get_bytes(f"{URL}?fresh")
    assert oldest in client._cache
    assert cached[1] not in client._cache


async def test_uncached_requests_leave_no_entry() -> None:
    """Radar frames have a unique URL each, so they are never cached."""
    client, _session = _client(FakeResponse(HTTPStatus.OK, b"png"))
    await client.async_get_bytes(URL, cache=False)
    assert not client._cache


async def test_missing_file() -> None:
    """A 404 is either tolerated or reported, depending on the caller."""
    client, _session = _client(
        FakeResponse(HTTPStatus.NOT_FOUND), FakeResponse(HTTPStatus.NOT_FOUND)
    )
    assert await client.async_get_bytes(URL, allow_missing=True) is None
    with pytest.raises(ChmiApiError, match="not found"):
        await client.async_get_bytes(URL)


async def test_server_error_and_transport_errors_are_wrapped() -> None:
    """Every failure reaches the coordinator as a ChmiApiError."""
    client, _session = _client(
        FakeResponse(HTTPStatus.INTERNAL_SERVER_ERROR),
        aiohttp.ClientError("boom"),
        TimeoutError(),
    )
    with pytest.raises(ChmiApiError, match="HTTP 500"):
        await client.async_get_bytes(URL)
    with pytest.raises(ChmiApiError, match="Error while fetching"):
        await client.async_get_bytes(URL)
    with pytest.raises(ChmiApiError, match="Timeout"):
        await client.async_get_bytes(URL)


async def test_invalid_json_is_reported() -> None:
    """A truncated document is reported instead of raising ValueError."""
    client, _session = _client(FakeResponse(HTTPStatus.OK, b"{not json"))
    with pytest.raises(ChmiApiError, match="not valid JSON"):
        await client.async_get_json(URL)
