from homeassistant.core import Context, HomeAssistant
from hunch import DEFAULT_VOCABULARY as V
from hunch import Action, Condition, Entity
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hunch.executor import Executor


def _e(eid, name="x", area=None):
    return Entity(
        entity_id=eid,
        domain=eid.split(".")[0],
        name=name,
        aliases=(),
        area_id=area,
        device_id=None,
        device_name=None,
        verbs=frozenset(),
        state=None,
    )


async def test_execute_calls_services_with_context_and_reports_per_target(hass: HomeAssistant):
    calls = async_mock_service(hass, "homeassistant", "turn_off")
    ctx = Context(user_id="u1")
    res = await Executor(hass).execute(
        [Action(V.by_name("turn_off"), (_e("light.a"), _e("switch.b")), {})], ctx
    )
    assert len(calls) == 1 and calls[0].data["entity_id"] == ["light.a", "switch.b"]
    assert calls[0].context is ctx
    assert all(r.ok for r in res) and [r.entity_id for r in res] == ["light.a", "switch.b"]


async def test_missing_service_marks_targets_failed_and_continues(hass: HomeAssistant):
    ok_calls = async_mock_service(hass, "cover", "close_cover")
    res = await Executor(hass).execute(
        [
            Action(V.by_name("lock"), (_e("lock.front"),), {}),
            Action(V.by_name("close"), (_e("cover.a"),), {}),
        ],
        Context(),
    )
    assert [r.ok for r in res] == [False, True] and res[0].error == "ServiceNotFound"
    assert len(ok_calls) == 1


async def test_condition_and_state_readings(hass: HomeAssistant):
    hass.states.async_set("binary_sensor.door", "on", {"device_class": "door"})
    hass.states.async_set("sensor.temp", "23.6", {"unit_of_measurement": "°C"})
    ex = Executor(hass)
    assert ex.condition_holds(Condition(_e("binary_sensor.door"), "on")) is True
    assert ex.condition_holds(Condition(_e("binary_sensor.door"), "off")) is False
    assert ex.condition_holds(Condition(_e("binary_sensor.missing"), "on")) is None
    r = ex.read_states([_e("sensor.temp", "Temperatur", "wohnzimmer")])[0]
    assert (r.state, r.unit, r.area_id) == ("23.6", "°C", "wohnzimmer")
