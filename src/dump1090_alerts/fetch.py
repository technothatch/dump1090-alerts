"""Fetching and decoding ``aircraft.json`` from a dump1090 / readsb decoder."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from dump1090_alerts.models import Aircraft

DEFAULT_HOST = "http://localhost:8080"
DEFAULT_TIMEOUT = 5.0
AIRCRAFT_PATH = "/data/aircraft.json"

__all__ = [
    "AIRCRAFT_PATH",
    "DEFAULT_HOST",
    "DEFAULT_TIMEOUT",
    "FetchError",
    "fetch_aircraft",
    "resolve_url",
]


class FetchError(RuntimeError):
    """The decoder endpoint could not be read or was not a valid aircraft.json."""


def resolve_url(url: str) -> str:
    """Accept a bare host (``http://host:8080``) or a full document URL.

    Anything that does not already point at a ``*.json`` document is treated
    as the decoder root and gets ``/data/aircraft.json`` appended, which is
    where both dump1090-fa and readsb publish their aircraft state.
    """
    cleaned = url.rstrip("/")
    if cleaned.endswith(".json"):
        return cleaned
    return cleaned + AIRCRAFT_PATH


def fetch_aircraft(
    url: str = DEFAULT_HOST,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[Aircraft]:
    """Fetch and decode one ``aircraft.json`` document.

    Args:
        url: Decoder root or full ``aircraft.json`` URL.
        timeout: Socket timeout in seconds.
        opener: Injectable for tests; mirrors ``urllib.request.urlopen``.

    Returns:
        The ``aircraft`` array. A missing or null array yields ``[]`` so a
        quiet decoder does not look like a crash; non-object entries are
        dropped rather than failing the whole poll.

    Raises:
        FetchError: Network failure, non-JSON body, or an unexpected shape.
    """
    endpoint = resolve_url(url)
    try:
        with opener(endpoint, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError(f"cannot read {endpoint}: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{endpoint} did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FetchError(f"{endpoint} returned a JSON {type(payload).__name__}, expected an object")
    aircraft = payload.get("aircraft")
    if aircraft is None:
        return []
    if not isinstance(aircraft, list):
        raise FetchError(
            f"{endpoint} returned 'aircraft' as {type(aircraft).__name__}, expected a list"
        )
    return [entry for entry in aircraft if isinstance(entry, dict)]
