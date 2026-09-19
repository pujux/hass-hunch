"""Round 2: resolve targets and parameters, only for verbs Round 1 couldn't finish."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from hunch.config import Thresholds
from hunch.model import Entity, HomeModel
from hunch.questions import JSON, ChoiceQ, NoulQ, Question, ScoreQ
from hunch.round1 import Shape
from hunch.scope import device_label
from hunch.vocabulary import ChoiceSpec, ScoreSpec

# Sentinel option appended to every Round 2 Choice so the model can say nothing fits,
# rather than being forced to pick among options that all miss.
NO_MATCH = "none of these"

# Known states per domain for condition questions. Extend as domains are added.
DOMAIN_STATES: dict[str, tuple[str, ...]] = {
    "lock": ("locked", "unlocked", "jammed"),
    "cover": ("open", "closed", "opening", "closing"),
    "light": ("on", "off"),
    "switch": ("on", "off"),
    "fan": ("on", "off"),
    "media_player": ("playing", "paused", "idle", "off"),
    "climate": ("heat", "cool", "auto", "off"),
    "alarm_control_panel": ("armed_home", "armed_away", "armed_night", "disarmed", "triggered"),
    "binary_sensor": ("on", "off"),
    "person": ("home", "not_home"),
}


def _area_name(home: HomeModel, e: Entity) -> str:
    area = home.area_by_id(e.area_id) if e.area_id else None
    return area.name if area else "no area"


@dataclass(frozen=True)
class TargetOption:
    label: str
    entities: tuple[Entity, ...]


def _group_by_device(candidates: tuple[Entity, ...]) -> list[list[Entity]]:
    """Entities grouped by device, in first-seen order. A deviceless entity is its own device."""
    by_device: dict[str, list[Entity]] = {}
    for e in candidates:
        by_device.setdefault(e.device_id or f"entity:{e.entity_id}", []).append(e)
    return list(by_device.values())


def _dedupe(raw: list[TargetOption]) -> tuple[TargetOption, ...]:
    seen: dict[str, int] = {}
    out: list[TargetOption] = []
    for opt in raw:
        n = seen.get(opt.label, 0) + 1
        seen[opt.label] = n
        out.append(opt if n == 1 else TargetOption(f"{opt.label} #{n}", opt.entities))
    return tuple(out)


def target_options(candidates: tuple[Entity, ...]) -> tuple[TargetOption, ...]:
    """One option per *entity*: a multi-entity device is expanded into its entities."""
    raw: list[TargetOption] = []
    for group in _group_by_device(candidates):
        if len(group) == 1:
            raw.append(TargetOption(device_label(group[0]), (group[0],)))
        else:
            raw.extend(TargetOption(f"{device_label(e)} — {e.name}", (e,)) for e in group)
    return _dedupe(raw)


def device_options(candidates: tuple[Entity, ...]) -> tuple[TargetOption, ...]:
    """One option per *device*: choosing it selects all of that device's candidate entities.

    People name devices, not entities, so the narrowing device round offers device names
    only; the per-entity split, if it is still needed, happens in Round 2.
    """
    return _dedupe(
        [TargetOption(device_label(g[0]), tuple(g)) for g in _group_by_device(candidates)]
    )


@dataclass(frozen=True)
class Round2Plan:
    exclude: dict[str, tuple[Entity, ...]] = field(default_factory=dict)
    singular: dict[str, tuple[TargetOption, ...]] = field(default_factory=dict)
    collective: dict[str, tuple[Entity, ...]] = field(default_factory=dict)
    params: tuple[str, ...] = ()
    condition_candidates: tuple[Entity, ...] = ()

    def all_candidates(self) -> tuple[Entity, ...]:
        seen: dict[str, Entity] = {}
        for ents in (*self.exclude.values(), *self.collective.values(), self.condition_candidates):
            for e in ents:
                seen.setdefault(e.entity_id, e)
        for opts in self.singular.values():
            for o in opts:
                for e in o.entities:
                    seen.setdefault(e.entity_id, e)
        return tuple(seen.values())


def plan_round2(
    home: HomeModel,
    shape: Shape,
    per_verb: Mapping[str, tuple[Entity, ...]],
    thresholds: Thresholds,
    scope_cap: int,
) -> Round2Plan:
    collective = shape.flag("collective") >= thresholds.collective
    has_exception = shape.flag("has_exception") >= thresholds.flag
    exclude: dict[str, tuple[Entity, ...]] = {}
    singular: dict[str, tuple[TargetOption, ...]] = {}
    coll: dict[str, tuple[Entity, ...]] = {}
    params: list[str] = []
    for verb in shape.fired_verbs:
        cands = per_verb.get(verb.name, ())
        if not cands:
            continue
        if verb.param is not None:
            params.append(verb.name)
        if collective and not has_exception:
            coll[verb.name] = cands
        elif collective:
            exclude[verb.name] = cands
        else:
            opts = target_options(cands)
            if len(opts) == 1:
                coll[verb.name] = cands
            else:
                singular[verb.name] = opts
    cond: tuple[Entity, ...] = ()
    if shape.condition_domain and shape.flag("has_condition") >= thresholds.flag:
        cond = tuple(e for e in home.entities if e.domain == shape.condition_domain)
        if shape.scope_areas:
            cond = tuple(e for e in cond if e.area_id in shape.scope_areas)
        if len(cond) > scope_cap:
            # Truncating would silently hide the right answer; ask nothing instead and let
            # the engine record that the condition could not be resolved.
            cond = ()
    return Round2Plan(exclude, singular, coll, tuple(params), cond)


def build_round2_state(prompt: str, plan: Round2Plan, home: HomeModel) -> JSON:
    return {
        "request": prompt,
        "candidates": [
            {
                "name": e.name,
                "aliases": list(e.aliases),
                "area": _area_name(home, e),
                "device": e.device_name,
                "type": e.domain,
                "state": e.state,
            }
            for e in plan.all_candidates()
        ],
    }


def build_round2_questions(shape: Shape, plan: Round2Plan, home: HomeModel) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    for verb_name, ents in plan.exclude.items():
        for e in ents:
            qs[f"exclude:{verb_name}:{e.entity_id}"] = NoulQ(
                f"The request in `request` names an exception — something that must NOT be "
                f"affected. Is the candidate named '{e.name}' in area '{_area_name(home, e)}' "
                f"that exception?"
            )
    for verb_name, opts in plan.singular.items():
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        qs[f"target:{verb_name}"] = ChoiceQ(
            f"Which single device does the request want to {verb.phrasing}?",
            tuple(o.label for o in opts) + (NO_MATCH,),
        )
    for verb_name in plan.params:
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        spec = verb.param
        if isinstance(spec, ScoreSpec):
            qs[f"param:{verb_name}"] = ScoreQ(
                f"What {spec.name.replace('_', ' ')} does the request ask for?", spec.levels
            )
        elif isinstance(spec, ChoiceSpec):
            qs[f"param:{verb_name}"] = ChoiceQ(
                f"Which {spec.name.replace('_', ' ')} does the request ask for?", spec.options
            )
    if plan.condition_candidates and shape.condition_domain:
        qs["cond_subject"] = ChoiceQ(
            "Which device is the request's condition about?",
            tuple(o.label for o in target_options(plan.condition_candidates)) + (NO_MATCH,),
        )
        qs["cond_state"] = ChoiceQ(
            "Which state must that device be in for the request's condition to hold?",
            DOMAIN_STATES.get(shape.condition_domain, ("on", "off")),
        )
    return qs
