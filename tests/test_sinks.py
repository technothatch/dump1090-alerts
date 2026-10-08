"""Tests for sink payload serialisation, transport failures and isolation."""

from __future__ import annotations

import json
import smtplib
import subprocess
import urllib.error
from typing import Any

import pytest

from dump1090_alerts.config import Action
from dump1090_alerts.models import Alert
from dump1090_alerts.sinks import (
    SinkError,
    SinkRuntime,
    deliver,
    discord_request,
    dispatch,
    email_message,
    ntfy_request,
    telegram_request,
    webhook_request,
)


def make_alert() -> Alert:
    return Alert(
        rule="emergency",
        description="emergency state",
        message="[emergency] ABC123 BAW117",
        hex="abc123",
        ts=1700000000.0,
        aircraft={"hex": "abc123", "flight": "BAW117  ", "alt_baro": 34000},
    )


def webhook_action(url: str) -> Action:
    return Action(type="webhook", options={"url": url, "headers": {}})


class FakeResponse:
    def __init__(self, body: bytes = b"ok", status: int = 200, via_getcode: bool = False) -> None:
        self.body = body
        self._status = status
        self.via_getcode = via_getcode
        if not via_getcode:
            self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self._status


class Recorder:
    def __init__(
        self,
        *,
        status: int = 200,
        error: Exception | None = None,
        via_getcode: bool = False,
    ) -> None:
        self.requests: list[Any] = []
        self.status = status
        self.error = error
        self.via_getcode = via_getcode

    def __call__(self, request: Any, timeout: float = 0.0, **_kwargs: Any) -> FakeResponse:
        if self.error is not None:
            raise self.error
        self.requests.append(request)
        return FakeResponse(b"", self.status, via_getcode=self.via_getcode)


class FakeSmtp:
    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.tls = False
        self.login_credentials: tuple[str, str] | None = None
        self.sent: list[Any] = []

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def starttls(self) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.login_credentials = (user, password)

    def send_message(self, message: Any) -> None:
        self.sent.append(message)


def smtp_factory(instances: list[FakeSmtp]) -> Any:
    def factory(host: str, port: int, timeout: float | None = None) -> FakeSmtp:
        client = FakeSmtp(host, port, timeout)
        instances.append(client)
        return client

    return factory


def broken_smtp(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError("no route to host")


class FakeRunner:
    def __init__(self, *, returncode: int = 0, stderr: Any = b"") -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command: Any, **kwargs: Any) -> Any:
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, stdout=b"", stderr=self.stderr)


def test_webhook_payload_and_headers() -> None:
    alert = make_alert()
    request = webhook_request(
        alert, {"url": "https://hooks.example/x", "headers": {"X-Token": "sekrit"}}
    )
    assert request.full_url == "https://hooks.example/x"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == alert.to_dict()
    assert request.get_header("X-token") == "sekrit"
    assert request.get_header("Content-type") == "application/json"


def test_webhook_without_custom_headers() -> None:
    request = webhook_request(make_alert(), {"url": "https://hooks.example/x"})
    assert json.loads(request.data)["rule"] == "emergency"
    assert request.get_header("X-Token") is None


def test_ntfy_headers_and_body() -> None:
    alert = make_alert()
    request = ntfy_request(
        alert,
        {
            "url": "https://ntfy.sh/adsb",
            "title": "Custom",
            "priority": 5,
            "tags": ["rotating_light", "airplane"],
            "token": "tk123",
        },
    )
    assert request.data == alert.message.encode("utf-8")
    assert request.get_header("Title") == "Custom"
    assert request.get_header("Priority") == "5"
    assert request.get_header("Tags") == "rotating_light,airplane"
    assert request.get_header("Authorization") == "Bearer tk123"
    assert request.get_header("Content-type") == "text/plain; charset=utf-8"


def test_ntfy_defaults_title_to_rule_name() -> None:
    request = ntfy_request(make_alert(), {"url": "https://ntfy.sh/adsb"})
    assert request.get_header("Title") == "emergency"
    assert request.get_header("Priority") is None
    assert request.get_header("Tags") is None
    assert request.get_header("Authorization") is None


def test_discord_payload_with_username() -> None:
    request = discord_request(
        make_alert(),
        {"url": "https://discord.com/api/webhooks/1/2", "username": "dump1090"},
    )
    assert json.loads(request.data) == {
        "content": "[emergency] ABC123 BAW117",
        "username": "dump1090",
    }


def test_discord_payload_without_username() -> None:
    request = discord_request(make_alert(), {"url": "https://discord.com/api/webhooks/1/2"})
    assert json.loads(request.data) == {"content": "[emergency] ABC123 BAW117"}


def test_telegram_payload_and_url() -> None:
    request = telegram_request(make_alert(), {"bot_token": "123:abc", "chat_id": 42})
    assert request.full_url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert json.loads(request.data) == {"chat_id": "42", "text": "[emergency] ABC123 BAW117"}
    assert request.get_header("Content-type") == "application/json"


def test_email_message_shape() -> None:
    message = email_message(
        make_alert(),
        {
            "subject": "ADS-B alert",
            "from": "adsb@example.com",
            "to": ["a@example.com", "b@example.com"],
        },
    )
    assert message["Subject"] == "ADS-B alert"
    assert message["From"] == "adsb@example.com"
    assert message["To"] == "a@example.com, b@example.com"
    body = message.get_content()
    assert "[emergency] ABC123 BAW117" in body
    assert '"hex": "abc123"' in body


def test_email_delivery_starts_tls_and_logs_in() -> None:
    instances: list[FakeSmtp] = []
    runtime = SinkRuntime(smtp=smtp_factory(instances), timeout=7.0)
    action = Action(
        type="email",
        options={
            "host": "mail.example.com",
            "port": 2525,
            "from": "adsb@example.com",
            "to": ["ops@example.com"],
            "starttls": True,
            "user": "mailer",
            "password": "hunter2",
            "subject": "dump1090 alert",
        },
    )
    deliver(make_alert(), action, runtime=runtime)
    client = instances[0]
    assert (client.host, client.port) == ("mail.example.com", 2525)
    assert client.timeout == 7.0
    assert client.tls is True
    assert client.login_credentials == ("mailer", "hunter2")
    assert len(client.sent) == 1


def test_email_delivery_without_tls_or_auth() -> None:
    instances: list[FakeSmtp] = []
    runtime = SinkRuntime(smtp=smtp_factory(instances))
    action = Action(
        type="email",
        options={
            "host": "localhost",
            "port": 25,
            "from": "adsb@example.com",
            "to": ["ops@example.com"],
            "starttls": False,
        },
    )
    deliver(make_alert(), action, runtime=runtime)
    client = instances[0]
    assert client.tls is False
    assert client.login_credentials is None
    assert client.sent[0]["Subject"] == "dump1090 alert"


def test_email_connection_failure_raises_sink_error() -> None:
    runtime = SinkRuntime(smtp=broken_smtp)
    action = Action(
        type="email",
        options={
            "host": "mail.example.com",
            "port": 587,
            "from": "a@example.com",
            "to": ["b@example.com"],
        },
    )
    with pytest.raises(SinkError, match=r"email via mail\.example\.com"):
        deliver(make_alert(), action, runtime=runtime)


def test_email_smtp_protocol_failure_raises_sink_error() -> None:
    class RefusingSmtp(FakeSmtp):
        def send_message(self, message: Any) -> Any:
            raise smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"no")})

    def factory(host: str, port: int, timeout: float | None = None) -> FakeSmtp:
        return RefusingSmtp(host, port, timeout)

    action = Action(
        type="email",
        options={
            "host": "mail.example.com",
            "port": 25,
            "from": "a@example.com",
            "to": ["ops@example.com"],
        },
    )
    with pytest.raises(SinkError, match=r"email via mail\.example\.com"):
        deliver(make_alert(), action, runtime=SinkRuntime(smtp=factory))


def test_shell_runner_gets_command_env_and_stdin() -> None:
    runner = FakeRunner()
    runtime = SinkRuntime(runner=runner)
    action = Action(
        type="shell",
        options={"command": ["/bin/notify", "--once"], "timeout": 5.0},
    )
    deliver(make_alert(), action, runtime=runtime)
    command, kwargs = runner.calls[0]
    assert command == ["/bin/notify", "--once"]
    assert kwargs["shell"] is False
    assert kwargs["input"] == b"[emergency] ABC123 BAW117"
    assert kwargs["timeout"] == 5.0
    env = kwargs["env"]
    assert env["DUMP1090_ALERT_RULE"] == "emergency"
    assert env["DUMP1090_ALERT_HEX"] == "abc123"
    assert env["DUMP1090_ALERT_MESSAGE"] == "[emergency] ABC123 BAW117"


def test_shell_string_command_uses_platform_shell() -> None:
    runner = FakeRunner()
    action = Action(type="shell", options={"command": "notify-send hi", "timeout": 3.0})
    deliver(make_alert(), action, runtime=SinkRuntime(runner=runner))
    assert runner.calls[0][1]["shell"] is True


def test_shell_non_zero_exit_raises_with_stderr() -> None:
    runner = FakeRunner(returncode=3, stderr=b"permission denied")
    action = Action(type="shell", options={"command": ["x"]})
    with pytest.raises(SinkError, match="exited 3: permission denied"):
        deliver(make_alert(), action, runtime=SinkRuntime(runner=runner))


def test_shell_non_bytes_stderr_is_stringified() -> None:
    runner = FakeRunner(returncode=1, stderr="weird warning")
    action = Action(type="shell", options={"command": ["x"]})
    with pytest.raises(SinkError, match="exited 1: weird warning"):
        deliver(make_alert(), action, runtime=SinkRuntime(runner=runner))


def test_shell_start_failure_raises_sink_error() -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("command not found")

    action = Action(type="shell", options={"command": ["x"]})
    with pytest.raises(SinkError, match="shell command failed to run"):
        deliver(make_alert(), action, runtime=SinkRuntime(runner=boom))


def test_shell_timeout_raises_sink_error() -> None:
    def slow(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1.0)

    action = Action(type="shell", options={"command": ["x"]})
    with pytest.raises(SinkError, match="shell command failed to run"):
        deliver(make_alert(), action, runtime=SinkRuntime(runner=slow))


def test_deliver_unknown_sink_type() -> None:
    with pytest.raises(SinkError, match="unknown sink type 'carrier-pigeon'"):
        deliver(make_alert(), Action(type="carrier-pigeon", options={}))


def test_post_reports_http_error_status() -> None:
    runtime = SinkRuntime(opener=Recorder(status=500))
    with pytest.raises(SinkError, match="HTTP 500"):
        deliver(make_alert(), webhook_action("https://hooks.example/x"), runtime=runtime)


def test_post_catches_http_error_exception() -> None:
    error = urllib.error.HTTPError("https://hooks.example/x", 404, "nope", None, None)
    runtime = SinkRuntime(opener=Recorder(error=error))
    with pytest.raises(SinkError, match="HTTP 404"):
        deliver(make_alert(), webhook_action("https://hooks.example/x"), runtime=runtime)


def test_post_reports_connection_failure() -> None:
    runtime = SinkRuntime(opener=Recorder(error=urllib.error.URLError("dns failed")))
    with pytest.raises(SinkError, match="cannot reach"):
        deliver(make_alert(), webhook_action("https://hooks.example/x"), runtime=runtime)


def test_post_uses_getcode_when_status_attribute_missing() -> None:
    runtime = SinkRuntime(opener=Recorder(via_getcode=True))
    errors = dispatch(make_alert(), [webhook_action("https://hooks.example/x")], runtime=runtime)
    assert errors == []


def test_dispatch_success_sends_every_action() -> None:
    recorder = Recorder()
    runtime = SinkRuntime(opener=recorder)
    actions = [webhook_action("https://a.example/x"), webhook_action("https://b.example/x")]
    assert dispatch(make_alert(), actions, runtime=runtime) == []
    assert [r.full_url for r in recorder.requests] == [
        "https://a.example/x",
        "https://b.example/x",
    ]


def test_dispatch_isolates_a_failing_sink_from_the_others() -> None:
    failing_opener = Recorder(error=urllib.error.URLError("refused"))
    runner = FakeRunner()
    runtime = SinkRuntime(opener=failing_opener, runner=runner)
    actions = [
        webhook_action("https://a.example/x"),
        Action(type="shell", options={"command": ["x"]}),
        webhook_action("https://b.example/x"),
    ]
    errors = dispatch(make_alert(), actions, runtime=runtime)
    assert len(errors) == 2
    assert errors[0].startswith("webhook: cannot reach")
    assert errors[1].startswith("webhook: cannot reach")
    assert len(runner.calls) == 1


def test_dispatch_continues_after_email_failure() -> None:
    recorder = Recorder()
    runtime = SinkRuntime(opener=recorder, smtp=broken_smtp)
    actions = [
        Action(
            type="email",
            options={
                "host": "mail.example.com",
                "port": 25,
                "from": "a@example.com",
                "to": ["b@example.com"],
            },
        ),
        webhook_action("https://a.example/x"),
    ]
    errors = dispatch(make_alert(), actions, runtime=runtime)
    assert len(errors) == 1
    assert errors[0].startswith("email: email via mail.example.com")
    assert len(recorder.requests) == 1


def test_dispatch_without_actions_is_a_no_op() -> None:
    assert dispatch(make_alert(), [], runtime=SinkRuntime(opener=Recorder())) == []
