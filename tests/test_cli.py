"""Tests for the command-line interface: flags, URL resolution, exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dump1090_alerts.cli import main
from dump1090_alerts.fetch import FetchError
from dump1090_alerts.sinks import SinkRuntime

CONFIG = """
[[rules]]
name = "emergency"
description = "emergency state"

[rules.match]
emergency = true

[[rules.sinks]]
type = "webhook"
url = "https://hooks.example/alerts"
"""

EMERGENCY_AIRCRAFT: dict[str, Any] = {
    "now": 1.0,
    "messages": 10,
    "aircraft": [{"hex": "abc123", "flight": "BAW117  ", "alt_baro": 34000, "emergency": "major"}],
}

CALM_AIRCRAFT: dict[str, Any] = {
    "now": 1.0,
    "messages": 10,
    "aircraft": [{"hex": "abc123", "flight": "BAW117  ", "alt_baro": 34000, "emergency": "none"}],
}


def write_config(tmp_path: Path, text: str = CONFIG) -> Path:
    path = tmp_path / "alerts.toml"
    path.write_text(text, encoding="utf-8")
    return path


class FetchStub:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float = 0.0) -> list[dict[str, Any]]:
        self.calls.append((url, timeout))
        if isinstance(self.payload, Exception):
            raise self.payload
        return list(self.payload["aircraft"])


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"ok"

    def getcode(self) -> int:
        return self.status


class Recorder:
    def __init__(self, *, status: int = 200, error: Exception | None = None) -> None:
        self.requests: list[Any] = []
        self.status = status
        self.error = error

    def __call__(self, request: Any, timeout: float = 0.0, **_kwargs: Any) -> FakeResponse:
        if self.error is not None:
            raise self.error
        self.requests.append(request)
        return FakeResponse(self.status)


def test_once_dry_run_prints_without_delivering(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    recorder = Recorder()
    exit_code = main(
        ["--config", str(config), "--once", "--dry-run"],
        fetch=FetchStub(EMERGENCY_AIRCRAFT),
        runtime=SinkRuntime(opener=recorder),
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "would fire emergency: [emergency] ABC123" in captured.out
    assert recorder.requests == []
    assert "1 rule(s), dry-run, once" in captured.err


def test_once_delivers_alert_to_webhook(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    recorder = Recorder()
    exit_code = main(
        ["--config", str(config), "--once"],
        fetch=FetchStub(EMERGENCY_AIRCRAFT),
        runtime=SinkRuntime(opener=recorder),
    )
    capsys.readouterr()
    assert exit_code == 0
    assert len(recorder.requests) == 1
    payload = json.loads(recorder.requests[0].data)
    assert payload["rule"] == "emergency"
    assert payload["hex"] == "abc123"
    assert payload["description"] == "emergency state"


def test_sink_failure_is_logged_but_exit_is_zero(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    recorder = Recorder(status=500)
    exit_code = main(
        ["--config", str(config), "--once"],
        fetch=FetchStub(EMERGENCY_AIRCRAFT),
        runtime=SinkRuntime(opener=recorder),
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "error: sink webhook: HTTP 500" in captured.err


def test_non_matching_aircraft_fires_nothing(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    exit_code = main(
        ["--config", str(config), "--once", "--dry-run"],
        fetch=FetchStub(CALM_AIRCRAFT),
        runtime=SinkRuntime(opener=Recorder()),
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "would fire" not in captured.out


def test_missing_config_file_exits_one(tmp_path: Path, capsys: Any) -> None:
    exit_code = main(
        ["--config", str(tmp_path / "nope.toml"), "--once"],
        fetch=FetchStub(CALM_AIRCRAFT),
        runtime=SinkRuntime(opener=Recorder()),
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "config file not found" in captured.err
    assert captured.out == ""


def test_invalid_config_toml_exits_one(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "broken.toml"
    path.write_text("[[rules\n", encoding="utf-8")
    exit_code = main(["--config", str(path), "--once"], fetch=FetchStub(CALM_AIRCRAFT))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "invalid TOML" in captured.err


def test_default_config_is_alerts_toml(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    exit_code = main(
        ["--once"],
        fetch=FetchStub(CALM_AIRCRAFT),
        runtime=SinkRuntime(opener=Recorder()),
    )
    capsys.readouterr()
    assert exit_code == 0


def test_missing_default_config_exits_one(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.chdir(tmp_path)
    exit_code = main(["--once"], fetch=FetchStub(CALM_AIRCRAFT))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "alerts.toml" in captured.err


def test_default_url_is_local_decoder(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    fetch = FetchStub(CALM_AIRCRAFT)
    main(["--config", str(config), "--once"], fetch=fetch, runtime=SinkRuntime(opener=Recorder()))
    capsys.readouterr()
    assert fetch.calls[0][0] == "http://localhost:8080/data/aircraft.json"
    assert fetch.calls[0][1] == 5.0


def test_url_flag_overrides_default(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    fetch = FetchStub(CALM_AIRCRAFT)
    main(
        ["--config", str(config), "--once", "--url", "http://decoder:9090"],
        fetch=fetch,
        runtime=SinkRuntime(opener=Recorder()),
    )
    capsys.readouterr()
    assert fetch.calls[0][0] == "http://decoder:9090/data/aircraft.json"


def test_url_flag_accepts_full_document_url(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    fetch = FetchStub(CALM_AIRCRAFT)
    main(
        ["--config", str(config), "--once", "--url", "http://decoder:9090/data/aircraft.json"],
        fetch=fetch,
        runtime=SinkRuntime(opener=Recorder()),
    )
    capsys.readouterr()
    assert fetch.calls[0][0] == "http://decoder:9090/data/aircraft.json"


def test_env_url_is_used_when_flag_absent(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    config = write_config(tmp_path)
    monkeypatch.setenv("DUMP1090_URL", "http://from-env:1234")
    fetch = FetchStub(CALM_AIRCRAFT)
    main(["--config", str(config), "--once"], fetch=fetch, runtime=SinkRuntime(opener=Recorder()))
    capsys.readouterr()
    assert fetch.calls[0][0] == "http://from-env:1234/data/aircraft.json"


def test_url_flag_beats_env_var(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    config = write_config(tmp_path)
    monkeypatch.setenv("DUMP1090_URL", "http://from-env:1234")
    fetch = FetchStub(CALM_AIRCRAFT)
    main(
        ["--config", str(config), "--once", "--url", "http://flag-host:8080"],
        fetch=fetch,
        runtime=SinkRuntime(opener=Recorder()),
    )
    capsys.readouterr()
    assert fetch.calls[0][0] == "http://flag-host:8080/data/aircraft.json"


def test_timeout_flag_is_passed_to_fetch(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    fetch = FetchStub(CALM_AIRCRAFT)
    main(
        ["--config", str(config), "--once", "--timeout", "1.5"],
        fetch=fetch,
        runtime=SinkRuntime(opener=Recorder()),
    )
    capsys.readouterr()
    assert fetch.calls[0][1] == 1.5


def test_once_with_fetch_error_exits_one(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    broken = FetchStub(FetchError("cannot read http://localhost:8080/data/aircraft.json: down"))
    exit_code = main(
        ["--config", str(config), "--once"],
        fetch=broken,
        runtime=SinkRuntime(opener=Recorder()),
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "cannot read" in captured.err


def test_loop_survives_fetch_errors_and_stops_after_max_polls(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    broken = FetchStub(FetchError("decoder is down"))
    sleeps: list[float] = []
    exit_code = main(
        ["--config", str(config), "--max-polls", "2", "--interval", "0.5"],
        fetch=broken,
        runtime=SinkRuntime(opener=Recorder()),
        sleep=sleeps.append,
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "decoder is down" in captured.err
    assert sleeps == [0.5]
    assert len(broken.calls) == 2


def test_loop_sleeps_between_successful_polls(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)
    fetch = FetchStub(CALM_AIRCRAFT)
    sleeps: list[float] = []
    exit_code = main(
        ["--config", str(config), "--max-polls", "3", "--interval", "1.0"],
        fetch=fetch,
        runtime=SinkRuntime(opener=Recorder()),
        sleep=sleeps.append,
    )
    capsys.readouterr()
    assert exit_code == 0
    assert len(fetch.calls) == 3
    assert sleeps == [1.0, 1.0]


def test_keyboard_interrupt_stops_cleanly(tmp_path: Path, capsys: Any) -> None:
    config = write_config(tmp_path)

    def interrupting_fetch(url: str, timeout: float = 0.0) -> list[dict[str, Any]]:
        raise KeyboardInterrupt

    exit_code = main(
        ["--config", str(config), "--max-polls", "5"],
        fetch=interrupting_fetch,
        runtime=SinkRuntime(opener=Recorder()),
        sleep=lambda _seconds: None,
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "# stopped" in captured.err


def test_version_flag_prints_and_exits_zero(capsys: Any) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    captured = capsys.readouterr()
    assert excinfo.value.code == 0
    assert "dump1090-alerts 0.1.0" in captured.out


def test_help_mentions_every_documented_flag(capsys: Any) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    captured = capsys.readouterr()
    assert excinfo.value.code == 0
    for flag in ["--config", "--url", "--once", "--dry-run", "--interval", "--max-polls"]:
        assert flag in captured.out
