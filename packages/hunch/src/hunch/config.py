"""Tunable thresholds and engine settings.

Defaults are starting points, tuned from the golden corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Thresholds:
    verb_fire: float = 0.7
    scope_fire: float = 0.7         # floor / area / domain Nouls
    collective: float = 0.5
    target_choice_conf: float = 0.7
    auto_execute: float = 0.75
    confirm_band: float = 0.5       # [confirm_band, auto_execute) -> NeedsConfirmation
    flag: float = 0.6               # has_exception/condition/is_query/has_timing/is_destructive


@dataclass(frozen=True)
class EngineConfig:
    model: str                      # pinned Jev model id, e.g. "jev-1.13.0"
    thresholds: Thresholds = field(default_factory=Thresholds)
    max_rounds: int = 2
    latency_budget_ms: int = 600
    request_timeout_ms: int = 1500
    max_silent_targets: int = 20
    scope_cap: int = 60
    device_round: bool = False
    supports_clarification: bool = True
    max_prompt_chars: int = 500

    def __post_init__(self) -> None:
        # A device round is a round: Round 1 + device round + Round 2 needs a budget of 3.
        if self.device_round and self.max_rounds < 3:
            raise ValueError("device_round requires max_rounds >= 3")
