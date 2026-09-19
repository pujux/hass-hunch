"""Backend-neutral question and answer types. The client maps these to SDK types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

JSON = dict[str, Any]


@dataclass(frozen=True)
class NoulQ:
    instructions: str


@dataclass(frozen=True)
class ChoiceQ:
    instructions: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class ScoreQ:
    instructions: str
    levels: tuple[str, ...]


Question = NoulQ | ChoiceQ | ScoreQ


@dataclass(frozen=True)
class NoulA:
    probability: float


@dataclass(frozen=True)
class ChoiceA:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class ScoreA:
    score: float
    confidence: float
    probabilities: Mapping[int, float]


Answer = NoulA | ChoiceA | ScoreA


@dataclass(frozen=True)
class Answers:
    model: str
    answers: Mapping[str, Answer]
    input_tokens: int | None

    def noul(self, question_id: str) -> float:
        a = self.answers[question_id]
        if not isinstance(a, NoulA):
            raise TypeError(f"{question_id} is {type(a).__name__}, expected NoulA")
        return a.probability

    def choice(self, question_id: str) -> ChoiceA:
        a = self.answers[question_id]
        if not isinstance(a, ChoiceA):
            raise TypeError(f"{question_id} is {type(a).__name__}, expected ChoiceA")
        return a

    def score(self, question_id: str) -> ScoreA:
        a = self.answers[question_id]
        if not isinstance(a, ScoreA):
            raise TypeError(f"{question_id} is {type(a).__name__}, expected ScoreA")
        return a
