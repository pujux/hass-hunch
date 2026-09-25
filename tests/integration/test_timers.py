import asyncio
import logging
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.hunch.const import EVENT_TIMER_FINISHED, TIMER_STORE_KEY
from custom_components.hunch.timers import HunchTimer, StoredAction, TimerStore
from tests.integration.conftest import scripted


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
    assert len(store.active()) == 1 and store.remaining(store.active()[0]) == 480
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
    assert hass_storage[TIMER_STORE_KEY]["data"]["timers"] == []  # the cancel is saved
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
        and a[0].remaining_seconds == 300
    )
    await store.async_stop()


def _row(tid, due, **extra):
    return {
        "timer_id": tid,
        "kind": "timer",
        "label": tid,
        "description": None,
        "duration_seconds": 300,
        "due_at": due,
        "actions": [],
        "language": "de",
        "conversation_id": None,
        "device_id": None,
        "satellite_id": None,
        "area_id": None,
        "user_id": None,
        **extra,
    }


def _stored(data):
    return {"version": 1, "minor_version": 1, "key": TIMER_STORE_KEY, "data": data}


async def test_remaining_shrinks_with_time(hass: HomeAssistant, hass_storage, freezer):
    store = TimerStore(hass, AsyncMock())
    await store.async_load()
    await store.async_add(_timer(seconds=480))
    assert store.as_active_timers()[0].remaining_seconds == 480
    freezer.tick(timedelta(seconds=100))
    assert store.as_active_timers()[0].remaining_seconds == 380
    await store.async_stop()


async def test_active_is_sorted_by_due_at(hass: HomeAssistant, hass_storage, freezer):
    store = TimerStore(hass, AsyncMock())
    await store.async_load()
    await store.async_add(_timer("late", seconds=600, label="Reis"))
    await store.async_add(_timer("soon", seconds=200, label="Nudeln"))
    assert [t.timer_id for t in store.active()] == ["soon", "late"]
    assert [t.timer_id for t in store.as_active_timers()] == ["soon", "late"]
    stored = hass_storage[TIMER_STORE_KEY]["data"]["timers"]
    assert [t["timer_id"] for t in stored] == ["soon", "late"]
    await store.async_stop()


async def test_failing_on_fire_still_saves(hass: HomeAssistant, hass_storage, freezer, caplog):
    store = TimerStore(hass, AsyncMock(side_effect=RuntimeError("boom")))
    await store.async_load()
    await store.async_add(_timer())
    freezer.tick(timedelta(seconds=481))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert store.active() == ()
    assert hass_storage[TIMER_STORE_KEY]["data"]["timers"] == []
    assert "Timer t1 failed to fire" in caplog.text


async def test_cancelled_fire_still_saves(hass: HomeAssistant, hass_storage, freezer):
    # an unload cancels the fire task: the fired timer must not come back as overdue next load
    store = TimerStore(hass, AsyncMock(side_effect=asyncio.CancelledError))
    await store.async_load()
    await store.async_add(_timer())
    freezer.tick(timedelta(seconds=481))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert store.active() == ()
    assert hass_storage[TIMER_STORE_KEY]["data"]["timers"] == []


async def test_unreadable_timer_is_dropped_on_load(
    hass: HomeAssistant, hass_storage, freezer, caplog
):
    later = (dt_util.utcnow() + timedelta(seconds=300)).isoformat()
    hass_storage[TIMER_STORE_KEY] = _stored(
        {
            "timers": [
                _row("bad", "garbage"),
                _row("good", later),
                {"timer_id": "half"},  # keys missing
                "junk",  # not a row at all
            ]
        }
    )
    fired = AsyncMock()
    store = TimerStore(hass, fired)
    await store.async_load()
    await hass.async_block_till_done()
    assert [t.timer_id for t in store.active()] == ["good"]
    fired.assert_not_awaited()
    assert "Dropping unreadable timer bad" in caplog.text
    assert "Dropping unreadable timer half" in caplog.text
    assert "Dropping unreadable timer 'junk'" in caplog.text
    await store.async_stop()


async def test_non_dict_store_data_is_tolerated(hass: HomeAssistant, hass_storage):
    for data in (["junk"], "junk", {"timers": "junk"}, {"timers": None}, {}):
        hass_storage[TIMER_STORE_KEY] = _stored(data)
        store = TimerStore(hass, AsyncMock())
        await store.async_load()
        assert store.active() == (), data
        await store.async_stop()


# ---- what a due timer does (async_fire_timer) ----------------------------------------------


async def _spots(hass: HomeAssistant) -> str:
    kuche = ar.async_get(hass).async_create("Küche")
    reg = er.async_get(hass)
    e = reg.async_get_or_create(
        "light", "test", "1", suggested_object_id="kuche_spots", original_name="Spots"
    )
    reg.async_update_entity(e.entity_id, area_id=kuche.id)
    hass.states.async_set(e.entity_id, "on")
    async_expose_entity(hass, "conversation", e.entity_id, True)
    return e.entity_id


def _delayed(actions):
    return _timer("d1", seconds=600, kind="delayed", label=None, actions=actions)


async def _fire_after(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_delayed_action_on_a_vanished_entity_runs_the_rest(
    hass: HomeAssistant, setup_hunch, freezer, caplog
):
    spots = await _spots(hass)
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    off = async_mock_service(hass, "homeassistant", "turn_off")
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    await entry.runtime_data.timers.async_add(
        _delayed([StoredAction("turn_off", (spots, "light.gone"), {})])
    )
    await _fire_after(hass, freezer, 601)
    assert len(off) == 1 and off[0].data["entity_id"] == [spots]
    assert off[0].context.user_id == "u"
    assert len(events) == 1
    assert events[0].data["executed"] == [spots] and events[0].data["failed"] == []
    assert "Timer d1: entities gone: ['light.gone']" in caplog.text
    await hass.config_entries.async_unload(entry.entry_id)


async def test_missing_timer_script_is_logged_not_raised(
    hass: HomeAssistant, setup_hunch, freezer, caplog
):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls, options={"timer_script": "script.nope"})
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    await entry.runtime_data.timers.async_add(_timer(seconds=60))
    with caplog.at_level(logging.WARNING):
        await _fire_after(hass, freezer, 61)
    assert len(events) == 1 and events[0].data["label"] == "Nudeln"
    assert "Timer script script.nope failed" in caplog.text
    assert entry.runtime_data.timers.active() == ()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_actions_that_cannot_be_carried_out_still_fire_the_event(
    hass: HomeAssistant, setup_hunch, freezer, caplog
):
    spots = await _spots(hass)
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls, options={"timer_script": "script.ansage"})
    off = async_mock_service(hass, "homeassistant", "turn_off")
    script_calls = async_mock_service(hass, "script", "turn_on")
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    # a stored verb this version does not know (a store written by another version)
    await entry.runtime_data.timers.async_add(
        _delayed(
            [
                StoredAction("turn_off", (spots,), {}),
                StoredAction("frobnicate", (spots, "light.other"), {}),
            ]
        )
    )
    await _fire_after(hass, freezer, 601)
    assert off == []
    assert len(events) == 1
    assert events[0].data["executed"] == []
    assert events[0].data["failed"] == [spots, "light.other"]
    # a delayed action is never announced through the timer script, failed or not
    assert script_calls == [] and events[0].data["kind"] == "delayed"
    assert "Timer d1: its actions could not be carried out" in caplog.text
    await hass.config_entries.async_unload(entry.entry_id)
