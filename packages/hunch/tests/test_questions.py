import dataclasses
import json

import pytest
from hunch.config import EngineConfig, Thresholds
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA, NoulQ, ScoreA, ScoreQ
from hunch.resolution import Trace


def test_question_types_are_frozen():
    q = NoulQ("is it on?")
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.instructions = "x"  # type: ignore[misc]
    assert ChoiceQ("pick", ("a", "b")).options == ("a", "b")
    assert ScoreQ("rate", ("low", "high")).levels == ("low", "high")


def test_answers_typed_accessors():
    a = Answers(
        model="jev-1.13.0",
        answers={
            "n": NoulA(0.9),
            "c": ChoiceA("kitchen", 0.8, {"kitchen": 0.8, "none": 0.2}),
            "s": ScoreA(2.4, 0.7, {1: 0.1, 2: 0.4, 3: 0.5}),
        },
        input_tokens=120,
    )
    assert a.noul("n") == 0.9
    assert a.choice("c").choice == "kitchen"
    assert a.score("s").score == 2.4


def test_answers_accessor_type_mismatch_raises():
    a = Answers("m", {"c": ChoiceA("x", 1.0, {"x": 1.0})}, None)
    with pytest.raises(TypeError):
        a.noul("c")


def test_answers_missing_id_raises_keyerror():
    with pytest.raises(KeyError):
        Answers("m", {}, None).noul("nope")


def test_engine_config_requires_model_and_has_defaults():
    cfg = EngineConfig(model="jev-1.13.0")
    assert cfg.max_rounds == 2
    assert cfg.scope_cap == 60
    assert cfg.thresholds == Thresholds()
    with pytest.raises(TypeError):
        EngineConfig()  # type: ignore[call-arg]


def test_trace_records_answers_decisions_and_models():
    t = Trace()
    t.record(1, Answers("jev-1.13.0", {"verb:turn_off": NoulA(0.93)}, 300))
    assert t.decide("verb:turn_off", 0.93, 0.7) is True
    assert t.decide("flag:collective", 0.4, 0.65) is False
    assert t.input_tokens == [300]
    d = t.to_dict()
    assert d["models"] == ["jev-1.13.0"]
    assert d["entries"][0] == {
        "round": 1,
        "question_id": "verb:turn_off",
        "answer": {"type": "noul", "probability": 0.93},
    }
    assert d["decisions"][1] == {
        "name": "flag:collective", "value": 0.4, "threshold": 0.65, "passed": False,
    }
    json.dumps(d)  # must be JSON-serialisable


def test_device_round_requires_a_third_round():
    with pytest.raises(ValueError, match="device_round requires max_rounds >= 3"):
        EngineConfig(model="jev-1.13.0", device_round=True)
    assert EngineConfig(model="jev-1.13.0", device_round=True, max_rounds=3).device_round


def test_public_api_exports_the_documented_names():
    import hunch

    expected = {
        "verbs_for_domain", "Answers", "NoulA", "ChoiceA", "ScoreA",
        "NoulQ", "ChoiceQ", "ScoreQ", "Question", "Answer", "JSON",
    }
    assert expected <= set(hunch.__all__)
    for name in hunch.__all__:
        assert getattr(hunch, name, None) is not None, name
