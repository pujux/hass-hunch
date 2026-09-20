from hunch.questions import ChoiceQ, NoulQ, ScoreQ
from hunch.round1 import Shape
from hunch.round2 import (
    NO_MATCH,
    build_round2_questions,
    build_round2_state,
    device_options,
    plan_round2,
    target_options,
)
from hunch.vocabulary import DEFAULT_VOCABULARY as V

_FLAG_NAMES = (
    "collective",
    "has_exception",
    "has_condition",
    "has_timing",
    "is_destructive",
)


def _shape(home, verbs, flags=None, condition_domain=None, areas=()):
    f = {k: 0.05 for k in _FLAG_NAMES}
    f.update(flags or {})
    return Shape(
        tuple(V.by_name(v) for v in verbs), tuple(areas), (), {}, {}, f, None, condition_domain
    )


def _ents(home, *ids):
    return tuple(home.entity_by_id(i) for i in ids)


def test_target_options_collapse_single_entity_devices_and_expand_multi(home):
    opts = target_options(
        _ents(
            home,
            "light.bedroom_left",
            "light.bedroom_right",
            "light.office_desk",
            "light.christmas_tree",
        )
    )
    labels = [o.label for o in opts]
    assert labels == [
        "Bedside lamps",  # the device itself: both lamps
        "Bedside lamps — Bedside left",
        "Bedside lamps — Bedside right",
        "Desk lamp",
        "Christmas tree",
    ]
    assert len(opts[0].entities) == 2 and all(len(o.entities) == 1 for o in opts[1:])


def test_target_options_dedupes_labels(home):
    a = home.entity_by_id("light.office_desk")
    b = type(a)(**{**a.__dict__, "entity_id": "light.office_desk_2", "device_id": "dev_other"})
    labels = [o.label for o in target_options((a, b))]
    assert labels == ["Desk lamp", "Desk lamp #2"]


def test_collective_without_exception_skips_round2(home, thresholds):
    shape = _shape(home, ["turn_off"], {"collective": 0.9})
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter")
    plan = plan_round2(home, shape, {"turn_off": cands}, thresholds, 60)
    assert plan.collective == {"turn_off": cands}
    assert build_round2_questions(shape, plan, home) == {}


def test_collective_with_exception_asks_exclude_per_candidate(home, thresholds):
    shape = _shape(home, ["turn_off"], {"collective": 0.9, "has_exception": 0.8})
    cands = _ents(home, "light.kitchen_ceiling", "switch.fridge")
    plan = plan_round2(home, shape, {"turn_off": cands}, thresholds, 60)
    qs = build_round2_questions(shape, plan, home)
    assert set(qs) == {"exclude:turn_off:light.kitchen_ceiling", "exclude:turn_off:switch.fridge"}
    assert all(isinstance(q, NoulQ) for q in qs.values())
    assert "Fridge" in qs["exclude:turn_off:switch.fridge"].instructions
    assert "Kitchen" in qs["exclude:turn_off:switch.fridge"].instructions


def test_singular_asks_one_choice_over_target_options(home, thresholds):
    shape = _shape(home, ["turn_on"], {"collective": 0.1})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, thresholds, 60)
    qs = build_round2_questions(shape, plan, home)
    assert isinstance(qs["target:turn_on"], ChoiceQ)
    assert qs["target:turn_on"].options == ("Living room main", "Reading lamp", NO_MATCH)


def test_param_question_uses_verb_spec(home, thresholds):
    shape = _shape(home, ["set_brightness"], {"collective": 0.9})
    plan = plan_round2(
        home, shape, {"set_brightness": _ents(home, "light.office_desk")}, thresholds, 60
    )
    qs = build_round2_questions(shape, plan, home)
    assert isinstance(qs["param:set_brightness"], ScoreQ)
    assert qs["param:set_brightness"].levels == V.by_name("set_brightness").param.levels


def test_param_verb_without_candidates_asks_no_param_question(home, thresholds):
    shape = _shape(home, ["set_brightness"], {"collective": 0.9})
    plan = plan_round2(home, shape, {"set_brightness": ()}, thresholds, 60)
    assert build_round2_questions(shape, plan, home) == {}
    assert plan.params == ()


def test_condition_questions_when_condition_domain_set(home, thresholds):
    shape = _shape(
        home, ["arm"], {"collective": 0.9, "has_condition": 0.8}, condition_domain="lock"
    )
    plan = plan_round2(home, shape, {"arm": ()}, thresholds, 60)
    qs = build_round2_questions(shape, plan, home)
    assert qs["cond_subject"].options == ("Front door", NO_MATCH)
    assert set(qs["cond_state"].options) >= {"locked", "unlocked"}


def test_round2_state_lists_only_relevant_candidates(home, thresholds):
    shape = _shape(home, ["turn_on"], {"collective": 0.1})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, thresholds, 60)
    state = build_round2_state("turn on the lamp", plan, home)
    assert state["request"] == "turn on the lamp"
    names = {c["name"] for c in state["candidates"]}
    assert names == {"Living room main", "Reading lamp"}
    assert {"aliases", "area", "device"} <= set(state["candidates"][0])
    assert {c["area"] for c in state["candidates"]} == {"Living room"}


def test_device_options_offer_whole_devices_not_entities(home):
    opts = device_options(
        _ents(
            home,
            "light.bedroom_left",
            "light.bedroom_right",
            "light.office_desk",
            "light.christmas_tree",
        )
    )
    assert [o.label for o in opts] == ["Bedside lamps", "Desk lamp", "Christmas tree"]
    assert [e.entity_id for e in opts[0].entities] == ["light.bedroom_left", "light.bedroom_right"]


def test_device_options_dedupe_labels(home):
    a = home.entity_by_id("light.office_desk")
    b = type(a)(**{**a.__dict__, "entity_id": "light.office_desk_2", "device_id": "dev_other"})
    assert [o.label for o in device_options((a, b))] == ["Desk lamp", "Desk lamp #2"]


def test_condition_candidates_are_scoped_to_the_fired_areas(home, thresholds):
    shape = _shape(
        home,
        ["arm"],
        {"collective": 0.9, "has_condition": 0.8},
        condition_domain="light",
        areas=("kitchen",),
    )
    plan = plan_round2(home, shape, {"arm": ()}, thresholds, 60)
    qs = build_round2_questions(shape, plan, home)
    assert qs["cond_subject"].options == ("Kitchen ceiling", "Counter strip", NO_MATCH)


def test_condition_candidates_over_the_cap_are_dropped_not_truncated(home, thresholds):
    shape = _shape(
        home, ["arm"], {"collective": 0.9, "has_condition": 0.8}, condition_domain="light"
    )
    plan = plan_round2(home, shape, {"arm": ()}, thresholds, 2)
    assert plan.condition_candidates == ()
    assert "cond_subject" not in build_round2_questions(shape, plan, home)


def test_round2_state_tells_jev_what_place_the_request_named(home, thresholds):
    # 'Licht oben aus': the alias resolved to every upstairs room in code; Jev must learn that the
    # candidates ARE the whole floor, or "all of these?" hovers near 0.4.
    upstairs = next(f for f in home.floors if f.floor_id == "upstairs")
    shape = _shape(home, ["turn_off"], {"collective": 0.3}, areas=upstairs.area_ids)
    plan = plan_round2(home, shape, {"turn_off": _ents(home, "light.bedroom_left")}, thresholds, 60)
    state = build_round2_state("Licht oben aus", plan, home, shape)
    assert state["scope"]["whole_floors"] == [
        {"name": upstairs.name, "aliases": list(upstairs.aliases)}
    ]
    assert "rooms" not in state["scope"]
    # a single room is listed as a room, not as a floor
    shape = _shape(home, ["turn_off"], {"collective": 0.3}, areas=("kitchen",))
    state = build_round2_state("Licht in der Küche aus", plan, home, shape)
    assert state["scope"] == {
        "place": "the floors and rooms named in the request",
        "rooms": ["Kitchen"],
    }
    # no place at all: say so (the whole home when Jev said so)
    shape = _shape(home, ["turn_off"], {"collective": 0.3})
    assert build_round2_state("Licht aus", plan, home, shape)["scope"] == {
        "place": "no place named"
    }
    assert "scope" not in build_round2_state("Licht aus", plan, home)
