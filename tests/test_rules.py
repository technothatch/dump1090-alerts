"""Tests for condition matching: every condition type, AND semantics, transitions."""

from __future__ import annotations

import re
from typing import Any

from dump1090_alerts.rules import EvalContext, Match, Near, evaluate

AIRBORNE_NOW = EvalContext(is_new=False, was_ground=False)
NEW_AIRBORNE = EvalContext(is_new=True, was_ground=None)
TOOK_OFF = EvalContext(is_new=False, was_ground=True)
LANDED = EvalContext(is_new=False, was_ground=False)


def aircraft(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "hex": "abc123",
        "flight": "BAW117  ",
        "alt_baro": 34000,
        "squawk": "1234",
        "emergency": "none",
        "category": "A3",
        "rssi": -3.2,
        "lat": 51.5000,
        "lon": -0.5000,
    }
    base.update(fields)
    return base


def test_hex_list_matches_case_insensitively() -> None:
    match = Match(hexes=frozenset({"abc123", "deadbe"}))
    assert evaluate(match, aircraft(), AIRBORNE_NOW)
    assert evaluate(match, aircraft(hex="ABC123"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(hex="fff000"), AIRBORNE_NOW)
    assert not evaluate(match, {"flight": "BAW117"}, AIRBORNE_NOW)


def test_callsign_prefix() -> None:
    match = Match(callsign_prefix="baw")
    assert evaluate(match, aircraft(), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(flight="EZY21   "), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(flight=None), AIRBORNE_NOW)


def test_callsign_regex() -> None:
    match = Match(callsign_pattern=re.compile(r"^BAW\d+$"))
    assert evaluate(match, aircraft(), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(flight="EZY21   "), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(flight=None), AIRBORNE_NOW)


def test_alt_above() -> None:
    match = Match(alt_above=30000)
    assert evaluate(match, aircraft(), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(alt_baro=20000), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(alt_baro="ground"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(alt_baro=None), AIRBORNE_NOW)


def test_alt_below_treats_ground_as_zero() -> None:
    match = Match(alt_below=10000)
    assert evaluate(match, aircraft(alt_baro=1500), AIRBORNE_NOW)
    assert evaluate(match, aircraft(alt_baro="ground"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(alt_baro=34000), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(alt_baro=None), AIRBORNE_NOW)


def test_near_uses_haversine_radius() -> None:
    match = Match(near=Near(lat=51.5, lon=-0.5, radius_nm=25.0))
    assert evaluate(match, aircraft(lat=51.51, lon=-0.50), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(lat=52.5, lon=-0.5), AIRBORNE_NOW)


def test_near_requires_position() -> None:
    match = Match(near=Near(lat=51.5, lon=-0.5, radius_nm=25.0))
    entry = aircraft()
    del entry["lat"]
    assert not evaluate(match, entry, AIRBORNE_NOW)
    assert not evaluate(match, aircraft(lat=None), AIRBORNE_NOW)


def test_squawk_list() -> None:
    match = Match(squawks=frozenset({"7700", "7600"}))
    assert evaluate(match, aircraft(squawk="7700"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(squawk="1234"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(squawk=None), AIRBORNE_NOW)


def test_emergency_flag_true() -> None:
    match = Match(emergency=True)
    assert evaluate(match, aircraft(emergency="major"), AIRBORNE_NOW)
    assert evaluate(match, aircraft(emergency="minor"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(emergency="none"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(emergency=None), AIRBORNE_NOW)


def test_emergency_flag_false_matches_none() -> None:
    match = Match(emergency=False)
    assert evaluate(match, aircraft(emergency="none"), AIRBORNE_NOW)
    assert evaluate(match, aircraft(emergency=None), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(emergency="radio"), AIRBORNE_NOW)


def test_category_prefix() -> None:
    assert evaluate(Match(category_prefix="A"), aircraft(), AIRBORNE_NOW)
    assert evaluate(Match(category_prefix="a3"), aircraft(), AIRBORNE_NOW)
    assert not evaluate(Match(category_prefix="B"), aircraft(), AIRBORNE_NOW)
    assert not evaluate(Match(category_prefix="A"), aircraft(category=None), AIRBORNE_NOW)


def test_rssi_threshold_inclusive() -> None:
    match = Match(rssi_min=-6.0)
    assert evaluate(match, aircraft(rssi=-6.0), AIRBORNE_NOW)
    assert evaluate(match, aircraft(rssi=-3.2), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(rssi=-10.0), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(rssi=None), AIRBORNE_NOW)


def test_on_ground_condition() -> None:
    assert evaluate(Match(on_ground=True), aircraft(alt_baro="ground"), AIRBORNE_NOW)
    assert evaluate(Match(on_ground=False), aircraft(), AIRBORNE_NOW)
    assert not evaluate(Match(on_ground=True), aircraft(), AIRBORNE_NOW)
    assert not evaluate(Match(on_ground=True), aircraft(alt_baro=None), AIRBORNE_NOW)


def test_new_condition() -> None:
    match = Match(new=True)
    assert evaluate(match, aircraft(), NEW_AIRBORNE)
    assert not evaluate(match, aircraft(), AIRBORNE_NOW)


def test_takeoff_transition() -> None:
    match = Match(takeoff=True)
    assert evaluate(match, aircraft(), TOOK_OFF)
    assert not evaluate(match, aircraft(), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(alt_baro="ground"), TOOK_OFF)
    assert not evaluate(match, aircraft(), NEW_AIRBORNE)


def test_landing_transition() -> None:
    match = Match(landing=True)
    assert evaluate(match, aircraft(alt_baro="ground"), AIRBORNE_NOW)
    assert not evaluate(match, aircraft(alt_baro="ground"), TOOK_OFF)
    assert not evaluate(match, aircraft(), LANDED)
    assert not evaluate(match, aircraft(alt_baro="ground"), NEW_AIRBORNE)


def test_conditions_are_anded() -> None:
    match = Match(hexes=frozenset({"abc123"}), alt_above=99999)
    assert not evaluate(match, aircraft(), AIRBORNE_NOW)
    matching = Match(hexes=frozenset({"abc123"}), alt_above=30000)
    assert evaluate(matching, aircraft(), AIRBORNE_NOW)


def test_combined_transitions_and_state() -> None:
    match = Match(new=True, takeoff=True, hexes=frozenset({"abc123"}))
    assert evaluate(match, aircraft(), EvalContext(is_new=True, was_ground=True))
    assert not evaluate(match, aircraft(), EvalContext(is_new=True, was_ground=False))
    assert not evaluate(match, aircraft(), EvalContext(is_new=False, was_ground=True))


def test_has_conditions_detects_every_kind() -> None:
    assert not Match().has_conditions
    assert Match(hexes=frozenset({"a"})).has_conditions
    assert Match(callsign_prefix="BAW").has_conditions
    assert Match(callsign_pattern=re.compile("B")).has_conditions
    assert Match(alt_above=100).has_conditions
    assert Match(alt_below=100).has_conditions
    assert Match(near=Near(lat=0.0, lon=0.0, radius_nm=1.0)).has_conditions
    assert Match(squawks=frozenset({"7700"})).has_conditions
    assert Match(emergency=True).has_conditions
    assert Match(category_prefix="A").has_conditions
    assert Match(rssi_min=-10.0).has_conditions
    assert Match(on_ground=True).has_conditions
    assert Match(new=True).has_conditions
    assert Match(takeoff=True).has_conditions
    assert Match(landing=True).has_conditions
