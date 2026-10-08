"""Notification sinks: webhook, ntfy, Discord, Telegram, email and shell.

Every sink is pure standard library (``urllib.request``, ``smtplib``,
``subprocess``). Builders are separated from transport so payload
serialisation can be asserted in tests without touching the network, and the
transport hooks live on :class:`SinkRuntime` so tests can substitute fakes.

Delivery never raises out of :func:`dispatch`: each action is attempted in
turn, failures are collected as strings, and the remaining sinks still run.
"""

from __future__ import annotations

import json
import os
import smtplib
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from dump1090_alerts.config import Action
from dump1090_alerts.models import Alert

__all__ = [
    "SinkError",
    "SinkRuntime",
    "deliver",
    "discord_request",
    "dispatch",
    "email_message",
    "ntfy_request",
    "telegram_request",
    "webhook_request",
]


class SinkError(RuntimeError):
    """One sink failed to deliver one alert; other sinks are unaffected."""


Opener = Callable[..., Any]
SmtpFactory = Callable[..., Any]
Runner = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class SinkRuntime:
    """Injectable transport hooks. Defaults are the stdlib implementations."""

    opener: Opener = urllib.request.urlopen
    smtp: SmtpFactory = smtplib.SMTP
    runner: Runner = subprocess.run
    timeout: float = 10.0


def _json_body(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _post(runtime: SinkRuntime, request: urllib.request.Request) -> None:
    try:
        with runtime.opener(request, timeout=runtime.timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise SinkError(f"HTTP {exc.code} from {request.full_url}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SinkError(f"cannot reach {request.full_url}: {exc}") from exc
    if status is not None and int(status) >= 400:
        raise SinkError(f"HTTP {status} from {request.full_url}: {body[:200]!r}")


def webhook_request(alert: Alert, options: Mapping[str, Any]) -> urllib.request.Request:
    """Generic JSON POST: the full alert payload plus any custom headers."""
    headers = {"Content-Type": "application/json"}
    headers.update(options.get("headers") or {})
    return urllib.request.Request(
        str(options["url"]),
        data=_json_body(alert.to_dict()),
        headers=headers,
        method="POST",
    )


def ntfy_request(alert: Alert, options: Mapping[str, Any]) -> urllib.request.Request:
    """ntfy.sh-style publish: message body, metadata in headers."""
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": str(options.get("title") or alert.rule),
    }
    if options.get("priority") is not None:
        headers["Priority"] = str(options["priority"])
    tags = options.get("tags")
    if tags:
        headers["Tags"] = ",".join(str(tag) for tag in tags)
    if options.get("token"):
        headers["Authorization"] = f"Bearer {options['token']}"
    return urllib.request.Request(
        str(options["url"]),
        data=alert.message.encode("utf-8"),
        headers=headers,
        method="POST",
    )


def discord_request(alert: Alert, options: Mapping[str, Any]) -> urllib.request.Request:
    """Discord webhook payload: content plus optional username override."""
    payload: dict[str, Any] = {"content": alert.message}
    if options.get("username"):
        payload["username"] = str(options["username"])
    return urllib.request.Request(
        str(options["url"]),
        data=_json_body(payload),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def telegram_request(alert: Alert, options: Mapping[str, Any]) -> urllib.request.Request:
    """Telegram Bot API ``sendMessage`` call built from bot token + chat id."""
    token = str(options["bot_token"]).strip()
    payload = {"chat_id": str(options["chat_id"]), "text": alert.message}
    return urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=_json_body(payload),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def email_message(alert: Alert, options: Mapping[str, Any]) -> EmailMessage:
    """RFC 5322 message: human-readable line first, alert JSON after it."""
    message = EmailMessage()
    message["Subject"] = str(options.get("subject") or "dump1090 alert")
    message["From"] = str(options["from"])
    recipients = [str(address) for address in options["to"]]
    message["To"] = ", ".join(recipients)
    body = f"{alert.message}\n\n{json.dumps(alert.to_dict(), indent=2, sort_keys=True)}\n"
    message.set_content(body)
    return message


def _send_email(alert: Alert, options: Mapping[str, Any], runtime: SinkRuntime) -> None:
    message = email_message(alert, options)
    host = str(options["host"])
    port = int(options.get("port", 587))
    try:
        client = runtime.smtp(host, port, timeout=runtime.timeout)
        with client:
            if options.get("starttls"):
                client.starttls()
            user = options.get("user")
            if user:
                client.login(str(user), str(options.get("password", "")))
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise SinkError(f"email via {host}:{port} failed: {exc}") from exc


def _run_shell(alert: Alert, options: Mapping[str, Any], runtime: SinkRuntime) -> None:
    command: Any = options["command"]
    env = dict(os.environ)
    env.update(
        {
            "DUMP1090_ALERT_RULE": alert.rule,
            "DUMP1090_ALERT_HEX": alert.hex,
            "DUMP1090_ALERT_MESSAGE": alert.message,
        }
    )
    timeout = float(options.get("timeout", 10.0))
    try:
        result = runtime.runner(
            command,
            input=alert.message.encode("utf-8"),
            env=env,
            timeout=timeout,
            capture_output=True,
            shell=isinstance(command, str),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SinkError(f"shell command failed to run: {exc}") from exc
    returncode = int(getattr(result, "returncode", 0))
    if returncode != 0:
        stderr = getattr(result, "stderr", b"") or b""
        if isinstance(stderr, bytes):
            detail = stderr.decode("utf-8", "replace").strip()
        else:
            detail = str(stderr)
        raise SinkError(f"shell command exited {returncode}: {detail}")


def deliver(alert: Alert, action: Action, *, runtime: SinkRuntime | None = None) -> None:
    """Deliver one alert to one sink.

    Raises:
        SinkError: The sink rejected the alert or could not be reached.
    """
    rt = runtime if runtime is not None else SinkRuntime()
    options = action.options
    if action.type == "webhook":
        _post(rt, webhook_request(alert, options))
    elif action.type == "ntfy":
        _post(rt, ntfy_request(alert, options))
    elif action.type == "discord":
        _post(rt, discord_request(alert, options))
    elif action.type == "telegram":
        _post(rt, telegram_request(alert, options))
    elif action.type == "email":
        _send_email(alert, options, rt)
    elif action.type == "shell":
        _run_shell(alert, options, rt)
    else:
        raise SinkError(f"unknown sink type {action.type!r}")


def dispatch(
    alert: Alert,
    actions: Sequence[Action],
    *,
    runtime: SinkRuntime | None = None,
) -> list[str]:
    """Deliver an alert to every action, isolating failures.

    Returns one message per failed sink (empty on full success). Exceptions
    of any kind from one sink are caught so the remaining sinks always get
    their chance to run.
    """
    errors: list[str] = []
    for action in actions:
        try:
            deliver(alert, action, runtime=runtime)
        except Exception as exc:
            errors.append(f"{action.type}: {exc}")
    return errors
