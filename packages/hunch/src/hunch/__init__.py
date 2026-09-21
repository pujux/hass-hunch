"""Hunch: code calculates, Jev judges."""

from hunch.client import (
    DecisionBackendError,
    DecisionClient,
    FakeDecisionClient,
    TypeSafeDecisionClient,
)
from hunch.config import EngineConfig, Thresholds
from hunch.engine import Engine
from hunch.loaders import home_from_export
from hunch.model import Area, Entity, Floor, HomeModel
from hunch.phrasing import DE, EN, PHRASEBOOKS, Phrasebook
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
from hunch.resolution import (
    Action,
    Condition,
    Escalate,
    NeedsClarification,
    NeedsConfirmation,
    PreviousTurn,
    Resolution,
    Resolved,
    Trace,
)
from hunch.vocabulary import (
    DEFAULT_VOCABULARY,
    ChoiceSpec,
    Risk,
    ScoreSpec,
    Verb,
    Vocabulary,
    verbs_for_domain,
)

__version__ = "0.1.0"

__all__ = [
    "DE",
    "EN",
    "JSON",
    "PHRASEBOOKS",
    "Phrasebook",
    "Action",
    "Answer",
    "Answers",
    "Area",
    "ChoiceA",
    "ChoiceQ",
    "ChoiceSpec",
    "Condition",
    "DEFAULT_VOCABULARY",
    "DecisionBackendError",
    "DecisionClient",
    "Engine",
    "EngineConfig",
    "Entity",
    "Escalate",
    "FakeDecisionClient",
    "Floor",
    "HomeModel",
    "NeedsClarification",
    "NeedsConfirmation",
    "NoulA",
    "NoulQ",
    "Question",
    "Resolution",
    "PreviousTurn",
    "Resolved",
    "Risk",
    "ScoreA",
    "ScoreQ",
    "ScoreSpec",
    "Thresholds",
    "Trace",
    "TypeSafeDecisionClient",
    "Verb",
    "Vocabulary",
    "home_from_export",
    "verbs_for_domain",
]
