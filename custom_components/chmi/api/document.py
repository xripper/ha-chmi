"""Helpers for the ČHMÚ ``DataCollection`` JSON envelope.

Every open-data JSON file wraps its payload the same way::

    {"zaznamID": ..., "data": {"type": "DataCollection",
     "data": {"header": "A,B,C", "values": [[...], ...]}}}
"""

from __future__ import annotations

from typing import Any

from .client import ChmiApiError


def data_rows(document: Any) -> tuple[list[str], list[list[Any]]]:
    """Split a ``DataCollection`` document into its header and rows."""
    try:
        payload = document["data"]["data"]
        header = payload["header"].split(",")
        values = payload["values"]
    except (KeyError, TypeError, AttributeError) as err:
        raise ChmiApiError(f"Unexpected document structure: {err}") from err
    if not isinstance(values, list):
        raise ChmiApiError("Document values are not a list")
    return header, values
