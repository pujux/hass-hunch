"""The verb set: what the fast path is allowed to do, and how risky each thing is."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from functools import cached_property


class Risk(Enum):
    SAFE = auto()         # auto-executes above threshold
    CONFIRM = auto()      # always asks first
    DESTRUCTIVE = auto()  # never executes from the fast path; escalates


@dataclass(frozen=True)
class ScoreSpec:
    name: str                  # parameter key in Action.params
    levels: tuple[str, ...]    # 2..10 ordered descriptions; index maps to a value via `values`
    values: tuple[float, ...]  # same length as levels; the number each level stands for


@dataclass(frozen=True)
class ChoiceSpec:
    name: str
    options: tuple[str, ...]


ParamSpec = ScoreSpec | ChoiceSpec


@dataclass(frozen=True)
class Verb:
    name: str
    domains: frozenset[str]
    param: ParamSpec | None
    risk: Risk
    intent: str
    phrasing: str
    is_query: bool = False


@dataclass(frozen=True)
class Vocabulary:
    verbs: tuple[Verb, ...]

    def by_name(self, name: str) -> Verb:
        return self._by_name[name]

    @cached_property
    def names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.verbs)

    @cached_property
    def _by_name(self) -> dict[str, Verb]:
        return {v.name: v for v in self.verbs}


def verbs_for_domain(domain: str, vocabulary: Vocabulary) -> frozenset[str]:
    return frozenset(v.name for v in vocabulary.verbs if domain in v.domains or v.is_query)


_ON_OFF = frozenset({"light", "switch", "fan", "media_player", "climate"})
_BRIGHTNESS = ScoreSpec(
    "brightness_pct",
    ("off", "very dim", "dim", "medium", "bright", "full"),
    (0, 10, 25, 50, 75, 100),
)
_POSITION = ScoreSpec(
    "position",
    ("fully closed", "mostly closed", "half open", "mostly open", "fully open"),
    (0, 25, 50, 75, 100),
)
_TEMPERATURE = ScoreSpec(
    "temperature",
    ("cold (16°C)", "cool (18°C)", "mild (20°C)", "warm (22°C)", "hot (24°C)"),
    (16, 18, 20, 22, 24),
)
_VOLUME = ScoreSpec(
    "volume_level",
    ("mute", "quiet", "medium", "loud", "max"),
    (0.0, 0.2, 0.5, 0.8, 1.0),
)

DEFAULT_VOCABULARY = Vocabulary(
    (
        Verb("turn_on", _ON_OFF, None, Risk.SAFE, "HassTurnOn", "turn something on"),
        Verb("turn_off", _ON_OFF, None, Risk.SAFE, "HassTurnOff", "turn something off"),
        Verb("set_brightness", frozenset({"light"}), _BRIGHTNESS, Risk.SAFE,
             "HassLightSet", "set how bright a light is"),
        Verb("open", frozenset({"cover"}), None, Risk.SAFE, "HassOpenCover",
             "open blinds, shades, curtains or a garage door"),
        Verb("close", frozenset({"cover"}), None, Risk.SAFE, "HassCloseCover",
             "close blinds, shades, curtains or a garage door"),
        Verb("set_position", frozenset({"cover"}), _POSITION, Risk.SAFE, "HassSetPosition",
             "set how far open blinds, shades or curtains are"),
        Verb("lock", frozenset({"lock"}), None, Risk.CONFIRM, "HassLock", "lock a door or lock"),
        Verb("unlock", frozenset({"lock"}), None, Risk.CONFIRM, "HassUnlock",
             "unlock a door or lock"),
        Verb("set_temperature", frozenset({"climate"}), _TEMPERATURE, Risk.SAFE,
             "HassClimateSetTemperature", "set a target temperature or make it warmer or cooler"),
        Verb("set_volume", frozenset({"media_player"}), _VOLUME, Risk.SAFE,
             "HassSetVolume", "change the volume of a speaker or TV"),
        Verb("media_pause", frozenset({"media_player"}), None, Risk.SAFE, "HassMediaPause",
             "pause playback"),
        Verb("media_play", frozenset({"media_player"}), None, Risk.SAFE, "HassMediaUnpause",
             "resume or start playback"),
        Verb("arm", frozenset({"alarm_control_panel"}), None, Risk.CONFIRM,
             "HassAlarmArm", "arm the alarm system"),
        Verb("disarm", frozenset({"alarm_control_panel"}), None, Risk.CONFIRM,
             "HassAlarmDisarm", "disarm the alarm system"),
        Verb("activate", frozenset({"scene", "script"}), None, Risk.SAFE, "HassTurnOn",
             "activate a scene or run a script by name"),
        Verb("query_state", frozenset(), None, Risk.SAFE, "HassGetState",
             "ask whether something is on, off, open, closed, locked, or what its value is",
             is_query=True),
    )
)
