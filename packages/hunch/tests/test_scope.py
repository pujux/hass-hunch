from hunch.config import EngineConfig
from hunch.resolution import Trace
from hunch.round1 import Shape
from hunch.scope import (
    Candidates,
    Clarify,
    DeviceRound,
    ScopeEscalate,
    device_label,
    ranked_widen,
    scope_candidates,
    strict_candidates,
)


def _shape(
    home, verbs=("turn_off",), areas=(), domains=(), area_probs=None, domain_probs=None, vocab=None
):
    from hunch.vocabulary import DEFAULT_VOCABULARY

    vocab = vocab or DEFAULT_VOCABULARY
    ap = {a.area_id: 0.05 for a in home.areas}
    ap.update(area_probs or {})
    dp = {d: 0.05 for d in home.domains}
    dp.update(domain_probs or {})
    return Shape(
        fired_verbs=tuple(vocab.by_name(v) for v in verbs),
        scope_areas=tuple(areas),
        scope_domains=tuple(domains),
        area_probs=ap,
        domain_probs=dp,
        flags={
            f: 0.05
            for f in (
                "collective",
                "has_exception",
                "has_condition",
                "has_timing",
                "is_destructive",
            )
        },
        scene=None,
        condition_domain=None,
    )


def test_strict_intersects_verb_area_domain(home, vocab):
    shape = _shape(home, areas=("kitchen", "living", "hallway"), domains=("light",))
    ids = {e.entity_id for e in strict_candidates(home, vocab.by_name("turn_off"), shape)}
    assert ids == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
        "light.living_main",
        "light.reading_lamp",
        "light.hallway",
    }


def test_strict_respects_verb_applicability(home, vocab):
    shape = _shape(home, verbs=("open",), areas=("living",), domains=())
    ids = {e.entity_id for e in strict_candidates(home, vocab.by_name("open"), shape)}
    assert ids == {"cover.living_blinds"}


def test_strict_empty_domain_means_all_domains_for_verb(home, vocab):
    shape = _shape(home, areas=("kitchen",))
    ids = {e.entity_id for e in strict_candidates(home, vocab.by_name("turn_off"), shape)}
    assert ids == {"light.kitchen_ceiling", "light.kitchen_counter", "switch.fridge"}


def test_ranked_widen_adds_best_area_first(home, vocab):
    shape = _shape(home, areas=(), domains=("light",), area_probs={"living": 0.45, "office": 0.2})
    ids = {e.entity_id for e in ranked_widen(home, vocab.by_name("turn_on"), shape, Trace())}
    assert ids == {"light.living_main", "light.reading_lamp"}


def test_ranked_widen_falls_back_to_domain_then_everything(home, vocab):
    # No area signal: domain light at 0.3, widen to all lights (incl. arealess christmas tree)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    ids = {e.entity_id for e in ranked_widen(home, vocab.by_name("turn_on"), shape, Trace())}
    assert "light.christmas_tree" in ids and "switch.fridge" not in ids


def test_scope_returns_strict_when_non_empty(home, vocab, config):
    shape = _shape(home, areas=("hallway",), domains=("light",))
    r = scope_candidates(home, vocab.by_name("turn_off"), shape, config, Trace())
    assert isinstance(r, Candidates) and not r.widened
    assert [e.entity_id for e in r.entities] == ["light.hallway"]


def test_scope_widens_when_strict_empty(home, vocab, config):
    shape = _shape(home, areas=("garage",), domains=("light",), area_probs={"living": 0.4})
    r = scope_candidates(home, vocab.by_name("turn_off"), shape, config, Trace())
    assert isinstance(r, Candidates) and r.widened
    assert {e.entity_id for e in r.entities} == {"light.living_main", "light.reading_lamp"}


def test_scope_cap_then_device_round_when_enabled(home, vocab):
    cfg = EngineConfig(model="m", scope_cap=2, device_round=True, max_rounds=3)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    r = scope_candidates(home, vocab.by_name("turn_on"), shape, cfg, Trace())
    assert isinstance(r, DeviceRound)
    assert len(r.entities) > 2


def test_scope_cap_then_clarify_when_device_round_disabled(home, vocab):
    cfg = EngineConfig(model="m", scope_cap=2, device_round=False, supports_clarification=True)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    r = scope_candidates(home, vocab.by_name("turn_on"), shape, cfg, Trace())
    assert isinstance(r, Clarify) and r.question_key == "which_area"


def test_scope_cap_then_escalate_when_nothing_else_allowed(home, vocab):
    cfg = EngineConfig(model="m", scope_cap=2, device_round=False, supports_clarification=False)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    r = scope_candidates(home, vocab.by_name("turn_on"), shape, cfg, Trace())
    assert isinstance(r, ScopeEscalate) and r.reason == "scope"


def test_scope_escalates_when_verb_applies_to_nothing(home, vocab, config):
    shape = _shape(home, verbs=("arm",))
    r = scope_candidates(home, vocab.by_name("arm"), shape, config, Trace())
    assert isinstance(r, ScopeEscalate) and r.reason == "scope"


def test_device_label_prefers_device_name(home):
    assert device_label(home.entity_by_id("light.bedroom_left")) == "Bedside lamps"
    assert device_label(home.entity_by_id("light.christmas_tree")) == "Christmas tree"


def test_scope_cap_applies_to_strict_sets_too(home, vocab):
    # domain:light fired, no area — strict, but 9 lights is over a cap of 2.
    cfg = EngineConfig(model="m", scope_cap=2, device_round=False, supports_clarification=True)
    shape = _shape(home, verbs=("turn_on",), domains=("light",))
    r = scope_candidates(home, vocab.by_name("turn_on"), shape, cfg, Trace())
    assert isinstance(r, Clarify) and r.question_key == "which_area"
    assert len(r.candidates) > 2
