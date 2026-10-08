"""Tests for aircraft.json URL resolution and fetching."""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from dump1090_alerts.fetch import FetchError, fetch_aircraft, resolve_url


def aircraft(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"hex": "abc123", "flight": "BAW117  ", "alt_baro": 34000}
    base.update(fields)
    return base


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def opener_for(payload: Any) -> Any:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        return FakeResponse(body)

    return opener


def test_resolve_url_appends_aircraft_path() -> None:
    assert resolve_url("http://localhost:8080") == "http://localhost:8080/data/aircraft.json"
    assert resolve_url("http://readsb.lan:8080/") == "http://readsb.lan:8080/data/aircraft.json"


def test_resolve_url_keeps_full_document_urls() -> None:
    full = "http://host:8080/data/aircraft.json"
    assert resolve_url(full) == full
    assert resolve_url(full + "/") == full


def test_fetch_returns_aircraft_array() -> None:
    seen: list[str] = []

    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        seen.append(url)
        return FakeResponse(
            json.dumps({"now": 1.0, "messages": 42, "aircraft": [aircraft()]}).encode()
        )

    entries = fetch_aircraft("http://decoder:8080", opener=opener)
    assert len(entries) == 1
    assert entries[0]["hex"] == "abc123"
    assert seen == ["http://decoder:8080/data/aircraft.json"]


def test_fetch_skips_non_object_entries() -> None:
    payload = {"aircraft": [aircraft(), "junk", 42, None]}
    entries = fetch_aircraft(opener=opener_for(payload))
    assert [entry["hex"] for entry in entries] == ["abc123"]


def test_fetch_missing_aircraft_array_is_empty() -> None:
    assert fetch_aircraft(opener=opener_for({"now": 1.0})) == []
    assert fetch_aircraft(opener=opener_for({"aircraft": None})) == []


def test_fetch_rejects_non_json_body() -> None:
    with pytest.raises(FetchError, match="did not return JSON"):
        fetch_aircraft(opener=opener_for(b"<html>proxy error</html>"))


def test_fetch_rejects_non_object_document() -> None:
    with pytest.raises(FetchError, match="expected an object"):
        fetch_aircraft(opener=opener_for([1, 2, 3]))


def test_fetch_rejects_non_list_aircraft_field() -> None:
    with pytest.raises(FetchError, match="'aircraft' as dict, expected a list"):
        fetch_aircraft(opener=opener_for({"aircraft": {"abc123": {}}}))


def test_fetch_reports_network_failure() -> None:
    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        raise urllib.error.URLError("connection refused")

    with pytest.raises(FetchError, match=r"cannot read http://localhost:8080/data/aircraft.json"):
        fetch_aircraft(opener=opener)


def test_fetch_reports_os_error() -> None:
    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        raise OSError("network is unreachable")

    with pytest.raises(FetchError, match="network is unreachable"):
        fetch_aircraft("http://remote:8080", opener=opener)
