"""Aircraft field helpers, the alert payload, and distance maths.

The shapes here mirror the ``aircraft.json`` document published by both
dump1090-fa and readsb (see the repository README for the field list). Every
field is optional on the wire except ``hex``, so all accessors are defensive
and distinguish "unknown" from a real value.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: One entry of the ``aircraft`` array in ``aircraft.json``.
Aircraft = Mapping[str, Any]

#: Mean Earth radius (6371 km) expressed in nautical miles.
EARTH_RADIUS_NM = 3440.065


def as_number(value: object) -> float | None:
    """Coerce a JSON number to ``float``; ``None`` for anything else.

    ``bool`` is deliberately rejected even though it subclasses ``int``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def callsign(aircraft: Aircraft) -> str:
    """Trimmed callsign from the ``flight`` field, or ``""`` when unusable."""
    value = aircraft.get("flight")
    return value.strip() if isinstance(value, str) else ""


def altitude_ft(aircraft: Aircraft) -> float | None:
    """``alt_baro`` in feet, treating the literal string ``"ground"`` as 0 ft.

    Returns ``None`` when the field is missing or not a number, so callers can
    tell "unknown altitude" apart from "on the ground".
    """
    if aircraft.get("alt_baro") == "ground":
        return 0.0
    return as_number(aircraft.get("alt_baro"))


def is_on_ground(aircraft: Aircraft) -> bool | None:
    """``True``/``False`` from ``alt_baro``; ``None`` when altitude is unknown."""
    if aircraft.get("alt_baro") == "ground":
        return True
    return False if altitude_ft(aircraft) is not None else None


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points, in nautical miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(min(1.0, a)))


def describe_aircraft(aircraft: Aircraft) -> str:
    """One-line human summary of an aircraft, used inside notifications."""
    bits: list[str] = [str(aircraft.get("hex", "?")).upper()]
    cs = callsign(aircraft)
    if cs:
        bits.append(cs)
    if aircraft.get("alt_baro") == "ground":
        bits.append("on ground")
    else:
        alt = altitude_ft(aircraft)
        if alt is not None:
            bits.append(f"{alt:,.0f} ft")
    squawk = aircraft.get("squawk")
    if isinstance(squawk, str) and squawk:
        bits.append(f"squawk {squawk}")
    emergency = aircraft.get("emergency")
    if isinstance(emergency, str) and emergency != "none":
        bits.append(f"emergency={emergency}")
    rssi = as_number(aircraft.get("rssi"))
    if rssi is not None:
        bits.append(f"rssi {rssi:g} dBFS")
    lat = as_number(aircraft.get("lat"))
    lon = as_number(aircraft.get("lon"))
    if lat is not None and lon is not None:
        bits.append(f"pos {lat:.4f},{lon:.4f}")
    return " ".join(bits)


@dataclass(frozen=True, slots=True)
class Alert:
    """One fired notification: which rule matched which aircraft, and why."""

    rule: str
    description: str
    message: str
    hex: str
    ts: float
    aircraft: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable payload handed to every sink."""
        return {
            "rule": self.rule,
            "description": self.description,
            "message": self.message,
            "hex": self.hex,
            "ts": self.ts,
            "aircraft": dict(self.aircraft),
        }
