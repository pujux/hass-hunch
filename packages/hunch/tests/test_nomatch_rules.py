"""The three rules added after the first real-home run (2026-09-20)."""

from hunch.config import EngineConfig, Thresholds
from hunch.questions import Answers, ChoiceA, ChoiceQ
from hunch.resolution import Escalate, NeedsClarification, NeedsConfirmation, Trace
from hunch.resolver import resolve
from hunch.round1 import FLAGS, Shape, build_round1_questions
from hunch.round2 import NO_MATCH, build_round2_questions, plan_round2
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def _shape(verbs, flags=None, verb_probs=None):
    f = {k: 0.05 for k in FLAGS}
    f.update(flags or {})
    return Shape(tuple(V.by_name(v) for v in verbs), (), (), {}, {}, f, None, None), (
        verb_probs or {}
    )


def _trace(verb_probs):
    t = Trace()
    for name, p in verb_probs.items():
        t.decide(f"verb:{name}", p, 0.7)
    return t


def _ents(home, *ids):
    return tuple(home.entity_by_id(i) for i in ids)


def test_names_specific_is_a_round1_flag(home, vocab):
    assert "names_specific" in FLAGS
    qs = build_round1_questions(home, vocab)
    assert "flag:names_specific" in qs


def test_plural_name_with_specific_flag_routes_to_singular_choice(home, thresholds):
    shape, _ = _shape(["turn_on"], {"collective": 0.87, "names_specific": 0.9})
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter")
    plan = plan_round2(home, shape, {"turn_on": cands}, thresholds, 60)
    assert "turn_on" in plan.singular and "turn_on" not in plan.collective
    assert isinstance(build_round2_questions(shape, plan, home)["target:turn_on"], ChoiceQ)


def test_collective_without_specific_flag_stays_collective(home, thresholds):
    shape, _ = _shape(["turn_on"], {"collective": 0.87, "names_specific": 0.2})
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter")
    plan = plan_round2(home, shape, {"turn_on": cands}, thresholds, 60)
    assert plan.collective == {"turn_on": cands}


def test_no_match_with_plural_hint_falls_back_to_collective_with_confirmation(home, config):
    shape, vp = _shape(["set_position"], {"collective": 0.44}, {"set_position": 0.93})
    cands = _ents(home, "light.bedroom_left", "light.bedroom_right", "light.office_desk")
    cands = _ents(
        home, "cover.living_blinds"
    )  # one cover in fixture; use two lights to force a Choice
    cands = _ents(home, "light.bedroom_left", "light.office_desk")
    shape, vp = _shape(["turn_on"], {"collective": 0.44}, {"turn_on": 0.93})
    plan = plan_round2(home, shape, {"turn_on": cands}, config.thresholds, 60)
    r2 = Answers(
        "m",
        {
            "target:turn_on": ChoiceA(
                NO_MATCH,
                0.56,
                {NO_MATCH: 0.56, "Bedside lamps — Bedside left": 0.2, "Desk lamp": 0.24},
            )
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, NeedsConfirmation) and r.reason == "collective_fallback"
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.bedroom_left",
        "light.office_desk",
    }
    assert "collective_fallback:turn_on" in r.trace.notes


def test_no_match_with_spread_mass_clarifies_with_ranked_candidates(home, config):
    shape, vp = _shape(["query_state"], {"collective": 0.05}, {"query_state": 0.98})
    cands = _ents(home, "light.living_main", "light.reading_lamp", "cover.living_blinds")
    plan = plan_round2(home, shape, {"query_state": cands}, config.thresholds, 60)
    r2 = Answers(
        "m",
        {
            "target:query_state": ChoiceA(
                NO_MATCH,
                0.36,
                {NO_MATCH: 0.36, "Blinds": 0.34, "Reading lamp": 0.2, "Living room main": 0.1},
            )
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, NeedsClarification) and r.question_key == "which_device"
    assert [e.entity_id for e in r.candidates] == [
        "cover.living_blinds",
        "light.reading_lamp",
        "light.living_main",
    ]


def test_clarification_candidates_are_capped(home):
    cfg = EngineConfig(model="m", clarify_max_candidates=2)
    shape, vp = _shape(["turn_on"], {"collective": 0.05}, {"turn_on": 0.9})
    cands = _ents(home, "light.living_main", "light.reading_lamp", "light.office_desk")
    plan = plan_round2(home, shape, {"turn_on": cands}, cfg.thresholds, 60)
    r2 = Answers(
        "m",
        {
            "target:turn_on": ChoiceA(
                NO_MATCH,
                0.4,
                {NO_MATCH: 0.4, "Desk lamp": 0.3, "Reading lamp": 0.2, "Living room main": 0.1},
            )
        },
        None,
    )
    r = resolve(shape, plan, r2, cfg, _trace(vp))
    assert isinstance(r, NeedsClarification) and len(r.candidates) == 2


def test_confident_no_match_still_escalates(home, config):
    shape, vp = _shape(["turn_on"], {"collective": 0.05}, {"turn_on": 0.9})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, config.thresholds, 60)
    r2 = Answers("m", {"target:turn_on": ChoiceA(NO_MATCH, 0.95, {NO_MATCH: 0.95})}, None)
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, Escalate) and r.reason == "low_confidence"


def test_new_thresholds_have_defaults():
    t = Thresholds()
    assert t.specific_device == 0.7 and t.collective_fallback == 0.4 and t.no_match_clarify == 0.6
    assert EngineConfig(model="m").clarify_max_candidates == 5


def test_specific_flag_does_not_defeat_an_exception(home, thresholds):
    # "turn off everything in the kitchen except the fridge": the named device is the exclusion.
    shape, _ = _shape(
        ["turn_off"], {"collective": 0.9, "has_exception": 0.85, "names_specific": 0.9}
    )
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter", "switch.fridge")
    plan = plan_round2(home, shape, {"turn_off": cands}, thresholds, 60)
    assert "turn_off" in plan.exclude and "turn_off" not in plan.singular
