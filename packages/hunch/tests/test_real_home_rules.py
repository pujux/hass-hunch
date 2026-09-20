"""Rules A–F from Julian's real prompts (2026-09-20)."""

from hunch.client import FakeDecisionClient
from hunch.config import EngineConfig
from hunch.engine import Engine
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA, ScoreA, ScoreQ
from hunch.resolution import Escalate, NeedsClarification, Resolved, Trace
from hunch.resolver import resolve
from hunch.round1 import FLAGS, Shape
from hunch.round2 import NO_MATCH, build_round2_questions, numeric_literals, plan_round2
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def _shape(verbs, flags=None, verb_probs=None, areas=(), area_probs=None, condition_domain=None):
    f = {k: 0.05 for k in FLAGS}
    f.update(flags or {})
    ap = dict(area_probs or {})
    return Shape(
        tuple(V.by_name(v) for v in verbs), tuple(areas), (), ap, {}, f, None, condition_domain
    ), (verb_probs or {})


def _trace(verb_probs):
    t = Trace()
    for name, p in verb_probs.items():
        t.decide(f"verb:{name}", p, 0.7)
    return t


def _ents(home, *ids):
    return tuple(home.entity_by_id(i) for i in ids)


# ---- A: numbers — code lists them, Jev picks and interprets, code computes ----------------


def test_numeric_literals_are_listed_verbatim():
    assert numeric_literals("Badezimmer Rollos auf 15%") == ("15%",)
    assert numeric_literals("Stell die Klimaanlage auf 22,5 Grad") == ("22,5 Grad",)
    assert numeric_literals("Rollos auf 15% und Licht auf 50 Prozent") == ("15%", "50 Prozent")
    assert numeric_literals("Rollos halb runter") == ()


def test_numbers_present_ask_jev_which_and_how(home, thresholds):
    shape, _ = _shape(["set_position"], {"collective": 0.9}, {"set_position": 0.98})
    plan = plan_round2(
        home,
        shape,
        {"set_position": _ents(home, "cover.living_blinds")},
        thresholds,
        60,
        prompt="Badezimmer Rollos auf 15%",
    )
    assert plan.numeric == {"set_position": ("15%",)}
    qs = build_round2_questions(shape, plan, home)
    assert qs["param_value:set_position"].options == ("15%", NO_MATCH)
    assert "param_relative:set_position" in qs and "param_inverted:set_position" in qs
    assert qs["param_value:set_position"].descriptions[NO_MATCH]
    assert "param:set_position" in qs  # the rubric is still asked, as the fallback


def test_jev_picked_absolute_value_is_computed_by_code(home, config):
    shape, vp = _shape(["set_position"], {"collective": 0.9}, {"set_position": 0.98})
    plan = plan_round2(
        home,
        shape,
        {"set_position": _ents(home, "cover.living_blinds")},
        config.thresholds,
        60,
        prompt="Badezimmer Rollos auf 15%",
    )
    r2 = Answers(
        "m",
        {
            "param_value:set_position": ChoiceA("15%", 0.95, {"15%": 0.95}),
            "param_relative:set_position": NoulA(0.1),
            "param_inverted:set_position": NoulA(0.05),
            "param:set_position": ScoreA(0.6, 0.5, {}),
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, Resolved) and r.actions[0].params == {"position": 15.0}
    assert abs(r.confidence - 0.9) < 1e-9


def test_inverted_mode_flips_a_cover_position(home, config):
    shape, vp = _shape(["set_position"], {"collective": 0.9}, {"set_position": 0.98})
    plan = plan_round2(
        home,
        shape,
        {"set_position": _ents(home, "cover.living_blinds")},
        config.thresholds,
        60,
        prompt="Rollos 15% zu",
    )
    r2 = Answers(
        "m",
        {
            "param_value:set_position": ChoiceA("15%", 0.9, {}),
            "param_relative:set_position": NoulA(0.1),
            "param_inverted:set_position": NoulA(0.85),
            "param:set_position": ScoreA(1.0, 0.5, {}),
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, Resolved) and r.actions[0].params == {"position": 85.0}


def test_relative_change_escalates(home, config):
    shape, vp = _shape(["set_temperature"], {"collective": 0.9}, {"set_temperature": 0.98})
    plan = plan_round2(
        home,
        shape,
        {"set_temperature": _ents(home, "climate.bedroom")},
        config.thresholds,
        60,
        prompt="Mach die Heizung um 2 Grad wärmer",
    )
    r2 = Answers(
        "m",
        {
            "param_value:set_temperature": ChoiceA("2 Grad", 0.9, {}),
            "param_relative:set_temperature": NoulA(0.9),
            "param:set_temperature": ScoreA(3.5, 0.6, {}),
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, Escalate) and r.reason == "relative_change"


def test_number_that_is_not_the_value_falls_back_to_the_rubric(home, config):
    shape, vp = _shape(["set_temperature"], {"collective": 0.9}, {"set_temperature": 0.98})
    plan = plan_round2(
        home,
        shape,
        {"set_temperature": _ents(home, "climate.bedroom")},
        config.thresholds,
        60,
        prompt="Stell die Klimaanlage wärmer, draußen sind 5 Grad",
    )
    r2 = Answers(
        "m",
        {
            "param_value:set_temperature": ChoiceA(NO_MATCH, 0.9, {}),
            "param_relative:set_temperature": NoulA(0.1),
            "param:set_temperature": ScoreA(3.0, 0.8, {}),  # "warm (22°C)" level
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, Resolved) and r.actions[0].params == {"temperature": 22.0}
    assert "param:number_rejected:set_temperature" in r.trace.notes


def test_implausible_value_falls_back_to_the_rubric(home, config):
    shape, vp = _shape(["set_temperature"], {"collective": 0.9}, {"set_temperature": 0.98})
    plan = plan_round2(
        home,
        shape,
        {"set_temperature": _ents(home, "climate.bedroom")},
        config.thresholds,
        60,
        prompt="Klimaanlage auf 55 Grad",
    )
    r2 = Answers(
        "m",
        {
            "param_value:set_temperature": ChoiceA("55 Grad", 0.9, {}),
            "param_relative:set_temperature": NoulA(0.1),
            "param:set_temperature": ScoreA(4.0, 0.7, {}),
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert r.actions[0].params == {"temperature": 24.0}
    assert "param:out_of_bounds:set_temperature" in r.trace.notes


# ---- B: a weak pick with real alternatives asks instead of escalating ------------------------


def test_weak_chosen_target_clarifies_with_chosen_first(home, config):
    shape, vp = _shape(["query_state"], {}, {"query_state": 0.92})
    cands = _ents(home, "light.living_main", "light.reading_lamp", "cover.living_blinds")
    plan = plan_round2(home, shape, {"query_state": cands}, config.thresholds, 60)
    r2 = Answers(
        "m",
        {
            "target:query_state": ChoiceA(
                "Reading lamp",
                0.36,
                {"Reading lamp": 0.36, "Blinds": 0.33, "Living room main": 0.2, NO_MATCH: 0.11},
            )
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, NeedsClarification)
    assert [e.entity_id for e in r.candidates][:2] == ["light.reading_lamp", "cover.living_blinds"]


# ---- C: whole area/floor named, no device named, no single target -> all of them ---------


# ---- D/E: a condition or exception we could not honour blocks execution ---------------------


def test_unresolved_condition_escalates_even_with_a_resolvable_action(home, config):
    shape, vp = _shape(
        ["close"],
        {"collective": 0.9, "has_condition": 0.99},
        {"close": 0.92},
        condition_domain="sensor",
    )
    plan = plan_round2(
        home, shape, {"close": _ents(home, "cover.living_blinds")}, config.thresholds, 60
    )
    assert plan.condition_candidates == ()  # fixture has no sensors
    r = resolve(shape, plan, None, config, _trace(vp))
    assert isinstance(r, Escalate) and r.reason == "condition"
    assert r.partial[0].verb.name == "close"


def test_exception_that_excludes_nothing_escalates(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.9, "has_exception": 0.85}, {"turn_off": 0.95})
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter")
    plan = plan_round2(home, shape, {"turn_off": cands}, config.thresholds, 60)
    r2 = Answers(
        "m",
        {
            "exclude:turn_off:light.kitchen_ceiling": NoulA(0.03),
            "exclude:turn_off:light.kitchen_counter": NoulA(0.04),
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, Escalate) and r.reason == "exception"
    assert "exception_unresolved:turn_off" in r.trace.notes


# ---- F: a collective query over the scope cap goes to the LLM, not to "which area?" ---------


async def test_collective_query_over_cap_escalates(home, vocab):
    cfg = EngineConfig(model="m", scope_cap=2)

    def script(state, qs):
        out = {}
        for qid, q in qs.items():
            if qid == "verb:query_state":
                out[qid] = NoulA(0.97)
            elif qid == "domain:light":
                out[qid] = NoulA(0.9)
            elif qid == "flag:collective":
                out[qid] = NoulA(0.8)
            elif isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
            elif isinstance(q, ScoreQ):
                from hunch.questions import ScoreA

                out[qid] = ScoreA(1.0, 0.9, {})
            else:
                out[qid] = NoulA(0.05)
        return out

    r = await Engine(FakeDecisionClient(script), vocab, cfg).decide(home, "which lights are on?")
    assert isinstance(r, Escalate) and r.reason == "scope"
    assert "dropped:query_state:query_over_cap" in r.trace.notes


# ---- Jev decides "one of them or all of them" with the candidates in front of it -------------


def test_all_of_noul_selects_every_candidate(home, config):
    # "Licht im Untergeschoss an": Jev sees the 5 downstairs lights and says "all of these".
    shape, vp = _shape(
        ["turn_on"], {"collective": 0.05}, {"turn_on": 0.96}, areas=("kitchen", "living", "hallway")
    )
    cands = _ents(
        home,
        "light.kitchen_ceiling",
        "light.kitchen_counter",
        "light.living_main",
        "light.reading_lamp",
        "light.hallway",
    )
    plan = plan_round2(
        home, shape, {"turn_on": cands}, config.thresholds, 60, prompt="Licht im Untergeschoss an"
    )
    assert plan.all_of == ("turn_on",)
    r2 = Answers(
        "m",
        {
            "target:turn_on": ChoiceA(NO_MATCH, 0.31, {NO_MATCH: 0.31}),
            "all_of:turn_on": NoulA(0.92),
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, Resolved) and len(r.actions[0].targets) == 5
    assert r.confidence == 0.92 and "all_of:turn_on" in r.trace.notes


def test_named_unknown_device_is_not_all_of_them(home, config):
    # "Wohnzimmer Stehlampe aufdrehen" with no Stehlampe exposed: Jev says not all, weak pick -> ask.
    shape, vp = _shape(["turn_on"], {"collective": 0.05}, {"turn_on": 0.97}, areas=("living",))
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(
        home,
        shape,
        {"turn_on": cands},
        config.thresholds,
        60,
        prompt="Wohnzimmer Stehlampe aufdrehen",
    )
    r2 = Answers(
        "m",
        {
            "target:turn_on": ChoiceA(
                "Living room main", 0.34, {"Living room main": 0.34, "Reading lamp": 0.3}
            ),
            "all_of:turn_on": NoulA(0.08),
        },
        None,
    )
    r = resolve(shape, plan, r2, config, _trace(vp))
    assert isinstance(r, NeedsClarification)


def test_all_of_is_not_asked_for_queries(home, thresholds):
    shape, _ = _shape(["query_state"], {}, {"query_state": 0.9})
    plan = plan_round2(
        home,
        shape,
        {"query_state": _ents(home, "light.living_main", "light.reading_lamp")},
        thresholds,
        60,
    )
    assert plan.all_of == ()
