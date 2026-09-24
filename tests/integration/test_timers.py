from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hunch.const import TIMER_STORE_KEY
from custom_components.hunch.timers import HunchTimer, StoredAction, TimerStore


def _timer(tid="t1", seconds=480.0, kind="timer", label="Nudeln", actions=()):
    return HunchTimer(
        timer_id=tid,
        kind=kind,
        label=label,
        description=None,
        duration_seconds=seconds,
        due_at=dt_util.utcnow() + timedelta(seconds=seconds),
        actions=tuple(actions),
        language="de",
        conversation_id="c1",
        device_id=None,
        satellite_id=None,
        area_id=None,
        user_id="u",
    )


async def test_add_fires_once_due_and_saves(hass: HomeAssistant, hass_storage, freezer):
    fired = AsyncMock()
    store = TimerStore(hass, fired)
    await store.async_load()
    await store.async_add(_timer())
    assert len(store.active()) == 1 and 479 <= store.remaining(store.active()[0]) <= 480
    assert hass_storage[TIMER_STORE_KEY]["data"]["timers"][0]["label"] == "Nudeln"
    freezer.tick(timedelta(seconds=481))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    fired.assert_awaited_once()
    timer, overdue = fired.await_args.args
    assert timer.timer_id == "t1" and overdue is False
    assert store.active() == () and hass_storage[TIMER_STORE_KEY]["data"]["timers"] == []


async def test_cancel_disarms(hass: HomeAssistant, hass_storage, freezer):
    fired = AsyncMock()
    store = TimerStore(hass, fired)
    await store.async_load()
    await store.async_add(_timer())
    assert (await store.async_cancel("t1")).label == "Nudeln"
    assert await store.async_cancel("t1") is None
    freezer.tick(timedelta(seconds=500))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    fired.assert_not_awaited()


async def test_load_rearms_and_fires_overdue_timers(hass: HomeAssistant, hass_storage, freezer):
    due = dt_util.utcnow() - timedelta(seconds=30)
    later = dt_util.utcnow() + timedelta(seconds=300)
    hass_storage[TIMER_STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": TIMER_STORE_KEY,
        "data": {
            "timers": [
                {
                    "timer_id": "old",
                    "kind": "timer",
                    "label": "Tee",
                    "description": None,
                    "duration_seconds": 60,
                    "due_at": due.isoformat(),
                    "actions": [],
                    "language": "de",
                    "conversation_id": None,
                    "device_id": None,
                    "satellite_id": None,
                    "area_id": None,
                    "user_id": None,
                },
                {
                    "timer_id": "new",
                    "kind": "delayed",
                    "label": None,
                    "description": "Wandlampe aus",
                    "duration_seconds": 300,
                    "due_at": later.isoformat(),
                    "actions": [{"verb": "turn_off", "entity_ids": ["light.a"], "params": {}}],
                    "language": "de",
                    "conversation_id": None,
                    "device_id": None,
                    "satellite_id": None,
                    "area_id": None,
                    "user_id": None,
                },
            ]
        },
    }
    fired = AsyncMock()
    store = TimerStore(hass, fired)
    await store.async_load()
    await hass.async_block_till_done()
    fired.assert_awaited_once()
    timer, overdue = fired.await_args.args
    assert timer.timer_id == "old" and overdue is True
    active = store.active()
    assert [t.timer_id for t in active] == ["new"]
    assert active[0].actions == (StoredAction("turn_off", ("light.a",), {}),)
    a = store.as_active_timers()
    assert (
        a[0].description == "Wandlampe aus"
        and a[0].kind == "delayed"
        and 299 <= a[0].remaining_seconds <= 300
    )
    await store.async_stop()
