"""Condition matching: does one rule apply to one aircraft on this poll?

A :class:`Match` is the parsed form of a ``[rules.match]`` table; every
condition that is configured must hold (conditions are ANDed). Transition
conditions (``new``, ``takeoff``, ``landing``) need per-poll state, which the
caller supplies through an :class:`EvalContext`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from re import Pattern

from dump1090_alerts.models import (
    Aircraft,
    altitude_ft,
    as_number,
    callsign,
    haversine_nm,
    is_on_ground,
)


@dataclass(frozen=True, slots=True)
class Near:
    """A circular geofence: everything within ``radius_nm`` of lat/lon."""

    lat: float
    lon: float
    radius_nm: float


@dataclass(frozen=True, slots=True)
class Match:
    """All conditions of a single rule, in evaluated form."""

    hexes: frozenset[str] = field(default_factory=frozenset)
    callsign_prefix: str | None = None
    callsign_pattern: Pattern[str] | None = None
    alt_above: float | None = None
    alt_below: float | None = None
    near: Near | None = None
    squawks: frozenset[str] = field(default_factory=frozenset)
    emergency: bool | None = None
    category_prefix: str | None = None
    rssi_min: float | None = None
    on_ground: bool | None = None
    new: bool = False
    takeoff: bool = False
    landing: bool = False

    @property
    def has_conditions(self) -> bool:
        """True when at least one condition is configured."""
        return any(
            (
                self.hexes,
                self.callsign_prefix is not None,
                self.callsign_pattern is not None,
                self.alt_above is not None,
                self.alt_below is not None,
                self.near is not None,
                self.squawks,
                self.emergency is not None,
                self.category_prefix is not None,
                self.rssi_min is not None,
                self.on_ground is not None,
                self.new,
                self.takeoff,
                self.landing,
            )
        )


@dataclass(frozen=True, slots=True)
class EvalContext:
    """Per-poll state that transition conditions need.

    Args:
        is_new: The aircraft hex was not observed before this poll.
        was_ground: Ground state on the previous poll (``None`` when the
            aircraft is new or its altitude was unknown last poll).
    """

    is_new: bool
    was_ground: bool | None


def evaluate(match: Match, aircraft: Aircraft, ctx: EvalContext) -> bool:
    """Return ``True`` when every configured condition on ``match`` holds.

    Unknown/missing aircraft fields never satisfy a condition: an aircraft
    without a position cannot match ``near``, one without ``alt_baro`` cannot
    match altitude or ground conditions, and so on.
    """
    if match.hexes:
        hex_id = aircraft.get("hex")
        if not isinstance(hex_id, str) or hex_id.lower() not in match.hexes:
            return False

    prefix = match.callsign_prefix
    if prefix is not None and not callsign(aircraft).upper().startswith(prefix.upper()):
        return False

    if match.callsign_pattern is not None:
        cs = callsign(aircraft)
        if not cs or match.callsign_pattern.search(cs) is None:
            return False

    if match.alt_above is not None:
        alt = altitude_ft(aircraft)
        if alt is None or alt < match.alt_above:
            return False

    if match.alt_below is not None:
        alt = altitude_ft(aircraft)
        if alt is None or alt > match.alt_below:
            return False

    if match.near is not None:
        lat = as_number(aircraft.get("lat"))
        lon = as_number(aircraft.get("lon"))
        if lat is None or lon is None:
            return False
        if haversine_nm(lat, lon, match.near.lat, match.near.lon) > match.near.radius_nm:
            return False

    if match.squawks:
        squawk = aircraft.get("squawk")
        if not isinstance(squawk, str) or squawk not in match.squawks:
            return False

    if match.emergency is not None:
        value = aircraft.get("emergency")
        state = isinstance(value, str) and value != "none"
        if state != match.emergency:
            return False

    if match.category_prefix is not None:
        category = aircraft.get("category")
        if not isinstance(category, str) or not category.upper().startswith(
            match.category_prefix.upper()
        ):
            return False

    if match.rssi_min is not None:
        rssi = as_number(aircraft.get("rssi"))
        if rssi is None or rssi < match.rssi_min:
            return False

    ground = is_on_ground(aircraft)

    if match.on_ground is not None and (ground is None or ground is not match.on_ground):
        return False

    return (
        (not match.new or ctx.is_new)
        and (not match.takeoff or (ctx.was_ground is True and ground is False))
        and (not match.landing or (ctx.was_ground is False and ground is True))
    )
