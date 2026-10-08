"""Poll evaluation: rules, per-aircraft state and cooldown/dedupe.

The engine is deliberately free of I/O — it takes the aircraft list a poll
produced and returns the alerts that should fire. Delivery, logging and the
polling loop live in :mod:`dump1090_alerts.cli`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from dump1090_alerts.config import Config, Rule
from dump1090_alerts.models import Aircraft, Alert, describe_aircraft, is_on_ground
from dump1090_alerts.rules import EvalContext, evaluate

__all__ = ["AlertEngine", "Firing"]


@dataclass(frozen=True, slots=True)
class Firing:
    """One rule matched one aircraft on one poll."""

    rule: Rule
    alert: Alert


class AlertEngine:
    """Stateful evaluator over successive ``aircraft.json`` polls.

    State kept per aircraft hex: whether it has been seen before (for ``new``)
    and its last known ground/airborne status (for ``takeoff``/``landing``),
    plus a ``(rule, hex) -> last fired`` map enforcing cooldowns.

    Args:
        config: Validated configuration holding the rules to evaluate.
        now: Clock injection point for deterministic cooldown tests.
        baseline: Override for ``engine.baseline``. ``None`` uses the config
            value; ``--once`` passes ``False`` because a single explicit
            evaluation has no later poll to defer to.
    """

    def __init__(
        self,
        config: Config,
        *,
        now: Callable[[], float] = time.time,
        baseline: bool | None = None,
    ) -> None:
        self.config = config
        self._now = now
        self._baseline = config.engine.baseline if baseline is None else baseline
        self._seen: set[str] = set()
        self._ground: dict[str, bool] = {}
        self._fired: dict[tuple[str, str], float] = {}
        self._baseline_done = False
        self.polls = 0
        self.alerts = 0

    def poll(self, aircraft: Sequence[Aircraft]) -> list[Firing]:
        """Evaluate every rule against one poll's aircraft array.

        When ``engine.baseline`` is enabled (the default) the very first poll
        only records state: no alerts fire, so restarting the process never
        replays history or fires a burst of ``new`` alerts for everything
        already in the air. Subsequent polls evaluate normally.
        """
        self.polls += 1
        baseline = self._baseline and not self._baseline_done
        firings: list[Firing] = []
        for entry in aircraft:
            hex_id = entry.get("hex")
            if not isinstance(hex_id, str) or not hex_id:
                continue
            ground = is_on_ground(entry)
            ctx = EvalContext(
                is_new=hex_id not in self._seen,
                was_ground=self._ground.get(hex_id),
            )
            if not baseline:
                firings.extend(self._evaluate_entry(entry, hex_id, ctx))
            self._seen.add(hex_id)
            if ground is not None:
                self._ground[hex_id] = ground
        self._baseline_done = True
        return firings

    def _evaluate_entry(self, entry: Aircraft, hex_id: str, ctx: EvalContext) -> list[Firing]:
        firings: list[Firing] = []
        now = self._now()
        for rule in self.config.rules:
            if not evaluate(rule.match, entry, ctx):
                continue
            key = (rule.name, hex_id)
            last = self._fired.get(key)
            if last is not None and now - last < rule.cooldown:
                continue
            self._fired[key] = now
            firings.append(Firing(rule=rule, alert=self._build_alert(rule, entry, hex_id, now)))
        self.alerts += len(firings)
        return firings

    @staticmethod
    def _build_alert(rule: Rule, entry: Aircraft, hex_id: str, ts: float) -> Alert:
        message = f"[{rule.name}] {describe_aircraft(entry)}"
        if rule.description:
            message += f" ({rule.description})"
        return Alert(
            rule=rule.name,
            description=rule.description,
            message=message,
            hex=hex_id,
            ts=ts,
            aircraft=entry,
        )
