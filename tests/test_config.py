"""Tests for TOML configuration parsing, validation and its error messages."""

from __future__ import annotations

import textwrap
import tomllib
from pathlib import Path

import pytest

from dump1090_alerts.config import (
    SINK_TYPES,
    Config,
    ConfigError,
    load_config,
    parse_config,
)

MINIMAL = """
    [[rules]]
    name = "watch"

    [rules.match]
    hex = ["ABC123"]

    [[rules.sinks]]
    type = "webhook"
    url = "https://example.com/hook"
    """

DEFAULT_SINK = '[[rules.sinks]]\ntype = "shell"\ncommand = ["x"]\n'


def rule_text(match_body: str = 'hex = ["a"]\n', sinks_body: str = DEFAULT_SINK) -> str:
    return f'[[rules]]\nname = "r"\n[rules.match]\n{match_body}{sinks_body}'


def parse(text: str) -> Config:
    return parse_config(tomllib.loads(textwrap.dedent(text)), source="<test>")


def error_in(text: str) -> str:
    with pytest.raises(ConfigError) as excinfo:
        parse(text)
    return str(excinfo.value)


def test_minimal_config_defaults() -> None:
    config = parse(MINIMAL)
    assert config.engine.cooldown == 60.0
    assert config.engine.baseline is True
    rule = config.rules[0]
    assert rule.name == "watch"
    assert rule.description == ""
    assert rule.cooldown == 60.0
    assert rule.match.hexes == frozenset({"abc123"})
    assert len(rule.actions) == 1
    assert rule.actions[0].type == "webhook"
    assert rule.actions[0].options["url"] == "https://example.com/hook"


def test_full_config_parses_all_engine_and_rule_keys() -> None:
    config = parse(
        """
        [engine]
        cooldown = 120
        baseline = false

        [[rules]]
        name = "emergency"
        description = "squawk trouble"
        cooldown = 30

        [rules.match]
        squawk = ["7500", "7600", "7700"]
        emergency = true
        category_prefix = "a"

        [[rules.sinks]]
        type = "ntfy"
        url = "https://ntfy.sh/adsb"
        token = "sekrit"
        priority = 5
        tags = ["rotating_light"]
        title = "ADS-B"
        """
    )
    assert config.engine.cooldown == 120.0
    assert config.engine.baseline is False
    rule = config.rules[0]
    assert rule.description == "squawk trouble"
    assert rule.cooldown == 30.0
    assert rule.match.squawks == frozenset({"7500", "7600", "7700"})
    assert rule.match.emergency is True
    assert rule.match.category_prefix == "A"
    options = rule.actions[0].options
    assert options["token"] == "sekrit"
    assert options["priority"] == 5
    assert options["tags"] == ["rotating_light"]
    assert options["title"] == "ADS-B"


def test_load_config_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "alerts.toml"
    path.write_text(MINIMAL, encoding="utf-8")
    config = load_config(path)
    assert config.source == str(path)
    assert config.rules[0].name == "watch"


def test_missing_file_has_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "nope.toml")
    message = str(excinfo.value)
    assert "config file not found" in message
    assert "alerts.toml.example" in message


def test_invalid_toml_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[[rules\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "invalid TOML" in str(excinfo.value)


def test_non_utf8_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "binary.toml"
    path.write_bytes(b"\xff\xfe\x00invalid")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "not valid UTF-8" in str(excinfo.value)


def test_missing_rules_section() -> None:
    assert "missing [[rules]]" in error_in("")


def test_rules_not_a_list() -> None:
    assert "missing [[rules]]" in error_in("rules = {}\n")


def test_empty_rules() -> None:
    assert "[[rules]] is empty" in error_in("rules = []\n")


def test_unknown_top_level_key() -> None:
    assert "unknown top-level key(s): bogus" in error_in("bogus = 1\n" + MINIMAL)


def test_engine_must_be_a_table() -> None:
    assert "[engine] must be a table, got int" in error_in("engine = 3\n" + MINIMAL)


def test_unknown_engine_key() -> None:
    assert "unknown key(s) in [engine]: interval" in error_in("[engine]\ninterval = 1\n" + MINIMAL)


def test_negative_engine_cooldown() -> None:
    assert "[engine].cooldown must be >= 0" in error_in("[engine]\ncooldown = -5\n" + MINIMAL)


def test_engine_baseline_wrong_type() -> None:
    assert "[engine].baseline must be a boolean, got int" in error_in(
        "[engine]\nbaseline = 1\n" + MINIMAL
    )


def test_rule_must_be_a_table() -> None:
    assert "rules[1] must be a table, got str" in error_in('rules = ["nope"]\n')


def test_rule_missing_name() -> None:
    assert "rules[1] is missing required key 'name'" in error_in(
        '[[rules]]\n[rules.match]\nhex = ["abc123"]\n'
    )


def test_rule_name_wrong_type() -> None:
    assert "rules[1].name must be a string, got int" in error_in("[[rules]]\nname = 7\n")


def test_rule_empty_name() -> None:
    assert "rules[1].name must not be empty" in error_in('[[rules]]\nname = "  "\n')


def test_rule_missing_match() -> None:
    assert "missing a [rules.match] table" in error_in('[[rules]]\nname = "r"\n')


def test_rule_missing_sinks() -> None:
    assert "needs at least one [[rules.sinks]]" in error_in(
        '[[rules]]\nname = "r"\n[rules.match]\nhex = ["a"]\n'
    )


def test_rule_unknown_key() -> None:
    assert "unknown key(s) in rules[1]: bogus" in error_in(
        '[[rules]]\nname = "r"\nbogus = 1\n[rules.match]\nhex = ["a"]\n'
    )


def test_duplicate_rule_names() -> None:
    assert "duplicate rule name 'watch'" in error_in(MINIMAL + MINIMAL)


def test_match_wrong_type() -> None:
    assert "rule 'r'.match must be a table, got str" in error_in(
        '[[rules]]\nname = "r"\nmatch = "nope"\n'
    )


def test_unknown_match_key_lists_valid_keys() -> None:
    assert "unknown key(s) in rule 'r'.match: altitude" in error_in(rule_text("altitude = 100\n"))


def test_empty_match_is_rejected() -> None:
    assert "defines no conditions" in error_in(rule_text(""))


def test_hex_must_be_array_of_strings() -> None:
    assert "hex must be an array of strings, got str" in error_in(rule_text('hex = "abc123"\n'))


def test_hex_array_entries_must_be_strings() -> None:
    assert "hex[1] must be a string, got int" in error_in(rule_text('hex = ["a", 2]\n'))


def test_invalid_squawk() -> None:
    assert "not a Mode A code" in error_in(rule_text('squawk = ["8888"]\n'))


def test_invalid_callsign_regex() -> None:
    assert "not a valid regex" in error_in(rule_text('callsign_regex = "[("\n'))


def test_callsign_prefix_empty() -> None:
    assert "callsign_prefix must not be empty" in error_in(rule_text('callsign_prefix = " "\n'))


def test_alt_above_wrong_type() -> None:
    assert "alt_above must be a number, got str" in error_in(rule_text('alt_above = "high"\n'))


def test_emergency_wrong_type() -> None:
    assert "emergency must be a boolean, got str" in error_in(rule_text('emergency = "yes"\n'))


def test_category_prefix_invalid() -> None:
    assert "not an emitter category prefix" in error_in(rule_text('category_prefix = "XY"\n'))


def test_transition_cannot_be_negated() -> None:
    assert "transition conditions cannot be negated" in error_in(
        rule_text('hex = ["a"]\ntakeoff = false\n')
    )


def test_on_ground_may_be_false() -> None:
    config = parse(rule_text("on_ground = false\n"))
    assert config.rules[0].match.on_ground is False


def test_near_missing_keys() -> None:
    assert "rule 'r'.match.near is missing key(s): radius_nm" in error_in(
        '[[rules]]\nname = "r"\n[rules.match.near]\nlat = 1.0\nlon = 2.0\n'
    )


def test_near_lat_out_of_range() -> None:
    assert "lat must be between -90 and 90" in error_in(
        '[[rules]]\nname = "r"\n[rules.match.near]\nlat = 95.0\nlon = 2.0\nradius_nm = 1.0\n'
    )


def test_near_lon_out_of_range() -> None:
    assert "lon must be between -180 and 180" in error_in(
        '[[rules]]\nname = "r"\n[rules.match.near]\nlat = 1.0\nlon = -181.0\nradius_nm = 1.0\n'
    )


def test_near_radius_must_be_positive() -> None:
    assert "radius_nm must be > 0" in error_in(
        '[[rules]]\nname = "r"\n[rules.match.near]\nlat = 1.0\nlon = 2.0\nradius_nm = 0.0\n'
    )


def test_near_unknown_key() -> None:
    assert "unknown key(s) in rule 'r'.match.near: altitude" in error_in(
        '[[rules]]\nname = "r"\n[rules.match.near]\n'
        "lat = 1.0\nlon = 2.0\nradius_nm = 1.0\naltitude = 5\n"
    )


def test_sink_type_missing() -> None:
    assert "missing required key 'type'" in error_in(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\nurl = "http://x"\n')
    )


def test_unknown_sink_type() -> None:
    assert "unknown sink type 'carrier-pigeon'" in error_in(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\ntype = "carrier-pigeon"\n')
    )


def test_sink_missing_required_option() -> None:
    assert "missing required option(s): url" in error_in(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\ntype = "ntfy"\n')
    )


def test_sink_unknown_option() -> None:
    assert "unknown key(s)" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "webhook"\nurl = "http://x"\nwat = 1\n',
        )
    )


def test_webhook_url_scheme_enforced() -> None:
    assert "must start with http:// or https://" in error_in(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\ntype = "webhook"\nurl = "ftp://x"\n')
    )


def test_webhook_headers_must_be_strings() -> None:
    assert "headers.X-Count must be a string, got int" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "webhook"\nurl = "http://x"\n'
            "[rules.sinks.headers]\nX-Count = 42\n",
        )
    )


def test_webhook_headers_default_to_empty() -> None:
    config = parse(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\ntype = "webhook"\nurl = "http://x"\n')
    )
    assert config.rules[0].actions[0].options["headers"] == {}


def test_ntfy_priority_range() -> None:
    assert "priority must be an integer 1-5" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "ntfy"\nurl = "https://ntfy.sh/x"\npriority = 9\n',
        )
    )


def test_telegram_chat_id_accepts_int_and_stores_string() -> None:
    config = parse(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "telegram"\nbot_token = "123:abc"\nchat_id = 4242\n',
        )
    )
    assert config.rules[0].actions[0].options["chat_id"] == "4242"


def test_telegram_chat_id_rejects_float() -> None:
    assert "chat_id must be a string or integer" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "telegram"\nbot_token = "123:abc"\nchat_id = 1.5\n',
        )
    )


def test_telegram_empty_bot_token() -> None:
    assert "bot_token must not be empty" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "telegram"\nbot_token = " "\nchat_id = "1"\n',
        )
    )


def test_email_defaults_are_applied() -> None:
    config = parse(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "email"\nhost = "localhost"\n'
            'from = "adsb@example.com"\nto = ["ops@example.com"]\n',
        )
    )
    options = config.rules[0].actions[0].options
    assert options["port"] == 587
    assert options["starttls"] is True
    assert options["subject"] == "dump1090 alert"


def test_email_rejects_bad_port() -> None:
    assert "port must be an integer 1-65535" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "email"\nhost = "h"\nfrom = "a@b"\n'
            'to = ["c@d"]\nport = 70000\n',
        )
    )


def test_email_rejects_fractional_port() -> None:
    assert "port must be an integer 1-65535" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "email"\nhost = "h"\nfrom = "a@b"\nto = ["c@d"]\nport = 1.5\n',
        )
    )


def test_email_rejects_empty_recipients() -> None:
    assert "to must contain at least one address" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "email"\nhost = "h"\nfrom = "a@b"\nto = []\n',
        )
    )


def test_email_rejects_non_list_recipients() -> None:
    assert "to must be an array of strings" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "email"\nhost = "h"\nfrom = "a@b"\nto = "c@d"\n',
        )
    )


def test_shell_accepts_string_command() -> None:
    config = parse(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\ntype = "shell"\ncommand = "notify me"\n')
    )
    options = config.rules[0].actions[0].options
    assert options["command"] == "notify me"
    assert options["timeout"] == 10.0


def test_shell_accepts_list_command_with_timeout() -> None:
    config = parse(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "shell"\ncommand = ["/bin/notify", "--once"]\ntimeout = 3.0\n',
        )
    )
    assert config.rules[0].actions[0].options["command"] == ["/bin/notify", "--once"]


def test_shell_rejects_empty_command() -> None:
    assert "command must not be empty" in error_in(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\ntype = "shell"\ncommand = "  "\n')
    )


def test_shell_rejects_empty_list_command() -> None:
    assert "command must not be empty" in error_in(
        rule_text('hex = ["a"]\n', '[[rules.sinks]]\ntype = "shell"\ncommand = []\n')
    )


def test_shell_rejects_bad_timeout() -> None:
    assert "timeout must be > 0" in error_in(
        rule_text(
            'hex = ["a"]\n',
            '[[rules.sinks]]\ntype = "shell"\ncommand = ["x"]\ntimeout = 0\n',
        )
    )


def test_sink_type_wrong_value() -> None:
    assert "type must be a string, got int" in error_in(
        rule_text('hex = ["a"]\n', "[[rules.sinks]]\ntype = 5\n")
    )


def test_rule_cooldown_wrong_type() -> None:
    assert "rule 'r'.cooldown must be a number, got str" in error_in(
        '[[rules]]\nname = "r"\ncooldown = "fast"\n'
    )


def test_rule_cooldown_overrides_engine() -> None:
    config = parse(
        '[engine]\ncooldown = 120\n[[rules]]\nname = "r"\ncooldown = 5\n'
        '[rules.match]\nhex = ["a"]\n' + DEFAULT_SINK
    )
    assert config.rules[0].cooldown == 5.0


def test_rule_negative_cooldown() -> None:
    assert "rule 'r'.cooldown must be >= 0" in error_in('[[rules]]\nname = "r"\ncooldown = -1\n')


def test_action_missing_required_key() -> None:
    assert "missing required key 'type'" in error_in(
        rule_text('hex = ["a"]\n', "[[rules.sinks]]\n")
    )


def test_sink_types_are_the_documented_six() -> None:
    assert frozenset({"webhook", "ntfy", "discord", "telegram", "email", "shell"}) == SINK_TYPES
