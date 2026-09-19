"""Backend boundary. Everything network-shaped lives here and nowhere else."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    Noul,
    RetryPolicy,
    Score,
    TypeSafeError,
)

from hunch.questions import (
    JSON,
    Answer,
    Answers,
    ChoiceA,
    ChoiceQ,
    NoulA,
    NoulQ,
    Question,
    ScoreA,
    ScoreQ,
)


class DecisionBackendError(Exception):
    def __init__(self, reason: str, cause: BaseException | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.__cause__ = cause


class DecisionClient(Protocol):
    async def ask(self, state: JSON, questions: Mapping[str, Question]) -> Answers: ...


ScriptFn = Callable[[JSON, Mapping[str, Question]], Mapping[str, Answer]]


class FakeDecisionClient:
    """Scripted answers for tests.

    Missing ids raise KeyError so unexpected questions fail loudly.
    """

    def __init__(self, script: Mapping[str, Answer] | ScriptFn, model: str = "fake") -> None:
        self._script = script
        self._model = model
        self.calls: list[tuple[JSON, dict[str, Question]]] = []

    async def ask(self, state: JSON, questions: Mapping[str, Question]) -> Answers:
        self.calls.append((state, dict(questions)))
        if callable(self._script):
            answers = dict(self._script(state, questions))
        else:
            answers = {qid: self._script[qid] for qid in questions}
        return Answers(model=self._model, answers=answers, input_tokens=None)


def to_sdk_question(q: Question) -> Noul | Choice | Score:
    if isinstance(q, NoulQ):
        return Noul(instructions=q.instructions)
    if isinstance(q, ChoiceQ):
        return Choice(instructions=q.instructions, criteria={o: None for o in q.options})
    if isinstance(q, ScoreQ):
        return Score(instructions=q.instructions, criteria=list(q.levels))
    raise TypeError(type(q))


def from_sdk_answer(a: Any) -> Answer:
    kind = getattr(a, "type", None)
    if kind == "noul":
        return NoulA(float(a.noul))
    if kind == "choice":
        return ChoiceA(str(a.choice), float(a.confidence), dict(a.probabilities))
    if kind == "score":
        probabilities = {int(k): float(v) for k, v in a.probabilities.items()}
        return ScoreA(float(a.score), float(a.confidence), probabilities)
    raise DecisionBackendError("malformed_response")


class TypeSafeDecisionClient:
    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        timeout_ms: int = 1500,
        sdk_client: Any | None = None,
    ) -> None:
        self._model = model
        timeout_s = timeout_ms / 1000
        self._sdk = sdk_client or AsyncTypeSafeClient(
            api_key=api_key,
            model=model,
            timeout=timeout_s,
            retry=RetryPolicy(max_retries=2, timeout=timeout_s),
        )

    async def ask(self, state: JSON, questions: Mapping[str, Question]) -> Answers:
        sdk_questions = {qid: to_sdk_question(q) for qid, q in questions.items()}
        try:
            resp = await self._sdk.system_one(
                state=state, questions=sdk_questions, model=self._model
            )
        except TypeSafeError as exc:
            raise DecisionBackendError("decision_backend_unavailable", exc) from exc
        if resp.model != self._model:
            raise DecisionBackendError("model_mismatch")
        answers = {qid: from_sdk_answer(a) for qid, a in resp.answers.items()}
        if set(answers) != set(questions):
            # A partial or over-full answer set would surface downstream as a KeyError deep
            # inside interpretation; fail here so the engine can degrade cleanly instead.
            raise DecisionBackendError("malformed_response")
        usage = getattr(resp, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        return Answers(model=resp.model, answers=answers, input_tokens=input_tokens)

    async def aclose(self) -> None:
        aclose = getattr(self._sdk, "aclose", None)
        if aclose:
            await aclose()
