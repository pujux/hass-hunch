"""Round 2: resolve targets and parameters, only for verbs Round 1 couldn't finish."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from hunch.config import Thresholds
from hunch.model import Entity, HomeModel
from hunch.phrasing import EN, Phrasebook
from hunch.questions import JSON, ChoiceQ, NoulQ, Question, ScoreQ
from hunch.round1 import Shape
from hunch.scope import device_label, verbatim_matches
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


def _dedupe(
    raw: list[TargetOption], area_names: Mapping[str, str] | None = None
) -> tuple[TargetOption, ...]:
    """Identical labels get the area in brackets when the duplicates sit in different areas
    ("Dachterrassentür (Galerie)"), so the model has something real to choose on; otherwise #n."""
    counts: dict[str, int] = {}
    for opt in raw:
        counts[opt.label] = counts.get(opt.label, 0) + 1
    seen: dict[str, int] = {}
    out: list[TargetOption] = []
    for opt in raw:
        if counts[opt.label] == 1:
            out.append(opt)
            continue
        area_id = opt.entities[0].area_id if opt.entities else None
        area = (area_names or {}).get(area_id or "", None)
        dups_share_area = len({o.entities[0].area_id for o in raw if o.label == opt.label}) == 1
        if area and not dups_share_area:
            out.append(TargetOption(f"{opt.label} ({area})", opt.entities))
        else:
            n = seen.get(opt.label, 0) + 1
            seen[opt.label] = n
            out.append(opt if n == 1 else TargetOption(f"{opt.label} #{n}", opt.entities))
    return tuple(out)


def target_options(
    candidates: tuple[Entity, ...], area_names: Mapping[str, str] | None = None
) -> tuple[TargetOption, ...]:
    """One option per *entity*: a multi-entity device is expanded into its entities."""
    raw: list[TargetOption] = []
    for group in _group_by_device(candidates):
        if len(group) == 1:
            raw.append(TargetOption(device_label(group[0]), (group[0],)))
        else:
            raw.extend(TargetOption(f"{device_label(e)} — {e.name}", (e,)) for e in group)
    return _dedupe(raw, area_names)


def device_options(
    candidates: tuple[Entity, ...], area_names: Mapping[str, str] | None = None
) -> tuple[TargetOption, ...]:
    """One option per *device*: choosing it selects all of that device's candidate entities.

    People name devices, not entities, so the narrowing device round offers device names
    only; the per-entity split, if it is still needed, happens in Round 2.
    """
    return _dedupe(
        [TargetOption(device_label(g[0]), tuple(g)) for g in _group_by_device(candidates)],
        area_names,
    )


@dataclass(frozen=True)
class Round2Plan:
    exclude: dict[str, tuple[Entity, ...]] = field(default_factory=dict)
    singular: dict[str, tuple[TargetOption, ...]] = field(default_factory=dict)
    collective: dict[str, tuple[Entity, ...]] = field(default_factory=dict)
    params: tuple[str, ...] = ()
    condition_candidates: tuple[Entity, ...] = ()
    # verbs whose collective sweep was narrowed by a verbatim device name in the prompt
    name_matched: tuple[str, ...] = ()
    # verb -> {param: value} read verbatim from the prompt (an explicit number beats a rubric)
    explicit: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    # singular verbs where a no-match may sweep the named area/floor
    scoped_sweep_ok: tuple[str, ...] = ()
    # singular verbs with no area and no name: a no-match may sweep the domain, with confirmation
    domain_sweep_ok: tuple[str, ...] = ()
    # options offered for the condition subject (labels must match the question exactly)
    condition_options: tuple[TargetOption, ...] = ()
    # singular verbs whose options differ only by area while the prompt names no area: ask
    ambiguous_by_area: tuple[str, ...] = ()

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


_PERCENT = re.compile(r"(?<![\d.,])(\d{1,3})\s*(?:%|prozent\b|percent\b)", re.I)
_DEGREES = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d)?)\s*(?:°|grad\b|degrees?\b|celsius\b)", re.I)


def extract_explicit(spec: ScoreSpec, prompt: str) -> float | None:
    """Code calculates: an explicit number with a unit in the prompt beats a rubric judgement.
    Exactly one match is required; several numbers are ambiguous and fall back to the Score."""
    if spec.kind == "percent":
        hits = _PERCENT.findall(prompt)
        return min(100.0, float(hits[0])) if len(hits) == 1 else None
    if spec.kind == "fraction":
        hits = _PERCENT.findall(prompt)
        return min(1.0, float(hits[0]) / 100.0) if len(hits) == 1 else None
    if spec.kind == "degrees":
        hits = _DEGREES.findall(prompt)
        return float(hits[0].replace(",", ".")) if len(hits) == 1 else None
    return None


def _verbatim_named(cands: tuple[Entity, ...], prompt: str) -> Entity | None:
    """Exactly one candidate named verbatim in the prompt, else None (see scope.verbatim_matches)."""
    hits = verbatim_matches(cands, prompt)
    return hits[0] if len(hits) == 1 else None


def plan_round2(
    home: HomeModel,
    shape: Shape,
    per_verb: Mapping[str, tuple[Entity, ...]],
    thresholds: Thresholds,
    scope_cap: int,
    prompt: str = "",
    widened: frozenset[str] = frozenset(),
) -> Round2Plan:
    collective = shape.flag("collective") >= thresholds.collective
    has_exception = shape.flag("has_exception") >= thresholds.flag
    if (
        collective
        and not has_exception  # "everything except the fridge" names a device to EXCLUDE
        and shape.flag("names_specific") >= thresholds.specific_device
    ):
        collective = False  # a plural-looking name of one device: pick it, don't sweep the scope
    area_names = {a.area_id: a.name for a in home.areas}
    exclude: dict[str, tuple[Entity, ...]] = {}
    singular: dict[str, tuple[TargetOption, ...]] = {}
    coll: dict[str, tuple[Entity, ...]] = {}
    params: list[str] = []
    name_matched: list[str] = []
    explicit: dict[str, dict[str, float]] = {}
    scoped_sweep_ok: list[str] = []
    domain_sweep_ok: list[str] = []
    ambiguous_by_area: list[str] = []
    for verb in shape.fired_verbs:
        cands = per_verb.get(verb.name, ())
        if not cands:
            continue
        if verb.param is not None:
            value = (
                extract_explicit(verb.param, prompt) if isinstance(verb.param, ScoreSpec) else None
            )
            if value is not None:
                explicit[verb.name] = {verb.param.name: value}
            else:
                params.append(verb.name)
        if collective and not has_exception:
            named = _verbatim_named(cands, prompt)
            if named is not None:
                cands = (named,)
                name_matched.append(verb.name)
            coll[verb.name] = cands
        elif collective:
            exclude[verb.name] = cands
        else:
            opts = target_options(cands, area_names)
            if len(opts) == 1:
                coll[verb.name] = cands
            else:
                singular[verb.name] = opts
                bases = {re.sub(r" \([^)]*\)$", "", o.label) for o in opts}
                if len(bases) == 1 and not shape.scope_areas:
                    # 'Dachterrasse Rollo' in Galerie and Schlafzimmer, no room said: nothing
                    # in the request can tell them apart, whatever the model claims.
                    ambiguous_by_area.append(verb.name)
                # A whole area or floor was named and no device was: if Jev then finds no
                # single target, the user meant every candidate in that scope.
                if (
                    not verb.is_query
                    and verb.name not in widened
                    and not verbatim_matches(cands, prompt)
                ):
                    if shape.scope_areas:
                        scoped_sweep_ok.append(verb.name)
                    elif shape.scope_domains:
                        domain_sweep_ok.append(verb.name)
    cond: tuple[Entity, ...] = ()
    if shape.condition_domain and shape.flag("has_condition") >= thresholds.flag:
        cond = tuple(e for e in home.entities if e.domain == shape.condition_domain)
        if shape.scope_areas:
            cond = tuple(e for e in cond if e.area_id in shape.scope_areas)
        if len(cond) > scope_cap:
            # Truncating would silently hide the right answer; ask nothing instead and let
            # the engine record that the condition could not be resolved.
            cond = ()
    return Round2Plan(
        exclude,
        singular,
        coll,
        tuple(params),
        cond,
        tuple(name_matched),
        explicit,
        tuple(scoped_sweep_ok),
        tuple(domain_sweep_ok),
        target_options(cond, area_names) if cond else (),
        tuple(ambiguous_by_area),
    )


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


def build_round2_questions(
    shape: Shape, plan: Round2Plan, home: HomeModel, pb: Phrasebook = EN
) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    for verb_name, ents in plan.exclude.items():
        for e in ents:
            qs[f"exclude:{verb_name}:{e.entity_id}"] = NoulQ(
                pb.exclusion_question.format(name=e.name, area=_area_name(home, e))
            )
    for verb_name, opts in plan.singular.items():
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        qs[f"target:{verb_name}"] = ChoiceQ(
            pb.target_question.format(phrasing=pb.phrasing_for(verb.name, verb.phrasing)),
            tuple(o.label for o in opts) + (NO_MATCH,),
        )
    for verb_name in plan.params:
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        spec = verb.param
        if isinstance(spec, ScoreSpec):
            qs[f"param:{verb_name}"] = ScoreQ(
                pb.param_question.format(param=pb.param_label(spec.name)),
                pb.levels_for(spec.name, spec.levels),
            )
        elif isinstance(spec, ChoiceSpec):
            qs[f"param:{verb_name}"] = ChoiceQ(
                pb.param_question.format(param=pb.param_label(spec.name)), spec.options
            )
    if plan.condition_candidates and shape.condition_domain:
        qs["cond_subject"] = ChoiceQ(
            pb.cond_subject_question,
            tuple(o.label for o in plan.condition_options) + (NO_MATCH,),
        )
        qs["cond_state"] = ChoiceQ(
            pb.cond_state_question,
            DOMAIN_STATES.get(shape.condition_domain, ("on", "off")),
        )
    return qs
