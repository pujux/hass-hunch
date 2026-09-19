"""What the engine returns. No free text anywhere; every variant carries the full trace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from hunch.model import Entity
from hunch.questions import JSON, Answer, Answers, ChoiceA, NoulA, ScoreA
from hunch.vocabulary import Verb


@dataclass(frozen=True)
class TraceEntry:
    round: int
    question_id: str
    answer: Answer


@dataclass(frozen=True)
class ThresholdDecision:
    name: str
    value: float
    threshold: float
    passed: bool


@dataclass
class Trace:
    """Mutable builder; the only non-frozen type in the package.

    Frozen when embedded via to_dict.
    """

    entries: list[TraceEntry] = field(default_factory=list)
    decisions: list[ThresholdDecision] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    input_tokens: list[int | None] = field(default_factory=list)

    def record(self, round: int, answers: Answers) -> None:
        self.models.append(answers.model)
        self.input_tokens.append(answers.input_tokens)
        for qid, ans in answers.answers.items():
            self.entries.append(TraceEntry(round, qid, ans))

    def decide(self, name: str, value: float, threshold: float) -> bool:
        passed = value >= threshold
        self.decisions.append(ThresholdDecision(name, value, threshold, passed))
        return passed

    def note(self, text: str) -> None:
        self.notes.append(text)

    def to_dict(self) -> JSON:
        return {
            "models": list(self.models),
            "entries": [
                {"round": e.round, "question_id": e.question_id, "answer": _answer_dict(e.answer)}
                for e in self.entries
            ],
            "decisions": [asdict(d) for d in self.decisions],
            "notes": list(self.notes),
            "input_tokens": list(self.input_tokens),
        }


def _answer_dict(a: Answer) -> JSON:
    if isinstance(a, NoulA):
        return {"type": "noul", "probability": a.probability}
    if isinstance(a, ChoiceA):
        return {
            "type": "choice",
            "choice": a.choice,
            "confidence": a.confidence,
            "probabilities": dict(a.probabilities),
        }
    if isinstance(a, ScoreA):
        return {
            "type": "score",
            "score": a.score,
            "confidence": a.confidence,
            "probabilities": {str(k): v for k, v in a.probabilities.items()},
        }
    raise TypeError(type(a))


@dataclass(frozen=True)
class Action:
    verb: Verb
    targets: tuple[Entity, ...]
    params: Mapping[str, float | str]


@dataclass(frozen=True)
class Condition:
    subject: Entity
    expected_state: str


@dataclass(frozen=True)
class Resolved:
    actions: tuple[Action, ...]
    condition: Condition | None
    confidence: float
    trace: Trace


@dataclass(frozen=True)
class NeedsConfirmation:
    actions: tuple[Action, ...]
    condition: Condition | None
    reason: str
    trace: Trace


@dataclass(frozen=True)
class NeedsClarification:
    question_key: str  # "which_area" | "which_device"
    candidates: tuple[Entity, ...]
    trace: Trace


@dataclass(frozen=True)
class Escalate:
    # reason: "timing" | "no_intent" | "destructive" | "low_confidence" | "scope"
    #       | "round_budget" | "condition" | "exception"
    #       | "decision_backend_unavailable" | "prompt_invalid"
    reason: str
    partial: tuple[Action, ...]
    trace: Trace


Resolution = Resolved | NeedsConfirmation | NeedsClarification | Escalate
