"""Verb -> Home Assistant service. Pure; no HA imports."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from hunch import Action

HOMEASSISTANT = "homeassistant"


@dataclass(frozen=True)
class ServiceCall:
    domain: str
    service: str
    data: dict[str, Any]


def _int(name: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return lambda p: {name: int(round(float(p[name])))} if name in p else {}


def _float(name: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return lambda p: {name: float(p[name])} if name in p else {}


def _volume(p: Mapping[str, Any]) -> dict[str, Any]:
    return {"volume_level": float(p["volume_level"]) / 100.0} if "volume_level" in p else {}


def _none(p: Mapping[str, Any]) -> dict[str, Any]:
    return {}


# verb -> (service domain or None = the target's own domain, service, data builder)
SERVICE_MAP: dict[str, tuple[str | None, str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    "turn_on": (HOMEASSISTANT, "turn_on", _none),
    "turn_off": (HOMEASSISTANT, "turn_off", _none),
    "set_brightness": ("light", "turn_on", _int("brightness_pct")),
    "open": ("cover", "open_cover", _none),
    "close": ("cover", "close_cover", _none),
    "set_position": ("cover", "set_cover_position", _int("position")),
    "lock": ("lock", "lock", _none),
    "unlock": ("lock", "unlock", _none),
    "set_temperature": ("climate", "set_temperature", _float("temperature")),
    "set_volume": ("media_player", "volume_set", _volume),
    "media_play": ("media_player", "media_play", _none),
    "media_pause": ("media_player", "media_pause", _none),
    "arm": ("alarm_control_panel", "alarm_arm_away", _none),  # the engine has no arming mode
    "disarm": ("alarm_control_panel", "alarm_disarm", _none),
    "activate": (None, "turn_on", _none),  # scene.turn_on / script.turn_on by target domain
}


def plan_calls(action: Action) -> list[ServiceCall]:
    if action.verb.is_query:
        return []
    domain, service, build = SERVICE_MAP[action.verb.name]
    extra = build(action.params)
    if domain is not None:
        return [
            ServiceCall(
                domain, service, {"entity_id": [e.entity_id for e in action.targets], **extra}
            )
        ]
    groups: dict[str, list[str]] = defaultdict(list)
    for e in action.targets:
        groups[e.domain].append(e.entity_id)
    return [ServiceCall(d, service, {"entity_id": ids, **extra}) for d, ids in groups.items()]
