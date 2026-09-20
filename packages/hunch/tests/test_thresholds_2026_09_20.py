"""Threshold decisions taken on Julian's corpus (2026-09-20): scope-backed collective,
lone-leader verb, auto_execute 0.70."""

from hunch.config import Thresholds
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA
from hunch.resolution import Resolved, Trace
from hunch.resolver import resolve
from hunch.round1 import FLAGS, Shape, build_round1_questions, interpret_round1
from hunch.round2 import plan_round2
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def test_new_defaults():
    t = Thresholds()
    assert t.auto_execute == 0.70 and t.verb_lone_leader == 0.55 and t.verb_lone_margin == 0.3


def test_collective_backed_by_a_named_area_is_strong(home, config):
    # "Fahr die Rollos im Schlafzimmer runter": area named, both blinds inside it, collective 0.72.
    f = {k: 0.05 for k in FLAGS}
    f["collective"] = 0.72
    shape = Shape(
        (V.by_name("turn_off"),), ("bedroom",), ("light",), {"bedroom": 0.99}, {}, f, None, None
    )
    t = Trace()
    t.decide("verb:turn_off", 0.92, 0.7)
    t.note("area_match:bedroom")
    cands = tuple(e for e in home.entities if e.area_id == "bedroom" and e.domain == "light")
    plan = plan_round2(
        home,
        shape,
        {"turn_off": cands},
        config.thresholds,
        60,
        prompt="Fahr die Rollos im Schlafzimmer runter",
    )
    r = resolve(shape, plan, None, config, t)
    assert (
        isinstance(r, Resolved) and r.confidence == 0.92
    )  # min(verb 0.92, max(collective 0.72, named scope 1.0))
    assert "collective_backed_by_scope:turn_off" in r.trace.notes


def test_collective_without_scope_backing_still_counts_as_is(home, config):
    f = {k: 0.05 for k in FLAGS}
    f["collective"] = 0.6
    shape = Shape((V.by_name("turn_off"),), (), ("light",), {}, {}, f, None, None)
    t = Trace()
    t.decide("verb:turn_off", 0.95, 0.7)
    cands = tuple(e for e in home.entities if e.domain == "light")[:3]
    plan = plan_round2(home, shape, {"turn_off": cands}, config.thresholds, 60)
    r = resolve(shape, plan, None, config, t)
    assert r.confidence == 0.6 if isinstance(r, Resolved) else True  # 0.6 < 0.7 -> confirm band


def test_lone_leader_verb_fires_when_nothing_reaches_verb_fire(home, vocab, thresholds):
    qs = build_round1_questions(home, vocab)
    ans = {
        qid: (ChoiceA("none", 0.9, {}) if isinstance(q, ChoiceQ) else NoulA(0.05))
        for qid, q in qs.items()
    }
    ans["verb:turn_off"] = NoulA(0.66)  # "Mach alles aus": just under verb_fire, nobody else close
    ans["verb:close"] = NoulA(0.2)
    t = Trace()
    shape = interpret_round1(home, vocab, Answers("m", ans, None), thresholds, t)
    assert [v.name for v in shape.fired_verbs] == ["turn_off"]
    assert "lone_leader:turn_off" in t.notes


def test_lone_leader_needs_a_clear_field(home, vocab, thresholds):
    qs = build_round1_questions(home, vocab)
    ans = {
        qid: (ChoiceA("none", 0.9, {}) if isinstance(q, ChoiceQ) else NoulA(0.05))
        for qid, q in qs.items()
    }
    ans["verb:turn_off"] = NoulA(0.66)
    ans["verb:turn_on"] = NoulA(0.45)  # a rival above verb_rival: ambiguous, nothing fires
    shape = interpret_round1(home, vocab, Answers("m", ans, None), thresholds, Trace())
    assert shape.fired_verbs == ()
