"""TOML configuration for dump1090-alerts, with precise, human-readable errors.

The config file (``alerts.toml``) is parsed with :mod:`tomllib` and then
validated key by key: every problem is reported as a :class:`ConfigError`
naming the offending key and what was expected, never as a traceback.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dump1090_alerts.rules import Match, Near

__all__ = [
    "SINK_TYPES",
    "Action",
    "Config",
    "ConfigError",
    "EngineOptions",
    "Rule",
    "load_config",
    "parse_config",
]


class ConfigError(Exception):
    """Any problem reading or validating a configuration file."""


TOP_LEVEL_KEYS = frozenset({"engine", "rules"})
ENGINE_KEYS = frozenset({"cooldown", "baseline"})
RULE_KEYS = frozenset({"name", "description", "cooldown", "match", "sinks"})
MATCH_KEYS = frozenset(
    {
        "hex",
        "callsign_prefix",
        "callsign_regex",
        "alt_above",
        "alt_below",
        "near",
        "squawk",
        "emergency",
        "category_prefix",
        "rssi_min",
        "on_ground",
        "new",
        "takeoff",
        "landing",
    }
)
NEAR_KEYS = frozenset({"lat", "lon", "radius_nm"})
TRANSITION_KEYS = frozenset({"new", "takeoff", "landing"})

#: Sink type -> (required options, optional options).
SINK_SPECS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "webhook": (frozenset({"url"}), frozenset({"headers"})),
    "ntfy": (frozenset({"url"}), frozenset({"token", "priority", "tags", "title"})),
    "discord": (frozenset({"url"}), frozenset({"username"})),
    "telegram": (frozenset({"bot_token", "chat_id"}), frozenset()),
    "email": (
        frozenset({"host", "from", "to"}),
        frozenset({"port", "starttls", "user", "password", "subject"}),
    ),
    "shell": (frozenset({"command"}), frozenset({"timeout"})),
}
SINK_TYPES = frozenset(SINK_SPECS)

SQUAWK_RE = re.compile(r"^[0-7]{1,4}$")
CATEGORY_RE = re.compile(r"^[A-D][0-7]?$")


@dataclass(frozen=True, slots=True)
class EngineOptions:
    """Global settings from the optional ``[engine]`` table."""

    cooldown: float = 60.0
    baseline: bool = True


@dataclass(frozen=True, slots=True)
class Action:
    """One validated sink invocation (``[[rules.sinks]]`` table)."""

    type: str
    options: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Rule:
    """One named rule: conditions plus the sinks to notify on a match."""

    name: str
    description: str
    cooldown: float
    match: Match
    actions: tuple[Action, ...]


@dataclass(frozen=True, slots=True)
class Config:
    """A fully validated configuration."""

    engine: EngineOptions
    rules: tuple[Rule, ...]
    source: str = "<config>"


def load_config(path: str | Path) -> Config:
    """Read and validate a TOML config file.

    Raises:
        ConfigError: file missing/unreadable, invalid TOML, or invalid schema.
    """
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(
            f"config file not found: {file_path} "
            "(copy alerts.toml.example to alerts.toml or pass --config PATH)"
        ) from None
    except OSError as exc:
        raise ConfigError(f"cannot read {file_path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{file_path}: is not valid UTF-8: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file_path}: invalid TOML: {exc}") from exc
    return parse_config(data, source=str(file_path))


def parse_config(data: Mapping[str, Any], *, source: str = "<config>") -> Config:
    """Validate an already-decoded TOML document."""
    unknown = sorted(set(data) - TOP_LEVEL_KEYS)
    if unknown:
        raise _fail(
            source,
            f"unknown top-level key(s): {', '.join(unknown)} (expected 'engine' and '[[rules]]')",
        )
    engine = _parse_engine(data.get("engine"), source)
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        raise _fail(source, "missing [[rules]] section — at least one rule is required")
    if not raw_rules:
        raise _fail(source, "[[rules]] is empty — at least one rule is required")
    rules = tuple(
        _parse_rule(item, index, engine.cooldown, source)
        for index, item in enumerate(raw_rules, start=1)
    )
    first_seen: dict[str, int] = {}
    for index, rule in enumerate(rules, start=1):
        if rule.name in first_seen:
            raise _fail(
                source,
                f"duplicate rule name {rule.name!r} "
                f"(rules[{first_seen[rule.name]}] and rules[{index}])",
            )
        first_seen[rule.name] = index
    return Config(engine=engine, rules=rules, source=source)


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


def _fail(source: str, message: str) -> ConfigError:
    return ConfigError(f"{source}: {message}")


def _kind(value: object) -> str:
    return type(value).__name__


def _reject_unknown(
    table: Mapping[str, Any], allowed: frozenset[str], where: str, source: str
) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise _fail(
            source,
            f"unknown key(s) in {where}: {', '.join(unknown)} "
            f"(valid: {', '.join(sorted(allowed))})",
        )


def _as_table(value: Any, where: str, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(source, f"{where} must be a table, got {_kind(value)}")
    return value


def _string(value: Any, where: str, source: str) -> str:
    if not isinstance(value, str):
        raise _fail(source, f"{where} must be a string, got {_kind(value)}")
    return value


def _boolean(value: Any, where: str, source: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(source, f"{where} must be a boolean, got {_kind(value)}")
    return value


def _number(value: Any, where: str, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(source, f"{where} must be a number, got {_kind(value)}")
    return float(value)


def _string_list(value: Any, where: str, source: str) -> list[str]:
    if not isinstance(value, list):
        raise _fail(source, f"{where} must be an array of strings, got {_kind(value)}")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise _fail(source, f"{where}[{index}] must be a string, got {_kind(item)}")
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def _parse_engine(raw: Any, source: str) -> EngineOptions:
    if raw is None:
        return EngineOptions()
    table = _as_table(raw, "[engine]", source)
    _reject_unknown(table, ENGINE_KEYS, "[engine]", source)
    cooldown = 60.0
    if "cooldown" in table:
        cooldown = _number(table["cooldown"], "[engine].cooldown", source)
        if cooldown < 0:
            raise _fail(source, "[engine].cooldown must be >= 0")
    baseline = True
    if "baseline" in table:
        baseline = _boolean(table["baseline"], "[engine].baseline", source)
    return EngineOptions(cooldown=cooldown, baseline=baseline)


def _parse_rule(raw: Any, index: int, default_cooldown: float, source: str) -> Rule:
    where = f"rules[{index}]"
    table = _as_table(raw, where, source)
    _reject_unknown(table, RULE_KEYS, where, source)
    if "name" not in table:
        raise _fail(source, f"{where} is missing required key 'name'")
    name = _string(table["name"], f"{where}.name", source).strip()
    if not name:
        raise _fail(source, f"{where}.name must not be empty")
    where = f"rule {name!r}"
    description = _string(table.get("description", ""), f"{where}.description", source)
    cooldown = default_cooldown
    if "cooldown" in table:
        cooldown = _number(table["cooldown"], f"{where}.cooldown", source)
        if cooldown < 0:
            raise _fail(source, f"{where}.cooldown must be >= 0")
    if "match" not in table:
        raise _fail(source, f"{where} is missing a [rules.match] table of conditions")
    match = _parse_match(table["match"], f"{where}.match", source)
    raw_sinks = table.get("sinks")
    if not isinstance(raw_sinks, list) or not raw_sinks:
        raise _fail(source, f"{where} needs at least one [[rules.sinks]] action")
    actions = tuple(
        _parse_action(item, f"{where}.sinks[{sink_index}]", source)
        for sink_index, item in enumerate(raw_sinks, start=1)
    )
    return Rule(
        name=name,
        description=description,
        cooldown=cooldown,
        match=match,
        actions=actions,
    )


def _parse_match(raw: Any, where: str, source: str) -> Match:
    table = _as_table(raw, where, source)
    _reject_unknown(table, MATCH_KEYS, where, source)

    hexes: frozenset[str] = frozenset()
    if "hex" in table:
        hexes = frozenset(h.lower() for h in _string_list(table["hex"], f"{where}.hex", source))

    callsign_prefix: str | None = None
    if "callsign_prefix" in table:
        callsign_prefix = _string(
            table["callsign_prefix"], f"{where}.callsign_prefix", source
        ).strip()
        if not callsign_prefix:
            raise _fail(source, f"{where}.callsign_prefix must not be empty")

    callsign_pattern = None
    if "callsign_regex" in table:
        pattern_text = _string(table["callsign_regex"], f"{where}.callsign_regex", source)
        try:
            callsign_pattern = re.compile(pattern_text)
        except re.error as exc:
            raise _fail(
                source, f"{where}.callsign_regex {pattern_text!r} is not a valid regex: {exc}"
            ) from exc

    alt_above: float | None = None
    if "alt_above" in table:
        alt_above = _number(table["alt_above"], f"{where}.alt_above", source)
    alt_below: float | None = None
    if "alt_below" in table:
        alt_below = _number(table["alt_below"], f"{where}.alt_below", source)

    near: Near | None = None
    if "near" in table:
        near = _parse_near(table["near"], f"{where}.near", source)

    squawks: frozenset[str] = frozenset()
    if "squawk" in table:
        entries = _string_list(table["squawk"], f"{where}.squawk", source)
        for entry in entries:
            if not SQUAWK_RE.match(entry):
                raise _fail(
                    source,
                    f"{where}.squawk entry {entry!r} is not a Mode A code "
                    "(1-4 octal digits such as 7700)",
                )
        squawks = frozenset(entries)

    emergency: bool | None = None
    if "emergency" in table:
        emergency = _boolean(table["emergency"], f"{where}.emergency", source)

    category_prefix: str | None = None
    if "category_prefix" in table:
        category_prefix = _string(
            table["category_prefix"], f"{where}.category_prefix", source
        ).upper()
        if not CATEGORY_RE.match(category_prefix):
            raise _fail(
                source,
                f"{where}.category_prefix {category_prefix!r} is not an emitter category "
                "prefix (A0-D7, e.g. 'A' or 'A3')",
            )

    rssi_min: float | None = None
    if "rssi_min" in table:
        rssi_min = _number(table["rssi_min"], f"{where}.rssi_min", source)

    on_ground: bool | None = None
    if "on_ground" in table:
        on_ground = _boolean(table["on_ground"], f"{where}.on_ground", source)

    transitions: dict[str, bool] = {}
    for key in sorted(TRANSITION_KEYS):
        if key in table:
            value = _boolean(table[key], f"{where}.{key}", source)
            if not value:
                raise _fail(
                    source, f"{where}.{key} must be true — transition conditions cannot be negated"
                )
            transitions[key] = value

    match = Match(
        hexes=hexes,
        callsign_prefix=callsign_prefix,
        callsign_pattern=callsign_pattern,
        alt_above=alt_above,
        alt_below=alt_below,
        near=near,
        squawks=squawks,
        emergency=emergency,
        category_prefix=category_prefix,
        rssi_min=rssi_min,
        on_ground=on_ground,
        new=transitions.get("new", False),
        takeoff=transitions.get("takeoff", False),
        landing=transitions.get("landing", False),
    )
    if not match.has_conditions:
        raise _fail(
            source,
            f"{where} defines no conditions — add at least one "
            "(hex, squawk, callsign_prefix, alt_above, near, emergency, ...)",
        )
    return match


def _parse_near(raw: Any, where: str, source: str) -> Near:
    table = _as_table(raw, where, source)
    _reject_unknown(table, NEAR_KEYS, where, source)
    missing = sorted(NEAR_KEYS - set(table))
    if missing:
        raise _fail(source, f"{where} is missing key(s): {', '.join(missing)}")
    lat = _number(table["lat"], f"{where}.lat", source)
    lon = _number(table["lon"], f"{where}.lon", source)
    radius_nm = _number(table["radius_nm"], f"{where}.radius_nm", source)
    if not -90.0 <= lat <= 90.0:
        raise _fail(source, f"{where}.lat must be between -90 and 90, got {lat}")
    if not -180.0 <= lon <= 180.0:
        raise _fail(source, f"{where}.lon must be between -180 and 180, got {lon}")
    if radius_nm <= 0:
        raise _fail(source, f"{where}.radius_nm must be > 0, got {radius_nm}")
    return Near(lat=lat, lon=lon, radius_nm=radius_nm)


def _parse_action(raw: Any, where: str, source: str) -> Action:
    table = _as_table(raw, where, source)
    if "type" not in table:
        raise _fail(source, f"{where} is missing required key 'type'")
    sink_type = _string(table["type"], f"{where}.type", source)
    if sink_type not in SINK_SPECS:
        raise _fail(
            source,
            f"{where}: unknown sink type {sink_type!r} (valid: {', '.join(sorted(SINK_SPECS))})",
        )
    required, optional = SINK_SPECS[sink_type]
    body = {key: value for key, value in table.items() if key != "type"}
    _reject_unknown(body, required | optional, f"{where} ({sink_type})", source)
    missing = sorted(required - set(body))
    if missing:
        raise _fail(
            source,
            f"{where}: sink {sink_type!r} is missing required option(s): {', '.join(missing)}",
        )
    return Action(type=sink_type, options=_coerce_options(sink_type, body, where, source))


def _coerce_options(
    sink_type: str, options: dict[str, Any], where: str, source: str
) -> dict[str, Any]:
    out = dict(options)
    if sink_type in {"webhook", "ntfy", "discord"}:
        url = _string(out["url"], f"{where}.url", source)
        if not url.startswith(("http://", "https://")):
            raise _fail(source, f"{where}.url must start with http:// or https://, got {url!r}")
        out["url"] = url
    if sink_type == "webhook":
        headers: dict[str, str] = {}
        if "headers" in out:
            table = _as_table(out["headers"], f"{where}.headers", source)
            for key, value in table.items():
                if not isinstance(value, str):
                    raise _fail(
                        source,
                        f"{where}.headers.{key} must be a string, got {_kind(value)}",
                    )
                headers[str(key)] = value
        out["headers"] = headers
    if sink_type == "ntfy":
        if "title" in out:
            out["title"] = _string(out["title"], f"{where}.title", source)
        if "token" in out:
            out["token"] = _string(out["token"], f"{where}.token", source)
        if "priority" in out:
            priority = _number(out["priority"], f"{where}.priority", source)
            if not priority.is_integer() or not 1 <= priority <= 5:
                raise _fail(source, f"{where}.priority must be an integer 1-5, got {priority}")
            out["priority"] = int(priority)
        if "tags" in out:
            out["tags"] = _string_list(out["tags"], f"{where}.tags", source)
    if sink_type == "telegram":
        out["bot_token"] = _string(out["bot_token"], f"{where}.bot_token", source).strip()
        if not out["bot_token"]:
            raise _fail(source, f"{where}.bot_token must not be empty")
        chat_id_raw = out["chat_id"]
        if isinstance(chat_id_raw, bool) or not isinstance(chat_id_raw, (str, int)):
            raise _fail(
                source, f"{where}.chat_id must be a string or integer, got {_kind(chat_id_raw)}"
            )
        chat_id = str(chat_id_raw).strip()
        if not chat_id:
            raise _fail(source, f"{where}.chat_id must not be empty")
        out["chat_id"] = chat_id
    if sink_type == "email":
        out["host"] = _string(out["host"], f"{where}.host", source)
        out["from"] = _string(out["from"], f"{where}.from", source)
        recipients = _string_list(out["to"], f"{where}.to", source)
        if not recipients:
            raise _fail(source, f"{where}.to must contain at least one address")
        out["to"] = recipients
        out.setdefault("port", 587)
        port = _number(out["port"], f"{where}.port", source)
        if not port.is_integer() or not 1 <= port <= 65535:
            raise _fail(source, f"{where}.port must be an integer 1-65535, got {port}")
        out["port"] = int(port)
        out.setdefault("starttls", True)
        out["starttls"] = _boolean(out["starttls"], f"{where}.starttls", source)
        out.setdefault("subject", "dump1090 alert")
        out["subject"] = _string(out["subject"], f"{where}.subject", source)
        if "user" in out:
            out["user"] = _string(out["user"], f"{where}.user", source)
        if "password" in out:
            out["password"] = _string(out["password"], f"{where}.password", source)
    if sink_type == "shell":
        command = out["command"]
        if isinstance(command, str):
            if not command.strip():
                raise _fail(source, f"{where}.command must not be empty")
        else:
            entries = _string_list(command, f"{where}.command", source)
            if not entries:
                raise _fail(source, f"{where}.command must not be empty")
            out["command"] = entries
        out.setdefault("timeout", 10.0)
        timeout = _number(out["timeout"], f"{where}.timeout", source)
        if timeout <= 0:
            raise _fail(source, f"{where}.timeout must be > 0, got {timeout}")
        out["timeout"] = timeout
    return out
