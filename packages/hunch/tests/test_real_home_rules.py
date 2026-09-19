"""Rules A–F from Julian's real prompts (2026-09-20)."""

from hunch.client import FakeDecisionClient
from hunch.config import EngineConfig
from hunch.engine import Engine
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA, ScoreQ
from hunch.resolution import Escalate, NeedsClarification, Resolved, Trace
from hunch.resolver import resolve
from hunch.round1 import FLAGS, Shape
from hunch.round2 import NO_MATCH, build_round2_questions, extract_explicit, plan_round2
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


# ---- A: explicit numbers are read by code ----------------------------------------------------


def test_extract_explicit_percent_degrees_fraction():
    pos = V.by_name("set_position").param
    assert extract_explicit(pos, "Badezimmer Rollos auf 15%") == 15.0
    assert extract_explicit(pos, "Rollos auf 40 Prozent") == 40.0
    assert extract_explicit(pos, "Rollos halb runter") is None
    assert extract_explicit(pos, "Rollos auf 15% und dann 30%") is None  # ambiguous
    temp = V.by_name("set_temperature").param
    assert extract_explicit(temp, "Stell die Klimaanlage auf 22 Grad") == 22.0
    assert extract_explicit(temp, "set it to 21.5°") == 21.5
    assert extract_explicit(temp, "make it warmer") is None
    vol = V.by_name("set_volume").param
    assert extract_explicit(vol, "volume to 30%") == 0.3


def test_explicit_value_skips_the_score_question_and_lands_in_params(home, thresholds):
    shape, vp = _shape(["set_position"], {"collective": 0.9}, {"set_position": 0.98})
    plan = plan_round2(
        home,
        shape,
        {"set_position": _ents(home, "cover.living_blinds")},
        thresholds,
        60,
        prompt="Badezimmer Rollos auf 15%",
    )
    assert plan.explicit == {"set_position": {"position": 15.0}} and plan.params == ()
    assert "param:set_position" not in build_round2_questions(shape, plan, home)
    r = resolve(shape, plan, None, EngineConfig(model="m"), _trace(vp))
    assert isinstance(r, Resolved) and r.actions[0].params == {"position": 15.0}


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


def test_scoped_sweep_when_no_device_named(home, config):
    shape, vp = _shape(
        ["turn_on"],
        {"collective": 0.05},
        {"turn_on": 0.96},
        areas=("kitchen", "living", "hallway"),
        area_probs={"kitchen": 0.2, "living": 0.2, "hallway": 0.2},
    )
    t = _trace(vp)
    t.decide("floor:downstairs", 0.9, 0.6)
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
    assert plan.scoped_sweep_ok == ("turn_on",)
    r2 = Answers("m", {"target:turn_on": ChoiceA(NO_MATCH, 0.31, {NO_MATCH: 0.31})}, None)
    r = resolve(shape, plan, r2, config, t)
    assert isinstance(r, Resolved) and len(r.actions[0].targets) == 5
    assert (
        r.confidence == 0.9
    )  # min(verb 0.96, strongest scope signal 0.9); the no-match confidence is not a contribution
    assert "scoped_sweep:turn_on" in r.trace.notes


def test_scoped_sweep_not_allowed_when_a_device_is_named(home, config):
    shape, vp = _shape(
        ["turn_on"],
        {"collective": 0.05},
        {"turn_on": 0.96},
        areas=("living",),
        area_probs={"living": 0.95},
    )
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(
        home,
        shape,
        {"turn_on": cands},
        config.thresholds,
        60,
        prompt="Reading lamp im Wohnzimmer an",
    )
    assert plan.scoped_sweep_ok == ()


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
