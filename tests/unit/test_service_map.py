import pytest
from hunch import DEFAULT_VOCABULARY as V
from hunch import Action, Entity

from custom_components.hunch.service_map import plan_calls


def _e(eid, domain=None):
    d = domain or eid.split(".")[0]
    return Entity(
        entity_id=eid,
        domain=d,
        name=eid,
        aliases=(),
        area_id=None,
        device_id=None,
        device_name=None,
        verbs=frozenset(),
        state=None,
    )


def test_turn_off_groups_everything_into_one_homeassistant_call():
    a = Action(V.by_name("turn_off"), (_e("light.a"), _e("switch.b"), _e("fan.c")), {})
    calls = plan_calls(a)
    assert len(calls) == 1
    assert (calls[0].domain, calls[0].service) == ("homeassistant", "turn_off")
    assert calls[0].data == {"entity_id": ["light.a", "switch.b", "fan.c"]}


def test_params_are_converted():
    assert plan_calls(
        Action(V.by_name("set_brightness"), (_e("light.a"),), {"brightness_pct": 35.0})
    )[0].data == {"entity_id": ["light.a"], "brightness_pct": 35}
    assert plan_calls(Action(V.by_name("set_position"), (_e("cover.a"),), {"position": 85.0}))[
        0
    ].data == {"entity_id": ["cover.a"], "position": 85}
    vol = plan_calls(
        Action(V.by_name("set_volume"), (_e("media_player.a"),), {"volume_level": 20.0})
    )[0]
    assert vol.service == "volume_set" and vol.data["volume_level"] == pytest.approx(0.2)
    t = plan_calls(Action(V.by_name("set_temperature"), (_e("climate.a"),), {"temperature": 22.0}))[
        0
    ]
    assert (t.domain, t.service, t.data["temperature"]) == ("climate", "set_temperature", 22.0)


def test_activate_splits_scene_and_script_by_domain():
    calls = plan_calls(Action(V.by_name("activate"), (_e("scene.a"), _e("script.b")), {}))
    assert {(c.domain, c.service) for c in calls} == {("scene", "turn_on"), ("script", "turn_on")}


def test_every_vocabulary_verb_has_a_mapping():
    for verb in V.verbs:
        if verb.is_query:
            assert plan_calls(Action(verb, (_e("sensor.x"),), {})) == []
            continue
        domain = sorted(verb.domains)[0]
        calls = plan_calls(
            Action(verb, (_e(f"{domain}.x"),), {verb.param.name: 50.0} if verb.param else {})
        )
        assert calls, verb.name


def test_arm_means_arm_away_and_lock_uses_lock_services():
    assert (
        plan_calls(Action(V.by_name("arm"), (_e("alarm_control_panel.a"),), {}))[0].service
        == "alarm_arm_away"
    )
    assert plan_calls(Action(V.by_name("unlock"), (_e("lock.a"),), {}))[0].service == "unlock"
