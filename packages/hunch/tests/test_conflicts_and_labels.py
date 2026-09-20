"""Exclusive verb groups, widened-candidate sweep guard, area-qualified duplicate labels,
and stem-vs-full area matching (2026-09-20, second real-home pass)."""

from hunch.model import Area, Entity, HomeModel
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA
from hunch.resolution import Trace
from hunch.round1 import FLAGS, Shape, build_round1_questions, interpret_round1
from hunch.round2 import build_round2_questions, plan_round2, target_options
from hunch.scope import verbatim_areas
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def _two_rooms_home():
    def e(eid, area, dev):
        return Entity(
            eid,
            "binary_sensor",
            "Tür",
            (),
            area,
            f"dev_{area}",
            dev,
            frozenset({"query_state"}),
            "off",
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


def _round1_answers(home, vocab, **over):
    qs = build_round1_questions(home, vocab)
    ans = {}
    for qid, q in qs.items():
        if qid in ("verb_primary", "area_primary"):
            ans[qid] = ChoiceA("several", 0.9, {})
        elif isinstance(q, ChoiceQ):
            ans[qid] = ChoiceA("none", 0.9, {})
        else:
            ans[qid] = NoulA(0.05)
    ans.update(over)
    return Answers("m", ans, None)


def test_verb_choice_drops_co_firing_echoes(home, vocab, thresholds):
    # "Rollo in der Küche auf": open AND turn_on both pass the Noul bar; Jev's comparison says open.
    t = Trace()
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:open": NoulA(0.93),
                "verb:turn_on": NoulA(0.73),
                "verb_primary": ChoiceA("open", 0.98, {"open": 0.98}),
            },
        ),
        thresholds,
        t,
    )
    assert [v.name for v in shape.fired_verbs] == ["open"] and shape.primary_verb == "open"
    assert "verb_conflict:turn_on<open" in t.notes


def test_verb_choice_promotes_a_verb_the_nouls_left_under_the_bar(home, vocab, thresholds):
    # "turn everything off": no Noul reaches 0.7, the comparison says turn_off at 0.86.
    t = Trace()
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:turn_off": NoulA(0.4),
                "verb:close": NoulA(0.48),
                "verb_primary": ChoiceA("turn_off", 0.86, {"turn_off": 0.86}),
            },
        ),
        thresholds,
        t,
    )
    assert [v.name for v in shape.fired_verbs] == ["turn_off"]
    assert "verb_promoted:turn_off" in t.notes


def test_verb_choice_none_means_no_device_action(home, vocab, thresholds):
    # "Wie spät ist es?": query_state half-fires, the comparison says none.
    t = Trace()
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:query_state": NoulA(0.72),
                "verb_primary": ChoiceA("none", 0.91, {"none": 0.91}),
            },
        ),
        thresholds,
        t,
    )
    assert shape.fired_verbs == ()


def test_verb_choice_several_keeps_the_noul_set(home, vocab, thresholds):
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:turn_off": NoulA(0.9),
                "verb:close": NoulA(0.9),
                "verb_primary": ChoiceA("several", 1.0, {}),
            },
        ),
        thresholds,
        Trace(),
    )
    assert sorted(v.name for v in shape.fired_verbs) == ["close", "turn_off"]


def test_area_choice_singles_out_a_room_and_none_clears_guesses(home, vocab, thresholds):
    # Noul fires the bedroom and the upstairs floor; Jev's comparison says Bedroom.
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:close": NoulA(0.9),
                "area:bedroom": NoulA(0.99),
                "floor:upstairs": NoulA(0.72),
                "area_primary": ChoiceA("Bedroom", 0.99, {}),
            },
        ),
        thresholds,
        Trace(),
    )
    assert shape.scope_areas == ("bedroom",)
    # "Licht aus": two rooms half-fire, the comparison says none.
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:turn_off": NoulA(0.9),
                "area:bedroom": NoulA(0.75),
                "area:office": NoulA(0.72),
                "area_primary": ChoiceA("none", 0.7, {}),
            },
        ),
        thresholds,
        Trace(),
    )
    assert shape.scope_areas == ()
    # "alles aus": whole home.
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:turn_off": NoulA(0.9),
                "area:kitchen": NoulA(0.85),
                "area:living": NoulA(0.8),
                "area_primary": ChoiceA("whole home", 0.9, {}),
            },
        ),
        thresholds,
        Trace(),
    )
    assert shape.scope_areas == () and shape.whole_home is True
    # a floor picked by name scopes its areas
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:turn_on": NoulA(0.9),
                "area_primary": ChoiceA("Downstairs", 0.95, {}),
            },
        ),
        thresholds,
        Trace(),
    )
    assert set(shape.scope_areas) == {"kitchen", "living", "hallway"}


def test_verb_promoted_when_noul_and_comparison_agree_without_certainty(home, vocab, thresholds):
    # "Mach alles aus": turn_off Noul 0.65, comparison turn_off 0.35 — two judgments agree.
    t = Trace()
    shape = interpret_round1(
        home,
        vocab,
        _round1_answers(
            home,
            vocab,
            **{
                "verb:turn_off": NoulA(0.65),
                "verb_primary": ChoiceA("turn_off", 0.35, {"turn_off": 0.35}),
            },
        ),
        thresholds,
        t,
    )
    assert [v.name for v in shape.fired_verbs] == ["turn_off"]
    assert any(d.name == "verb:turn_off" and abs(d.value - 0.65) < 1e-9 for d in t.decisions)
