"""Tunable thresholds and engine settings.

Defaults are starting points, tuned from the golden corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Thresholds:
    verb_fire: float = 0.7
    scope_fire: float = 0.7  # floor / area / domain Nouls
    collective: float = 0.5
    target_choice_conf: float = 0.7
    auto_execute: float = 0.75
    confirm_band: float = 0.5  # [confirm_band, auto_execute) -> NeedsConfirmation
    flag: float = 0.6
    specific_device: float = 0.7  # names_specific flag: plural-looking name of ONE device
    collective_fallback: float = (
        0.4  # no-match target + collective >= this: all candidates, confirm
    )
    no_match_clarify: float = 0.6  # no-match target below this confidence: clarify, not escalate


@dataclass(frozen=True)
class EngineConfig:
    # Pinned Jev model id, e.g. "jev-1.13.0". A response from any other model is rejected.
    model: str
    # Probability/confidence gates; see Thresholds above.
    thresholds: Thresholds = field(default_factory=Thresholds)
    # Budget of backend round trips per request. Round 1 always costs one; a device round
    # and Round 2 cost one each, so device_round requires >= 3. Overrunning it escalates
    # as "round_budget" rather than guessing.
    max_rounds: int = 2
    # A collective action over more entities than this always asks for confirmation.
    max_silent_targets: int = 20
    # Largest candidate set a Round 2 Choice may be built over. Beyond it accuracy decays
    # (context rot), so the request falls through to a device round, clarification or
    # escalation instead.
    scope_cap: int = 60
    # Spend an extra round narrowing an over-cap set by device name before giving up.
    device_round: bool = False
    # Whether the caller can hold the turn open to ask a follow-up question. When false,
    # what would have been a NeedsClarification escalates instead.
    supports_clarification: bool = True
    clarify_max_candidates: int = 5  # candidates offered in a NeedsClarification
    # Prompts longer than this escalate unread as "prompt_invalid".
    max_prompt_chars: int = 500

    def __post_init__(self) -> None:
        # A device round is a round: Round 1 + device round + Round 2 needs a budget of 3.
        if self.device_round and self.max_rounds < 3:
            raise ValueError("device_round requires max_rounds >= 3")
