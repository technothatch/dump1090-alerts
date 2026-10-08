"""Tests for poll evaluation: baseline, state transitions and cooldowns."""

from __future__ import annotations

import tomllib
from typing import Any

from dump1090_alerts.config import Config, parse_config
from dump1090_alerts.engine import AlertEngine


class Clock:
    """Deterministic stand-in for ``time.time``."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def make_config(text: str) -> Config:
    return parse_config(tomllib.loads(text), source="<test>")


def aircraft(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "hex": "abc123",
        "flight": "BAW117  ",
        "alt_baro": 34000,
        "emergency": "none",
        "squawk": "1234",
    }
    base.update(fields)
    return base


NEW_RULE = """
[[rules]]
name = "new-plane"
[rules.match]
new = true
[[rules.sinks]]
type = "shell"
command = ["x"]
"""

GROUND_RULE = """
[[rules]]
name = "on-ground"
[rules.match]
on_ground = true
[[rules.sinks]]
type = "shell"
command = ["x"]
"""

TAKEOFF_RULE = """
[[rules]]
name = "takeoff"
[rules.match]
takeoff = true
[[rules.sinks]]
type = "shell"
command = ["x"]
"""

LANDING_RULE = """
[[rules]]
name = "landing"
[rules.match]
landing = true
[[rules.sinks]]
type = "shell"
command = ["x"]
"""

EMERGENCY_RULE = """
[[rules]]
name = "emergency"
description = "emergency state"
[rules.match]
emergency = true
[[rules.sinks]]
type = "shell"
command = ["x"]
"""


def test_baseline_first_poll_is_silent_then_new_aircraft_fire() -> None:
    engine = AlertEngine(make_config(NEW_RULE), now=Clock())
    assert engine.poll([aircraft()]) == []
    assert engine.poll([aircraft()]) == []
    later = engine.poll([aircraft(), aircraft(hex="deadbe")])
    assert [f.alert.hex for f in later] == ["deadbe"]


def test_baseline_disabled_fires_on_first_poll() -> None:
    config = make_config("[engine]\nbaseline = false\n" + NEW_RULE)
    engine = AlertEngine(config, now=Clock())
    fired = engine.poll([aircraft()])
    assert len(fired) == 1
    assert fired[0].rule.name == "new-plane"


def test_once_mode_overrides_baseline() -> None:
    engine = AlertEngine(make_config(NEW_RULE), now=Clock(), baseline=False)
    assert len(engine.poll([aircraft()])) == 1


def test_new_condition_is_single_shot_even_with_zero_cooldown() -> None:
    config = make_config("[engine]\ncooldown = 0\nbaseline = false\n" + NEW_RULE)
    engine = AlertEngine(config, now=Clock())
    assert len(engine.poll([aircraft()])) == 1
    assert engine.poll([aircraft()]) == []


def test_takeoff_transition_across_polls() -> None:
    config = make_config("[engine]\nbaseline = false\n" + TAKEOFF_RULE)
    engine = AlertEngine(config, now=Clock())
    assert engine.poll([aircraft(alt_baro="ground")]) == []
    assert len(engine.poll([aircraft(alt_baro=1200)])) == 1
    assert engine.poll([aircraft(alt_baro=5000)]) == []


def test_landing_transition_across_polls() -> None:
    config = make_config("[engine]\nbaseline = false\n" + LANDING_RULE)
    engine = AlertEngine(config, now=Clock())
    assert engine.poll([aircraft(alt_baro=3000)]) == []
    assert len(engine.poll([aircraft(alt_baro="ground")])) == 1
    assert engine.poll([aircraft(alt_baro="ground")]) == []


def test_transition_blocked_when_previous_altitude_unknown() -> None:
    config = make_config("[engine]\nbaseline = false\n" + TAKEOFF_RULE)
    engine = AlertEngine(config, now=Clock())
    first = aircraft()
    del first["alt_baro"]
    assert engine.poll([first]) == []
    assert engine.poll([aircraft(alt_baro=3000)]) == []


def test_cooldown_blocks_then_expires() -> None:
    config = make_config("[engine]\ncooldown = 60\nbaseline = false\n" + EMERGENCY_RULE)
    clock = Clock(1000.0)
    engine = AlertEngine(config, now=clock)
    assert len(engine.poll([aircraft(emergency="major")])) == 1
    clock.now = 1030.0
    assert engine.poll([aircraft(emergency="major")]) == []
    clock.now = 1059.0
    assert engine.poll([aircraft(emergency="major")]) == []
    clock.now = 1060.0
    assert len(engine.poll([aircraft(emergency="major")])) == 1


def test_cooldown_is_tracked_per_aircraft() -> None:
    config = make_config("[engine]\ncooldown = 600\nbaseline = false\n" + EMERGENCY_RULE)
    engine = AlertEngine(config, now=Clock())
    assert len(engine.poll([aircraft(emergency="major")])) == 1
    assert engine.poll([aircraft(emergency="major")]) == []
    second = engine.poll([aircraft(hex="deadbe", emergency="major")])
    assert [f.alert.hex for f in second] == ["deadbe"]


def test_rules_have_independent_cooldowns() -> None:
    text = (
        "[engine]\nbaseline = false\n"
        '[[rules]]\nname = "slow"\ncooldown = 60\n'
        "[rules.match]\nemergency = true\n"
        '[[rules.sinks]]\ntype = "shell"\ncommand = ["x"]\n'
        '[[rules]]\nname = "fast"\ncooldown = 0\n'
        "[rules.match]\nemergency = true\n"
        '[[rules.sinks]]\ntype = "shell"\ncommand = ["x"]\n'
    )
    engine = AlertEngine(make_config(text), now=Clock())
    first = engine.poll([aircraft(emergency="major")])
    assert sorted(f.rule.name for f in first) == ["fast", "slow"]
    second = engine.poll([aircraft(emergency="major")])
    assert [f.rule.name for f in second] == ["fast"]


def test_entries_without_hex_are_skipped() -> None:
    engine = AlertEngine(make_config("[engine]\nbaseline = false\n" + NEW_RULE), now=Clock())
    assert engine.poll([{}, {"flight": "BAW117"}, {"hex": ""}]) == []


def test_ground_rule_matches_alt_ground() -> None:
    config = make_config("[engine]\nbaseline = false\n" + GROUND_RULE)
    engine = AlertEngine(config, now=Clock())
    assert len(engine.poll([aircraft(alt_baro="ground")])) == 1
    assert engine.poll([aircraft(alt_baro="ground")]) == []


def test_alert_payload_contents() -> None:
    config = make_config("[engine]\nbaseline = false\n" + EMERGENCY_RULE)
    clock = Clock(1234.5)
    engine = AlertEngine(config, now=clock)
    entry = aircraft(emergency="major")
    firing = engine.poll([entry])[0]
    alert = firing.alert
    assert alert.rule == "emergency"
    assert alert.description == "emergency state"
    assert alert.hex == "abc123"
    assert alert.ts == 1234.5
    assert alert.aircraft is entry
    assert alert.message.startswith("[emergency] ABC123 BAW117")
    assert "(emergency state)" in alert.message


def test_counters_track_polls_and_alerts() -> None:
    config = make_config("[engine]\nbaseline = false\ncooldown = 0\n" + EMERGENCY_RULE)
    engine = AlertEngine(config, now=Clock())
    engine.poll([aircraft(emergency="major"), aircraft(hex="deadbe", emergency="major")])
    engine.poll([aircraft(emergency="major")])
    assert engine.polls == 2
    assert engine.alerts == 3
