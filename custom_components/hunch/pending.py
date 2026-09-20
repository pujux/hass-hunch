"""One pending confirmation or clarification per conversation id. In memory, 120 s, single use."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hunch import Action, Condition, Entity
from hunch.vocabulary import Verb


@dataclass(frozen=True)
class PendingConfirm:
    actions: tuple[Action, ...]
    condition: Condition | None
    question: str
    created: float


@dataclass(frozen=True)
class PendingClarify:
    verb: Verb
    params: Mapping[str, float | str]
    candidates: tuple[Entity, ...]
    labels: tuple[str, ...]  # offered to the user and to Jev, parallel to candidates
    question: str
    created: float


class PendingStore:
    def __init__(
        self, ttl_seconds: float = 120.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._turns: dict[str, PendingConfirm | PendingClarify] = {}

    def now(self) -> float:
        return self._clock()

    def put(self, conversation_id: str, turn: PendingConfirm | PendingClarify) -> None:
        self._turns[conversation_id] = turn

    def take(self, conversation_id: str) -> PendingConfirm | PendingClarify | None:
        turn = self._turns.pop(conversation_id, None)
        if turn is None or self._clock() - turn.created > self._ttl:
            return None
        return turn

    def peek_expired(self, conversation_id: str) -> bool:
        """Check if a turn is expired. If so, remove it and return True. Otherwise return False."""
        turn = self._turns.get(conversation_id)
        if turn is None:
            return False
        if self._clock() - turn.created > self._ttl:
            self._turns.pop(conversation_id, None)
            return True
        return False
