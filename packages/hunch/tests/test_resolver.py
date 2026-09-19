from hunch.config import EngineConfig
from hunch.questions import Answers, ChoiceA, NoulA, ScoreA
from hunch.resolution import Escalate, NeedsConfirmation, Resolved, Trace
from hunch.resolver import resolve, score_to_value
from hunch.round1 import Shape
from hunch.round2 import NO_MATCH, plan_round2
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def _shape(verbs, flags=None, verb_probs=None, condition_domain=None):
    flag_names = (
        "collective", "has_exception", "has_condition",
        "is_query", "has_timing", "is_destructive",
    )
    f = {k: 0.05 for k in flag_names}
    f.update(flags or {})
    shape = Shape(
        tuple(V.by_name(v) for v in verbs), (), (), {}, {}, f, None, condition_domain
    )
    return shape, (verb_probs or {})


def _trace_with_verbs(verb_probs):
    t = Trace()
    for name, p in verb_probs.items():
        t.decide(f"verb:{name}", p, 0.7)
    return t


def _ents(home, *ids):
    return tuple(home.entity_by_id(i) for i in ids)


def test_score_to_value_interpolates():
    spec = V.by_name("set_brightness").param
    assert score_to_value(spec, 1.0) == 0
    assert score_to_value(spec, 6.0) == 100
    assert score_to_value(spec, 3.5) == 37.5


def test_collective_resolves_without_round2(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.9}, {"turn_off": 0.95})
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter")
    plan = plan_round2(home, shape, {"turn_off": cands}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert r.actions[0].verb.name == "turn_off" and r.actions[0].targets == cands
    assert r.confidence == 0.9  # min(verb 0.95, collective 0.9)


def test_exceptions_drop_excluded_entities(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.9, "has_exception": 0.85}, {"turn_off": 0.95})
    cands = _ents(home, "light.kitchen_ceiling", "switch.fridge")
    plan = plan_round2(home, shape, {"turn_off": cands}, config.thresholds)
    r2 = Answers("m", {
        "exclude:turn_off:light.kitchen_ceiling": NoulA(0.05),
        "exclude:turn_off:switch.fridge": NoulA(0.92),
    }, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert [e.entity_id for e in r.actions[0].targets] == ["light.kitchen_ceiling"]
    # min(0.95 verb, 0.9 collective, 0.85 exception flag, 0.95 kept, 0.92 excluded)
    assert abs(r.confidence - 0.85) < 1e-9


def test_singular_picks_choice_target(home, config):
    shape, vp = _shape(["turn_on"], {"collective": 0.1}, {"turn_on": 0.9})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, config.thresholds)
    choice = ChoiceA(
        "Reading lamp", 0.88, {"Reading lamp": 0.88, "Living room main": 0.12}
    )
    r2 = Answers("m", {"target:turn_on": choice}, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert [e.entity_id for e in r.actions[0].targets] == ["light.reading_lamp"]
    assert r.confidence == 0.88


def test_param_is_interpolated_into_action(home, config):
    shape, vp = _shape(["set_brightness"], {"collective": 0.9}, {"set_brightness": 0.9})
    cands = {"set_brightness": _ents(home, "light.office_desk")}
    plan = plan_round2(home, shape, cands, config.thresholds)
    r2 = Answers("m", {"param:set_brightness": ScoreA(3.5, 0.8, {})}, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert r.actions[0].params == {"brightness_pct": 37.5}


def test_confirm_tier_verb_needs_confirmation(home, config):
    shape, vp = _shape(["unlock"], {"collective": 0.9}, {"unlock": 0.95})
    plan = plan_round2(home, shape, {"unlock": _ents(home, "lock.front_door")}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, NeedsConfirmation) and r.reason == "risk:confirm"


def test_blast_radius_needs_confirmation(home, config):
    cfg = EngineConfig(model="m", max_silent_targets=2)
    shape, vp = _shape(["turn_off"], {"collective": 0.95}, {"turn_off": 0.95})
    cands = tuple(e for e in home.entities if "turn_off" in e.verbs)
    plan = plan_round2(home, shape, {"turn_off": cands}, cfg.thresholds)
    r = resolve(shape, plan, None, cfg, _trace_with_verbs(vp))
    assert isinstance(r, NeedsConfirmation) and r.reason == "blast_radius"


def test_mid_confidence_needs_confirmation(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.66}, {"turn_off": 0.72})
    plan = plan_round2(home, shape, {"turn_off": _ents(home, "light.hallway")}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, NeedsConfirmation) and r.reason == "confidence"


def test_low_confidence_escalates_with_partial(home, config):
    shape, vp = _shape(["turn_on"], {"collective": 0.1}, {"turn_on": 0.9})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, config.thresholds)
    choice = ChoiceA(
        "Reading lamp", 0.4, {"Reading lamp": 0.4, "Living room main": 0.35}
    )
    r2 = Answers("m", {"target:turn_on": choice}, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Escalate) and r.reason == "low_confidence"
    assert r.partial[0].verb.name == "turn_on"


def test_destructive_flag_escalates_before_anything(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.9, "is_destructive": 0.8}, {"turn_off": 0.95})
    plan = plan_round2(home, shape, {"turn_off": _ents(home, "light.hallway")}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, Escalate) and r.reason == "destructive"


def test_condition_is_attached_not_evaluated(home, config):
    # "if the blinds are closed, lock the front door"
    shape, vp = _shape(
        ["lock"], {"collective": 0.9, "has_condition": 0.8}, {"lock": 0.9},
        condition_domain="cover",
    )
    cands = {"lock": _ents(home, "lock.front_door")}
    plan = plan_round2(home, shape, cands, config.thresholds)
    r2 = Answers("m", {
        "cond_subject": ChoiceA("Blinds", 0.9, {"Blinds": 0.9}),
        "cond_state": ChoiceA("closed", 0.85, {"closed": 0.85, "open": 0.15}),
    }, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    # lock is CONFIRM tier, so we get NeedsConfirmation, but the condition rides along
    assert isinstance(r, NeedsConfirmation) and r.reason == "risk:confirm"
    assert r.condition is not None and r.condition.subject.entity_id == "cover.living_blinds"
    assert r.condition.expected_state == "closed"


def test_no_match_target_yields_no_action(home, config):
    shape, vp = _shape(["turn_on"], {"collective": 0.1}, {"turn_on": 0.9})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, config.thresholds)
    r2 = Answers("m", {"target:turn_on": ChoiceA(NO_MATCH, 0.9, {NO_MATCH: 0.9})}, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Escalate) and r.reason == "low_confidence" and r.partial == ()
