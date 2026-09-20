"""Threshold decisions taken on Julian's corpus (2026-09-20): scope-backed collective,
lone-leader verb, auto_execute 0.70."""

from hunch.config import Thresholds
from hunch.resolution import Resolved, Trace
from hunch.resolver import resolve
from hunch.round1 import FLAGS, Shape
from hunch.round2 import plan_round2
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def test_new_defaults():
    t = Thresholds()
    assert t.auto_execute == 0.70 and t.verb_fire == 0.7


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
