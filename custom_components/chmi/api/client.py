"""Minimal HTTP client with conditional GET support."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import aiohttp
from homeassistant.util.json import json_loads

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)

# Station and metadata file names contain the date, so their URLs change every
# day; the cache keeps only the most recently used ones to stay bounded.
MAX_CACHED_RESPONSES = 16


class ChmiApiError(Exception):
    """Raised when data cannot be retrieved from ČHMÚ."""


@dataclass(slots=True)
class _CacheEntry:
    """Stored response used for conditional requests."""

    payload: bytes
    etag: str | None = None
    last_modified: str | None = None


class ChmiClient:
    """Fetch files from the ČHMÚ open data servers.

    All station and alert files are static documents that are rewritten
    periodically, so every request is sent conditionally; unchanged files come
    back as HTTP 304 and are served from the in-memory cache.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with a shared aiohttp session."""
        self._session = session
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()

    def forget(self, url: str) -> None:
        """Drop a cached response, e.g. when a file rolls over to a new day."""
        self._cache.pop(url, None)

    async def async_get_bytes(
        self, url: str, *, allow_missing: bool = False, cache: bool = True
    ) -> bytes | None:
        """Return the body of a file, or None when it does not exist."""
        headers: dict[str, str] = {}
        entry = self._cache.get(url) if cache else None
        if entry is not None:
            self._cache.move_to_end(url)
            if entry.etag:
                headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                headers["If-Modified-Since"] = entry.last_modified

        try:
            response = await self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            )
            async with response:
                if response.status == HTTPStatus.NOT_MODIFIED and entry is not None:
                    return entry.payload
                if response.status == HTTPStatus.NOT_FOUND:
                    if allow_missing:
                        return None
                    raise ChmiApiError(f"{url} not found")
                if response.status != HTTPStatus.OK:
                    raise ChmiApiError(f"{url} returned HTTP {response.status}")
                payload = await response.read()
        except TimeoutError as err:
            raise ChmiApiError(f"Timeout while fetching {url}") from err
        except aiohttp.ClientError as err:
            raise ChmiApiError(f"Error while fetching {url}: {err}") from err

        if cache:
            self._cache[url] = _CacheEntry(
                payload=payload,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
            self._cache.move_to_end(url)
            while len(self._cache) > MAX_CACHED_RESPONSES:
                self._cache.popitem(last=False)
        return payload

    async def async_get_json(
        self, url: str, *, allow_missing: bool = False
    ) -> Any | None:
        """Return a parsed JSON document, or None when it does not exist."""
        payload = await self.async_get_bytes(url, allow_missing=allow_missing)
        if payload is None:
            return None
        try:
            return json_loads(payload)
        except ValueError as err:
            raise ChmiApiError(f"{url} is not valid JSON: {err}") from err

    async def async_get_text(
        self, url: str, *, allow_missing: bool = False, cache: bool = True
    ) -> str | None:
        """Return a text document, or None when it does not exist."""
        payload = await self.async_get_bytes(
            url, allow_missing=allow_missing, cache=cache
        )
        if payload is None:
            return None
        return payload.decode("utf-8", errors="replace")
