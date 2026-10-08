"""Command-line interface for dump1090-alerts."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence

from dump1090_alerts import __version__
from dump1090_alerts.config import ConfigError, load_config
from dump1090_alerts.engine import AlertEngine, Firing
from dump1090_alerts.fetch import DEFAULT_HOST, FetchError, fetch_aircraft, resolve_url
from dump1090_alerts.models import Aircraft
from dump1090_alerts.sinks import SinkRuntime, dispatch

EXIT_OK = 0
EXIT_ERROR = 1

Fetcher = Callable[..., list[Aircraft]]

__all__ = ["EXIT_ERROR", "EXIT_OK", "main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dump1090-alerts",
        description=(
            "Rule engine that watches a dump1090 / readsb aircraft.json endpoint "
            "and fires notifications (webhook, ntfy, Discord, Telegram, email, shell)."
        ),
        epilog="ADS-B data is public by design; nothing here contacts the network unless asked to.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default="alerts.toml",
        help="TOML configuration file (default: alerts.toml)",
    )
    parser.add_argument(
        "--url",
        metavar="URL",
        default=None,
        help=(f"decoder base URL or aircraft.json URL (env DUMP1090_URL, default {DEFAULT_HOST})"),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        metavar="SECS",
        help="poll interval in seconds (default: 2)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        metavar="SECS",
        help="HTTP timeout in seconds (default: 5)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="evaluate a single poll and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print would-be notifications instead of delivering them",
    )
    parser.add_argument(
        "--max-polls",
        type=int,
        default=0,
        metavar="N",
        help="stop after N polls (0 = run forever; used by tests)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _report(firing: Firing, *, dry_run: bool, runtime: SinkRuntime | None) -> None:
    if dry_run:
        print(f"would fire {firing.alert.rule}: {firing.alert.message}")
        return
    for error in dispatch(firing.alert, firing.rule.actions, runtime=runtime):
        print(f"error: sink {error}", file=sys.stderr)


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetcher | None = None,
    runtime: SinkRuntime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Entry point. Returns a process exit code.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).
        fetch: Injectable aircraft fetcher for tests.
        runtime: Injectable sink transport for tests.
        sleep: Injectable sleep for tests.
    """
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    fetcher = fetch if fetch is not None else fetch_aircraft
    override = args.url if args.url else os.environ.get("DUMP1090_URL", "")
    url = resolve_url(override or DEFAULT_HOST)
    engine = AlertEngine(config, baseline=False if args.once else None)
    scope = "once" if args.once else f"every {args.interval}s"
    mode = "dry-run, " if args.dry_run else ""
    print(
        f"# {url} — {len(config.rules)} rule(s), {mode}{scope}",
        file=sys.stderr,
    )

    polls = 0
    try:
        while True:
            try:
                aircraft = fetcher(url, timeout=args.timeout)
            except FetchError as exc:
                print(f"error: {exc}", file=sys.stderr)
                if args.once:
                    return EXIT_ERROR
                polls += 1
                if args.max_polls and polls >= args.max_polls:
                    break
                sleep(args.interval)
                continue
            for firing in engine.poll(aircraft):
                _report(firing, dry_run=args.dry_run, runtime=runtime)
            polls += 1
            if args.once or (args.max_polls and polls >= args.max_polls):
                break
            sleep(args.interval)
    except KeyboardInterrupt:
        print("\n# stopped", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
