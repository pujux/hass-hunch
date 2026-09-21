"""One pending confirmation or clarification per conversation id. In memory, 120 s, single use."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hunch import Action, Condition, Entity, PreviousTurn
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
        self._sweep()
        self._turns[conversation_id] = turn

    def _sweep(self) -> None:
        """Drop everything past its TTL. Conversation ids are unbounded and nothing else
        ever removes a turn that was asked about and then abandoned."""
        cutoff = self._clock() - self._ttl
        for cid in [c for c, t in self._turns.items() if t.created < cutoff]:
            del self._turns[cid]

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


class LastTurnStore:
    """What the last completed turn did, per conversation id, for follow-ups ("und im
    Esszimmer"). Kept a few minutes, read without removing, replaced by every completed turn."""

    def __init__(
        self, ttl_seconds: float = 300.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._turns: dict[str, tuple[PreviousTurn, float]] = {}

    def put(self, conversation_id: str, turn: PreviousTurn) -> None:
        now = self._clock()
        self._turns = {k: v for k, v in self._turns.items() if now - v[1] <= self._ttl}
        self._turns[conversation_id] = (turn, now)

    def get(self, conversation_id: str) -> PreviousTurn | None:
        entry = self._turns.get(conversation_id)
        if entry is None or self._clock() - entry[1] > self._ttl:
            self._turns.pop(conversation_id, None)
            return None
        return entry[0]
