"""Hunch: code calculates, Jev judges."""

from hunch.client import (
    DecisionBackendError,
    DecisionClient,
    FakeDecisionClient,
    TypeSafeDecisionClient,
)
from hunch.config import EngineConfig, Thresholds
from hunch.engine import Engine
from hunch.model import Area, Entity, Floor, HomeModel
from hunch.resolution import (
    Action,
    Condition,
    Escalate,
    NeedsClarification,
    NeedsConfirmation,
    Resolution,
    Resolved,
    Trace,
)
from hunch.vocabulary import DEFAULT_VOCABULARY, ChoiceSpec, Risk, ScoreSpec, Verb, Vocabulary

__version__ = "0.1.0"

__all__ = [
    "Action", "Area", "ChoiceSpec", "Condition", "DEFAULT_VOCABULARY", "DecisionBackendError",
    "DecisionClient", "Engine", "EngineConfig", "Entity", "Escalate", "FakeDecisionClient", "Floor",
    "HomeModel", "NeedsClarification", "NeedsConfirmation", "Resolution", "Resolved", "Risk",
    "ScoreSpec", "Thresholds", "Trace", "TypeSafeDecisionClient", "Verb", "Vocabulary",
]
