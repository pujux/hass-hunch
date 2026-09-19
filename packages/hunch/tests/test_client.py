from types import SimpleNamespace

import pytest
from hunch.client import (
    DecisionBackendError,
    FakeDecisionClient,
    TypeSafeDecisionClient,
    from_sdk_answer,
    to_sdk_question,
)
from hunch.questions import ChoiceA, ChoiceQ, NoulA, NoulQ, ScoreA, ScoreQ
from typesafe_sdk import Choice, Noul, Score, TypeSafeRateLimitError


def test_to_sdk_question_maps_all_three_primitives():
    n = to_sdk_question(NoulQ("is it on?"))
    c = to_sdk_question(ChoiceQ("which?", ("a", "b")))
    s = to_sdk_question(ScoreQ("how bright?", ("dim", "bright")))
    assert isinstance(n, Noul) and n.instructions == "is it on?"
    assert isinstance(c, Choice) and set(c.criteria) == {"a", "b"}
    assert isinstance(s, Score) and list(s.criteria) == ["dim", "bright"]


def test_from_sdk_answer_maps_all_three_primitives():
    assert from_sdk_answer(SimpleNamespace(type="noul", noul=0.9)) == NoulA(0.9)
    assert from_sdk_answer(
        SimpleNamespace(
            type="choice", choice="a", confidence=0.8, probabilities={"a": 0.8, "b": 0.2}
        )
    ) == ChoiceA("a", 0.8, {"a": 0.8, "b": 0.2})
    assert from_sdk_answer(
        SimpleNamespace(type="score", score=1.5, confidence=0.6, probabilities={1: 0.5, 2: 0.5})
    ) == ScoreA(1.5, 0.6, {1: 0.5, 2: 0.5})


def test_from_sdk_answer_rejects_unknown_type():
    with pytest.raises(DecisionBackendError):
        from_sdk_answer(SimpleNamespace(type="poem"))


async def test_fake_client_returns_script_and_records_calls():
    fake = FakeDecisionClient({"verb:turn_off": NoulA(0.9)})
    out = await fake.ask({"request": "off"}, {"verb:turn_off": NoulQ("x")})
    assert out.noul("verb:turn_off") == 0.9
    assert out.model == "fake"
    assert fake.calls == [({"request": "off"}, {"verb:turn_off": NoulQ("x")})]


async def test_fake_client_fails_loudly_on_unscripted_question():
    fake = FakeDecisionClient({})
    with pytest.raises(KeyError):
        await fake.ask({}, {"verb:turn_off": NoulQ("x")})


async def test_fake_client_accepts_callable_script():
    fake = FakeDecisionClient(lambda state, qs: {qid: NoulA(0.5) for qid in qs})
    out = await fake.ask({}, {"a": NoulQ("a"), "b": NoulQ("b")})
    assert out.noul("a") == 0.5 and out.noul("b") == 0.5


class _StubSDK:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def system_one(self, *, state, questions, model, timeout=None):
        self.calls.append((state, questions, model))
        if self.error:
            raise self.error
        return self.response


def _resp(model="jev-1.13.0"):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(input_tokens=42),
        answers={"q": SimpleNamespace(type="noul", noul=0.77)},
    )


async def test_typesafe_client_round_trips_and_pins_model():
    sdk = _StubSDK(response=_resp())
    client = TypeSafeDecisionClient(model="jev-1.13.0", sdk_client=sdk)
    out = await client.ask({"request": "hi"}, {"q": NoulQ("x")})
    assert out.noul("q") == 0.77 and out.model == "jev-1.13.0" and out.input_tokens == 42
    assert sdk.calls[0][2] == "jev-1.13.0"
    assert isinstance(sdk.calls[0][1]["q"], Noul)


async def test_typesafe_client_rejects_model_mismatch():
    stub = _StubSDK(response=_resp("jev-1.14.0"))
    client = TypeSafeDecisionClient(model="jev-1.13.0", sdk_client=stub)
    with pytest.raises(DecisionBackendError) as ei:
        await client.ask({}, {"q": NoulQ("x")})
    assert ei.value.reason == "model_mismatch"


async def test_typesafe_client_wraps_sdk_errors():
    err = TypeSafeRateLimitError.__new__(TypeSafeRateLimitError)
    client = TypeSafeDecisionClient(model="jev-1.13.0", sdk_client=_StubSDK(error=err))
    with pytest.raises(DecisionBackendError) as ei:
        await client.ask({}, {"q": NoulQ("x")})
    assert ei.value.reason == "decision_backend_unavailable"
