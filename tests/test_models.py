"""Tests for aircraft field helpers, distance maths and the alert payload."""

from __future__ import annotations

import json
from typing import Any

from dump1090_alerts.models import (
    Alert,
    altitude_ft,
    as_number,
    callsign,
    describe_aircraft,
    haversine_nm,
    is_on_ground,
)


def aircraft(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"hex": "abc123", "flight": "BAW117  ", "alt_baro": 34000}
    base.update(fields)
    return base


def test_altitude_ground_string_is_zero() -> None:
    assert altitude_ft(aircraft(alt_baro="ground")) == 0.0


def test_altitude_numeric_coerced_to_float() -> None:
    assert altitude_ft(aircraft(alt_baro=34000)) == 34000.0
    assert altitude_ft(aircraft(alt_baro=1250.5)) == 1250.5


def test_altitude_missing_or_text_is_unknown() -> None:
    assert altitude_ft(aircraft(alt_baro=None)) is None
    assert altitude_ft(aircraft(alt_baro="high")) is None
    entry = aircraft()
    del entry["alt_baro"]
    assert altitude_ft(entry) is None


def test_is_on_ground_tristate() -> None:
    assert is_on_ground(aircraft(alt_baro="ground")) is True
    assert is_on_ground(aircraft(alt_baro=30000)) is False
    assert is_on_ground(aircraft(alt_baro=None)) is None


def test_as_number_rejects_bool_and_text() -> None:
    assert as_number(True) is None
    assert as_number("12") is None
    assert as_number(None) is None
    assert as_number(7) == 7.0


def test_callsign_trims_and_handles_bad_types() -> None:
    assert callsign(aircraft()) == "BAW117"
    assert callsign(aircraft(flight="  ")) == ""
    assert callsign(aircraft(flight=123456)) == ""
    assert callsign(aircraft()) == callsign(aircraft())


def test_haversine_one_degree_of_latitude() -> None:
    assert 59.0 < haversine_nm(0.0, 0.0, 1.0, 0.0) < 61.0


def test_haversine_same_point_is_zero() -> None:
    assert haversine_nm(51.5, -0.5, 51.5, -0.5) == 0.0


def test_haversine_longitude_shrinks_with_latitude() -> None:
    equator = haversine_nm(0.0, 0.0, 0.0, 1.0)
    north = haversine_nm(60.0, 0.0, 60.0, 1.0)
    assert 59.0 < equator < 61.0
    assert 28.0 < north < 32.0


def test_describe_includes_ground_squawk_emergency_rssi() -> None:
    text = describe_aircraft(
        aircraft(alt_baro="ground", squawk="7700", emergency="major", rssi=-4.5)
    )
    assert "ABC123" in text
    assert "BAW117" in text
    assert "on ground" in text
    assert "squawk 7700" in text
    assert "emergency=major" in text
    assert "rssi -4.5 dBFS" in text


def test_describe_includes_altitude_and_position() -> None:
    text = describe_aircraft(aircraft(lat=51.5, lon=-0.5, alt_baro=12000))
    assert "12,000 ft" in text
    assert "pos 51.5000,-0.5000" in text


def test_describe_minimal_aircraft_is_hex_only() -> None:
    assert describe_aircraft({"hex": "fff000"}) == "FFF000"


def test_alert_to_dict_is_json_round_trippable() -> None:
    alert = Alert(
        rule="emergency",
        description="squawk",
        message="[emergency] ABC123",
        hex="abc123",
        ts=1700000000.5,
        aircraft=aircraft(),
    )
    payload = json.loads(json.dumps(alert.to_dict()))
    assert payload["rule"] == "emergency"
    assert payload["hex"] == "abc123"
    assert payload["ts"] == 1700000000.5
    assert payload["aircraft"]["flight"] == "BAW117  "
