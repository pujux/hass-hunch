"""Hunch-managed timers: kitchen timers, and the scheduled half of "für 15 Minuten" / "in 15
Minuten". Persisted in a HA Store, armed with the HA clock, announced with one event."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from hunch import DEFAULT_VOCABULARY, INVERSES, Action, ActiveTimer

from .const import EVENT_TIMER_FINISHED, MAX_OVERDUE_SECONDS, TIMER_STORE_KEY, TIMER_STORE_VERSION

_LOGGER = logging.getLogger(__name__)

OnFire = Callable[["HunchTimer", bool], Awaitable[None]]


@dataclass(frozen=True)
class StoredAction:
    verb: str
    entity_ids: tuple[str, ...]
    params: Mapping[str, float | str]


@dataclass(frozen=True)
class HunchTimer:
    timer_id: str
    kind: str  # "timer" | "revert" | "delayed"
    label: str | None
    description: str | None
    duration_seconds: float
    due_at: datetime  # aware UTC
    actions: tuple[StoredAction, ...]
    language: str
    conversation_id: str | None
    device_id: str | None
    satellite_id: str | None
    area_id: str | None
    user_id: str | None  # stored actions run as this user, like the immediate half did

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["due_at"] = self.due_at.isoformat()
        d["actions"] = [
            {"verb": a.verb, "entity_ids": list(a.entity_ids), "params": dict(a.params)}
            for a in self.actions
        ]
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> HunchTimer:
        due = dt_util.parse_datetime(d["due_at"])
        if due is None:
            raise ValueError(f"bad due_at {d['due_at']!r}")
        return cls(
            timer_id=d["timer_id"],
            kind=d["kind"],
            label=d.get("label"),
            description=d.get("description"),
            duration_seconds=float(d["duration_seconds"]),
            due_at=dt_util.as_utc(due),
            actions=tuple(
                StoredAction(a["verb"], tuple(a["entity_ids"]), dict(a.get("params", {})))
                for a in d.get("actions", [])
            ),
            language=d.get("language", "en"),
            conversation_id=d.get("conversation_id"),
            device_id=d.get("device_id"),
            satellite_id=d.get("satellite_id"),
            area_id=d.get("area_id"),
            user_id=d.get("user_id"),
        )


def new_timer_id() -> str:
    return uuid4().hex


def stored_actions(actions: Sequence[Action]) -> tuple[StoredAction, ...]:
    return tuple(
        StoredAction(a.verb.name, tuple(e.entity_id for e in a.targets), dict(a.params))
        for a in actions
    )


# The state a device is in after each invertible verb. A "für" target that was already in it
# before the request is left alone afterwards: "Licht im Bad für 10 Minuten aus" on a light that
# is already off must not switch it on 10 minutes later. A code table, read against hass.states.
COMMANDED_STATE = {
    "turn_on": "on",
    "turn_off": "off",
    "open": "open",
    "close": "closed",
    "unlock": "unlocked",
    "media_play": "playing",
    "media_pause": "paused",
}


def already_in_state(actions: Sequence[Action], states: Mapping[str, str | None]) -> set[str]:
    """The targets whose state before the request (`states`: entity id -> state) already was the
    one their action commands. A verb missing from the table counts as a change."""
    return {
        e.entity_id
        for a in actions
        if (want := COMMANDED_STATE.get(a.verb.name)) is not None
        for e in a.targets
        if states.get(e.entity_id) == want
    }


def inverse_actions(actions: Sequence[Action], ok_ids: set[str]) -> tuple[Action, ...]:
    """The undo of "für": each action's inverse verb on the targets that were actually
    changed (`ok_ids`), without values (an inverse has none)."""
    out = []
    for a in actions:
        inverse = INVERSES.get(a.verb.name)
        targets = tuple(e for e in a.targets if e.entity_id in ok_ids)
        if inverse and targets:
            out.append(Action(DEFAULT_VOCABULARY.by_name(inverse), targets, {}))
    return tuple(out)


class TimerStore:
    def __init__(
        self,
        hass: HomeAssistant,
        on_fire: OnFire,
        entry: ConfigEntry | None = None,
    ) -> None:
        self._hass = hass
        self._on_fire = on_fire
        self._entry = entry  # fire tasks are tracked by the entry when there is one
        self._store: Store[dict[str, Any]] = Store(hass, TIMER_STORE_VERSION, TIMER_STORE_KEY)
        self._timers: dict[str, HunchTimer] = {}
        self._unsub: dict[str, CALLBACK_TYPE] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        overdue: list[HunchTimer] = []
        for raw in data.get("timers", []):
            try:
                timer = HunchTimer.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                _LOGGER.warning("Dropping unreadable timer %s", raw.get("timer_id"))
                continue
            self._timers[timer.timer_id] = timer
            if timer.due_at <= dt_util.utcnow():
                overdue.append(timer)
            else:
                self._arm(timer)
        if overdue:
            # Fire once HA has started: the services a revert needs may not exist yet.
            @callback
            def _started(_hass: HomeAssistant) -> None:
                for timer in overdue:
                    self._schedule(self._fire(timer.timer_id, True))

            async_at_started(self._hass, _started)

    def _schedule(self, coro: Coroutine[Any, Any, None]) -> None:
        if self._entry is not None:
            self._entry.async_create_background_task(self._hass, coro, "hunch_timer")
        else:
            self._hass.async_create_task(coro)

    def active(self) -> tuple[HunchTimer, ...]:
        return tuple(sorted(self._timers.values(), key=lambda t: t.due_at))

    def remaining(self, timer: HunchTimer) -> float:
        return max(0.0, (timer.due_at - dt_util.utcnow()).total_seconds())

    def as_active_timers(self) -> tuple[ActiveTimer, ...]:
        return tuple(
            ActiveTimer(t.timer_id, t.label, self.remaining(t), t.kind, t.description)
            for t in self.active()
        )

    async def async_add(self, timer: HunchTimer) -> None:
        self._timers[timer.timer_id] = timer
        self._arm(timer)
        await self._save()

    async def async_cancel(self, timer_id: str) -> HunchTimer | None:
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return None
        if unsub := self._unsub.pop(timer_id, None):
            unsub()
        await self._save()
        return timer

    async def async_stop(self) -> None:
        for unsub in self._unsub.values():
            unsub()
        self._unsub.clear()

    def _arm(self, timer: HunchTimer) -> None:
        # `@callback`: a plain function would be run in the executor thread by HA, where
        # creating tasks is not allowed — the timer would never fire.
        @callback
        def _due(_now: datetime) -> None:
            self._unsub.pop(timer.timer_id, None)
            self._schedule(self._fire(timer.timer_id, False))

        self._unsub[timer.timer_id] = async_track_point_in_utc_time(self._hass, _due, timer.due_at)

    async def _fire(self, timer_id: str, overdue: bool) -> None:
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return
        try:
            await self._on_fire(timer, overdue)
        except Exception:  # noqa: BLE001 - a broken announcement must not kill the store
            _LOGGER.exception("Timer %s failed to fire", timer_id)
        try:
            await self._save()
        except Exception:  # noqa: BLE001 - a failed save must not stop the next timer
            _LOGGER.exception("Timer store could not be saved")

    async def _save(self) -> None:
        await self._store.async_save({"timers": [t.to_dict() for t in self.active()]})


async def async_fire_timer(
    hass: HomeAssistant, entry: ConfigEntry, timer: HunchTimer, overdue: bool
) -> None:
    """What a due timer does: run its stored actions (revert / delayed), fire the event, call the
    configured script. Import here is late to avoid a cycle with `__init__`."""
    from .executor import Executor

    rt = entry.runtime_data
    executed: list[str] = []
    failed: list[str] = []
    late = (dt_util.utcnow() - timer.due_at).total_seconds()
    skipped = bool(timer.actions) and late > MAX_OVERDUE_SECONDS
    if skipped:
        _LOGGER.warning(
            "Timer %s is %.0f s overdue; not carrying out its actions", timer.timer_id, late
        )
    if timer.actions and not skipped:
        home = rt.builder.build()
        actions = []
        for sa in timer.actions:
            targets = tuple(e for i in sa.entity_ids if (e := home.entity_by_id(i)) is not None)
            missing = set(sa.entity_ids) - {e.entity_id for e in targets}
            if missing:
                _LOGGER.warning("Timer %s: entities gone: %s", timer.timer_id, sorted(missing))
            if targets:
                actions.append(
                    Action(DEFAULT_VOCABULARY.by_name(sa.verb), targets, dict(sa.params))
                )
        results = await Executor(hass).execute(actions, Context(user_id=timer.user_id))
        executed = [r.entity_id for r in results if r.ok]
        failed = [r.entity_id for r in results if not r.ok]
    data = {
        **{k: v for k, v in timer.to_dict().items() if k != "actions"},
        "overdue": overdue,
        "skipped": skipped,
        "executed": executed,
        "failed": failed,
    }
    hass.bus.async_fire(EVENT_TIMER_FINISHED, data)
    if rt.timer_script:
        try:
            await hass.services.async_call(
                "script",
                "turn_on",
                {"entity_id": rt.timer_script, "variables": data},
                blocking=False,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Timer script %s failed: %s", rt.timer_script, err)
