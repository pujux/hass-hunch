"""Exclusive verb groups, widened-candidate sweep guard, area-qualified duplicate labels,
and stem-vs-full area matching (2026-09-20, second real-home pass)."""

from hunch.model import Area, Entity, HomeModel
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA
from hunch.resolution import Trace
from hunch.round1 import FLAGS, Shape, build_round1_questions, interpret_round1
from hunch.round2 import build_round2_questions, plan_round2, target_options
from hunch.scope import verbatim_areas
from hunch.vocabulary import DEFAULT_VOCABULARY as V
from hunch.vocabulary import EXCLUSIVE_GROUPS


def test_exclusive_groups_cover_the_contradictory_pairs():
    flat = set().union(*EXCLUSIVE_GROUPS)
    assert {"open", "close", "set_position", "turn_on", "turn_off", "lock", "unlock"} <= flat


def test_only_the_strongest_verb_of_an_exclusive_group_fires(home, vocab, thresholds):
    qs = build_round1_questions(home, vocab)
    ans = {}
    for qid, q in qs.items():
        ans[qid] = ChoiceA("none", 0.9, {}) if isinstance(q, ChoiceQ) else NoulA(0.05)
    ans["verb:open"] = NoulA(0.93)
    ans["verb:close"] = NoulA(0.80)  # "zu"/"auf" confusion: open and close cannot both be meant
    ans["verb:set_position"] = NoulA(0.75)
    ans["verb:turn_on"] = NoulA(0.73)  # a different group: survives (its candidates decide later)
    t = Trace()
    shape = interpret_round1(home, vocab, Answers("m", ans, None), thresholds, t)
    assert sorted(v.name for v in shape.fired_verbs) == ["open", "turn_on"]
    assert "verb_conflict:close<open" in t.notes and "verb_conflict:set_position<open" in t.notes


def _two_rooms_home():
    e = lambda eid, area, dev: Entity(
        eid, "binary_sensor", "Tür", (), area, f"dev_{area}", dev, frozenset({"query_state"}), "off"
    )
    return HomeModel(
        floors=(),
        areas=(
            Area("galerie", "Galerie", (), None),
            Area("schlafzimmer", "Schlafzimmer", (), None),
        ),
        entities=(
            e("binary_sensor.ga", "galerie", "Dachterrassentür"),
            e("binary_sensor.sz", "schlafzimmer", "Dachterrassentür"),
        ),
        scenes=(),
    )


def test_duplicate_labels_are_qualified_by_area_not_by_number():
    home = _two_rooms_home()
    names = {a.area_id: a.name for a in home.areas}
    labels = [o.label for o in target_options(home.entities, names)]
    assert labels == ["Dachterrassentür (Galerie)", "Dachterrassentür (Schlafzimmer)"]
    assert [o.label for o in target_options(home.entities)] == [
        "Dachterrassentür",
        "Dachterrassentür #2",
    ]


def test_plan_uses_area_qualified_labels_in_the_target_question(thresholds):
    home = _two_rooms_home()
    f = {k: 0.05 for k in FLAGS}
    shape = Shape((V.by_name("query_state"),), (), ("binary_sensor",), {}, {}, f, None, None)
    plan = plan_round2(
        home,
        shape,
        {"query_state": home.entities},
        thresholds,
        60,
        prompt="Ist die Dachterrassentür offen?",
    )
    q = build_round2_questions(shape, plan, home)["target:query_state"]
    assert q.options[:2] == ("Dachterrassentür (Galerie)", "Dachterrassentür (Schlafzimmer)")


def test_stem_match_yields_to_a_full_area_name():
    home = HomeModel(
        floors=(),
        areas=(Area("bo", "Badezimmer Oben", (), None), Area("bu", "Badezimmer Unten", (), None)),
        entities=(),
        scenes=(),
    )
    assert verbatim_areas(home, "Lüfter im Badezimmer unten einschalten") == ("bu",)
    assert verbatim_areas(home, "Badezimmer Rollos auf 15%") == ("bo", "bu")


def test_same_name_in_two_areas_with_no_area_named_clarifies_even_on_a_confident_pick(thresholds):
    from hunch.config import EngineConfig
    from hunch.questions import Answers, ChoiceA
    from hunch.resolution import NeedsClarification
    from hunch.resolver import resolve

    home = _two_rooms_home()
    f = {k: 0.05 for k in FLAGS}
    shape = Shape((V.by_name("query_state"),), (), ("binary_sensor",), {}, {}, f, None, None)
    plan = plan_round2(
        home,
        shape,
        {"query_state": home.entities},
        thresholds,
        60,
        prompt="Ist die Dachterrassentür offen?",
    )
    assert plan.ambiguous_by_area == ("query_state",)
    t = Trace()
    t.decide("verb:query_state", 0.98, 0.7)
    r2 = Answers(
        "m",
        {
            "target:query_state": ChoiceA(
                "Dachterrassentür (Galerie)", 0.97, {"Dachterrassentür (Galerie)": 0.97}
            )
        },
        None,
    )
    r = resolve(shape, plan, r2, EngineConfig(model="m"), t)
    assert isinstance(r, NeedsClarification) and len(r.candidates) == 2
