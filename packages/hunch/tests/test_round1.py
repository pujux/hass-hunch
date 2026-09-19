from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA, NoulQ
from hunch.resolution import Trace
from hunch.round1 import FLAGS, build_round1_questions, build_round1_state, interpret_round1


def test_state_is_small_and_names_only(home):
    state = build_round1_state(home, "turn off the downstairs lights")
    assert state["request"] == "turn off the downstairs lights"
    assert state["floors"] == ["Downstairs", "Upstairs"]
    assert state["areas"] == [
        "Kitchen", "Living room (lounge)", "Hallway", "Bedroom", "Office (study)",
    ]
    assert state["domains"] == ["climate", "cover", "light", "lock", "switch"]
    assert state["scenes"] == ["Movie night", "Goodnight"]
    assert "entities" not in state  # never show Round 1 the entity list


def test_questions_cover_verbs_floors_areas_domains_flags_scene_condition(home, vocab):
    qs = build_round1_questions(home, vocab)
    assert {f"verb:{n}" for n in vocab.names} <= set(qs)
    assert {"floor:downstairs", "floor:upstairs"} <= set(qs)
    assert {"area:kitchen", "area:office"} <= set(qs)
    assert {"domain:light", "domain:lock"} <= set(qs)
    assert {f"flag:{f}" for f in FLAGS} <= set(qs)
    assert isinstance(qs["scene"], ChoiceQ)
    assert qs["scene"].options == ("Movie night", "Goodnight", "none")
    assert isinstance(qs["condition_domain"], ChoiceQ)
    assert qs["condition_domain"].options[-1] == "none"
    assert all(
        isinstance(q, NoulQ)
        for k, q in qs.items()
        if k.startswith(("verb:", "floor:", "area:", "domain:", "flag:"))
    )


def test_area_question_mentions_aliases(home, vocab):
    qs = build_round1_questions(home, vocab)
    assert "lounge" in qs["area:living"].instructions


def test_no_scene_question_when_home_has_no_scenes(home, vocab):
    bare = type(home)(home.floors, home.areas, home.entities, ())
    assert "scene" not in build_round1_questions(bare, vocab)


def _answers(home, vocab, **overrides):
    qs = build_round1_questions(home, vocab)
    base = {}
    for qid, q in qs.items():
        if isinstance(q, ChoiceQ):
            base[qid] = ChoiceA("none", 0.9, {o: (0.9 if o == "none" else 0.0) for o in q.options})
        else:
            base[qid] = NoulA(0.05)
    base.update(overrides)
    return Answers("jev-1.13.0", base, 400)


def test_interpret_floor_expands_to_areas_and_fires_verb(home, vocab, thresholds):
    ans = _answers(home, vocab, **{
        "verb:turn_off": NoulA(0.95),
        "floor:downstairs": NoulA(0.9),
        "domain:light": NoulA(0.9),
        "flag:collective": NoulA(0.9),
    })
    shape = interpret_round1(home, vocab, ans, thresholds, Trace())
    assert [v.name for v in shape.fired_verbs] == ["turn_off"]
    assert set(shape.scope_areas) == {"kitchen", "living", "hallway"}
    assert shape.scope_domains == ("light",)
    assert shape.flag("collective") == 0.9
    assert shape.scene is None and shape.condition_domain is None


def test_interpret_keeps_probabilities_for_ranked_widening(home, vocab, thresholds):
    ans = _answers(home, vocab, **{"verb:turn_on": NoulA(0.9), "area:living": NoulA(0.45)})
    shape = interpret_round1(home, vocab, ans, thresholds, Trace())
    assert shape.scope_areas == ()             # 0.45 < scope_fire
    assert shape.area_probs["living"] == 0.45  # but retained


def test_interpret_scene_and_condition_domain(home, vocab, thresholds):
    ans = _answers(home, vocab, **{
        "verb:activate": NoulA(0.9),
        "scene": ChoiceA(
            "Movie night", 0.85, {"Movie night": 0.85, "Goodnight": 0.1, "none": 0.05}
        ),
        "flag:has_condition": NoulA(0.8),
        "condition_domain": ChoiceA("lock", 0.8, {"lock": 0.8, "none": 0.2}),
    })
    shape = interpret_round1(home, vocab, ans, thresholds, Trace())
    assert shape.scene is not None and shape.scene.entity_id == "scene.movie_night"
    assert shape.condition_domain == "lock"


def test_interpret_ignores_scene_below_confidence(home, vocab, thresholds):
    ans = _answers(home, vocab, **{
        "scene": ChoiceA("Movie night", 0.4, {"Movie night": 0.4, "Goodnight": 0.35, "none": 0.25}),
    })
    assert interpret_round1(home, vocab, ans, thresholds, Trace()).scene is None


def test_interpret_records_trace(home, vocab, thresholds):
    trace = Trace()
    ans = _answers(home, vocab, **{"verb:turn_off": NoulA(0.9)})
    interpret_round1(home, vocab, ans, thresholds, trace)
    assert trace.models == ["jev-1.13.0"]
    assert any(d.name == "verb:turn_off" and d.passed for d in trace.decisions)
