# dump1090-alerts

[![CI](https://github.com/technothatch/dump1090-alerts/actions/workflows/ci.yml/badge.svg)](https://github.com/technothatch/dump1090-alerts/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/technothatch/dump1090-alerts/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/technothatch/dump1090-alerts)](https://github.com/technothatch/dump1090-alerts/releases)
[![Issues](https://img.shields.io/github/issues/technothatch/dump1090-alerts)](https://github.com/technothatch/dump1090-alerts/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/technothatch/dump1090-alerts/pulls)
[![Code size](https://img.shields.io/github/languages/code-size/technothatch/dump1090-alerts)](https://github.com/technothatch/dump1090-alerts)
[![Last commit](https://img.shields.io/github/last-commit/technothatch/dump1090-alerts)](https://github.com/technothatch/dump1090-alerts/commits/main)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-97%25-brightgreen.svg)](https://github.com/technothatch/dump1090-alerts)

A rule engine that watches the `aircraft.json` published by
[dump1090](https://github.com/flightaware/dump1090) and
[readsb](https://github.com/wiedehopf/readsb) and fires notifications when the
traffic matches conditions you care about: an emergency squawk, a callsign
prefix, anything above FL300 inside a radius, a new ICAO hex, a takeoff. Rules
live in a plain TOML file, notifications go out through webhooks, ntfy,
Discord, Telegram, e-mail or a shell command — and the whole thing is a single
stdlib-only Python process you can run under systemd, Docker or cron.

## Features

- **13 condition types**, ANDed inside a rule: `hex` list, callsign prefix,
  callsign regex, `alt_above` / `alt_below` (with `"ground"` counted as 0 ft),
  within `near {lat, lon, radius_nm}` (haversine), `squawk` list,
  `emergency != "none"`, emitter `category_prefix` (`A`–`D7`),
  `rssi_min`, `on_ground`, and the `new` / `takeoff` / `landing` transitions.
- **Six sinks, all standard library**: generic JSON `webhook` (custom
  headers), `ntfy`, `discord`, `telegram`, `email` (SMTP + STARTTLS) and
  `shell` (argv list or platform command, alert data on stdin and in
  `DUMP1090_ALERT_*` environment variables).
- **Cooldown / dedupe per rule + aircraft** — a hovering emergency that stays
  in `aircraft.json` for ten minutes alerts once, not 300 times.
- **Graceful degradation** — if one sink fails (DNS, SMTP, exit code), the
  error is logged and the remaining sinks still run.
- **Baseline first poll** — restarting the process never replays history or
  floods you with a `new` alert for every aircraft already in the air.
- **Zero runtime dependencies** — `tomllib`, `urllib`, `smtplib`,
  `subprocess` only; fully typed (`py.typed`), 170 tests at 97% coverage.

## Install

```bash
pip install dump1090-alerts
```

Or from source:

```bash
git clone https://github.com/technothatch/dump1090-alerts.git
cd dump1090-alerts
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Quickstart

Copy the annotated example configuration and edit it to taste:

```bash
cp alerts.toml.example alerts.toml
```

Check what would fire against your decoder — no notifications are sent:

```console
$ dump1090-alerts --config alerts.toml --once --dry-run
# http://localhost:8080/data/aircraft.json — 5 rule(s), dry-run, once
would fire emergency: [emergency] 40621D BAW117 34,000 ft squawk 7700 emergency=major rssi -4.3 dBFS pos 51.4712,-0.4543 (Emergency squawk (7500/7600/7700) or any non-none emergency flag)
would fire overhead: [overhead] 40621D BAW117 34,000 ft squawk 7700 emergency=major rssi -4.3 dBFS pos 51.4712,-0.4543 (Anything above FL300 within 25 nm of the receiver)
would fire strong-signal: [strong-signal] 40621D BAW117 34,000 ft squawk 7700 emergency=major rssi -4.3 dBFS pos 51.4712,-0.4543 (Very strong signal — usually an aircraft close overhead)
would fire new-aircraft: [new-aircraft] 40621D BAW117 34,000 ft squawk 7700 emergency=major rssi -4.3 dBFS pos 51.4712,-0.4543 (First time this ICAO hex is seen (one hour cooldown))
would fire new-aircraft: [new-aircraft] 4CA918 RYR22GK on ground squawk 1200 rssi -14.7 dBFS pos 51.4650,-0.4900 (First time this ICAO hex is seen (one hour cooldown))
```

Happy? Drop `--dry-run` and let it run (the first poll only records state,
alerts start on the second poll, two seconds later):

```console
$ dump1090-alerts --config alerts.toml
# http://localhost:8080/data/aircraft.json — 5 rule(s), every 2.0s
```

Point it at another decoder with `--url` or the `DUMP1090_URL` environment
variable — both accept a bare base URL or a full `.../data/aircraft.json`
document URL. `--interval`, `--timeout` and `--max-polls` tune the polling;
`--once` evaluates a single poll and exits, which makes it easy to drive from
cron.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success (individual sink failures are logged to stderr, not fatal) |
| `1` | Configuration error, or the decoder could not be read in `--once` mode |

## Configuration reference

### Command line / environment

| Flag | Environment | Meaning | Default |
| --- | --- | --- | --- |
| `--config PATH` | — | TOML configuration file | `alerts.toml` |
| `--url URL` | `DUMP1090_URL` | Decoder base URL or full `aircraft.json` URL | `http://localhost:8080` |
| `--interval SECS` | — | Poll interval in seconds | `2.0` |
| `--timeout SECS` | — | HTTP timeout in seconds | `5.0` |
| `--once` | — | Evaluate one poll, then exit | off |
| `--dry-run` | — | Print would-be notifications, deliver nothing | off |
| `--max-polls N` | — | Stop after N polls (`0` = run forever) | `0` |
| `--version` | — | Print version and exit | — |

### `alerts.toml`

```toml
[engine]
cooldown = 60    # default seconds before the same rule+aircraft may fire again
baseline = true  # first poll only records state (no restart bursts)

[[rules]]
name = "emergency"          # required, unique
description = "..."         # appended to the notification
cooldown = 3600             # optional per-rule override of engine.cooldown

[rules.match]               # every condition present must hold (ANDed)
squawk = ["7500", "7600", "7700"]
emergency = true

[[rules.sinks]]             # at least one sink per rule
type = "ntfy"
url = "https://ntfy.sh/your-secret-topic"
```

### Conditions (`[rules.match]`)

| Key | Type | Matches when |
| --- | --- | --- |
| `hex` | array of strings | ICAO hex is in the list (case-insensitive) |
| `callsign_prefix` | string | `flight` starts with the prefix (case-insensitive) |
| `callsign_regex` | string | regex searches the trimmed callsign |
| `alt_above` | number | `alt_baro` ≥ value in ft (`"ground"` = 0) |
| `alt_below` | number | `alt_baro` ≤ value in ft (`"ground"` = 0) |
| `near` | table `{lat, lon, radius_nm}` | haversine distance ≤ radius |
| `squawk` | array of strings | Mode A code is in the list (1–4 octal digits) |
| `emergency` | bool | `true`: `emergency != "none"`; `false`: no emergency |
| `category_prefix` | string | emitter category starts with prefix (`"A"`, `"A3"`, …) |
| `rssi_min` | number | `rssi` ≥ threshold in dBFS (e.g. `-6.0`) |
| `on_ground` | bool | aircraft is (not) on the ground |
| `new` | bool | first poll this ICAO hex has ever been seen |
| `takeoff` | bool | ground → airborne transition since the previous poll |
| `landing` | bool | airborne → ground transition since the previous poll |

Unknown or missing aircraft fields never satisfy a condition: an aircraft
without a position cannot match `near`, one without `alt_baro` cannot match
altitude or ground conditions.

### Sinks (`[[rules.sinks]]`)

| `type` | Required options | Optional options |
| --- | --- | --- |
| `webhook` | `url` | `headers` (table of extra HTTP headers) |
| `ntfy` | `url` | `token`, `priority` (1–5), `tags`, `title` |
| `discord` | `url` (webhook URL) | `username` |
| `telegram` | `bot_token`, `chat_id` | — |
| `email` | `host`, `from`, `to` | `port` (587), `starttls` (true), `user`, `password`, `subject` |
| `shell` | `command` (string or argv array) | `timeout` (10 s) |

`webhook` posts the full alert JSON (`rule`, `description`, `message`, `hex`,
`ts`, `aircraft`); `ntfy` posts the message text with metadata in headers;
`discord` and `telegram` post their platform-native JSON payloads; `email`
sends the message line plus the JSON after it; `shell` receives the message on
stdin with `DUMP1090_ALERT_RULE`, `DUMP1090_ALERT_HEX` and
`DUMP1090_ALERT_MESSAGE` in the environment.

Invalid configuration is rejected before any network I/O, with an error that
names the offending key and what was expected:

```console
$ dump1090-alerts --config broken.toml --once
error: broken.toml: rule 'emergency'.match.squawk entry '8888' is not a Mode A code (1-4 octal digits such as 7700)
```

## How it works

```
   aircraft.json
   ─────────────▶ fetch.py —─▶ engine.py ──▶ Firing(rule, alert) ──▶ cli.py ──▶ sinks.py
   (urllib,          │            │                                     │         │
    resolves URL,    │            │ per-hex state:                      │         │  webhook
    tolerant shape)  │            │  seen / ground / last-fired         │  dry-run│  ntfy
                     ▼            ▼          │                          │  print  │  discord
              rules.py          models.py ◀──┘                          │  errors │  telegram
              condition eval    "ground"→0 ft, haversine,               │  to     │  email
              (AND semantics)   one-line summary                        │  stderr │  shell
```

- **`rules.py` is pure** — a `Match` plus an `EvalContext` (was I new? was I
  on the ground last poll?) in, a boolean out. No I/O, no clock, no globals,
  which is why every condition is trivially testable.
- **`engine.py` owns state**: which hexes are known, their last ground
  status, and the `(rule, hex) -> last fired` cooldown map. Cooldowns are
  checked *before* the state is updated, so a suppressed match does not
  refresh the timer.
- **`sinks.py` isolates failures** — `dispatch()` runs every action in turn,
  catches everything, and returns one error string per failed sink; a broken
  SMTP server never stops the webhook.
- **`cli.py` wires it together**: config load (fail fast with a clear
  message), URL resolution (`--url` → `DUMP1090_URL` → localhost), poll loop,
  dry-run printing.

## Development

```bash
git clone https://github.com/technothatch/dump1090-alerts.git
cd dump1090-alerts
pip install -e ".[dev]"

make lint     # ruff check + ruff format --check + mypy
make test     # pytest with coverage (fail-under 80)
make check    # everything, the same gate CI runs
```

The test suite covers every condition type, cooldown/dedupe timing with an
injected clock, baseline and ground-transition state, payload serialisation
for all six sinks (mocked HTTP and SMTP), config validation including the
error messages, URL resolution and the CLI flags — 170 tests at 97% line and
branch coverage. No test requires a running decoder.

## Related projects

- [dump1090-fa](https://github.com/flightaware/dump1090) — FlightAware's decoder
- [readsb](https://github.com/wiedehopf/readsb) — high-performance fork, JSON docs in-repo
- [tar1090](https://github.com/wiedehopf/tar1090) — web UI for readsb
- [graphs1090](https://github.com/wiedehopf/graphs1090) — statistics graphs
- [squawk-decoder](https://github.com/technothatch/squawk-decoder) — what a squawk code means
- [dump1090-cli](https://github.com/technothatch/dump1090-cli) — pretty CLI for live data

## License

MIT © technothatch. See [LICENSE](LICENSE).
