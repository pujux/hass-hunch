"""Round 2: resolve targets and parameters, only for verbs Round 1 couldn't finish."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from hunch.config import Thresholds
from hunch.model import Entity, HomeModel
from hunch.phrasing import EN, Phrasebook
from hunch.questions import JSON, ChoiceQ, NoulQ, Question, ScoreQ
from hunch.resolution import PreviousTurn
from hunch.round1 import Shape, previous_turn_state
from hunch.scope import device_label, verbatim_areas
from hunch.timing import DurationLiteral, duration_literals, duration_questions
from hunch.vocabulary import ChoiceSpec, ScoreSpec

# Sentinel option appended to every Round 2 Choice so the model can say nothing fits,
# rather than being forced to pick among options that all miss.
NO_MATCH = "none of these"
ALL_IN_ROOM = "all of them"
BELOW = "below the number"
ABOVE = "above the number"

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
    """Options Jev can pick from. A device with one candidate entity is one option. A device
    with several is offered as the device itself ("Bedside lamps" -> both lamps) AND as each
    entity ("Bedside lamps — Bedside left"), because people name either; Jev decides which."""
    raw: list[TargetOption] = []
    for group in _group_by_device(candidates):
        if len(group) == 1:
            raw.append(TargetOption(device_label(group[0]), (group[0],)))
        else:
            raw.append(TargetOption(device_label(group[0]), tuple(group)))
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
    # verb -> the numeric literals found in the prompt; Jev is asked which one (if any) is the value
    numeric: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # options offered for the condition subject (labels must match the question exactly)
    condition_options: tuple[TargetOption, ...] = ()
    # singular verbs whose options differ only by area while the prompt names no area: ask
    ambiguous_by_area: tuple[str, ...] = ()
    # singular verbs also asked "all of these?" (Jev sees the candidates in the state)
    all_of: tuple[str, ...] = ()
    # collective verbs whose candidates span several device types: Jev is asked per candidate
    include: Mapping[str, tuple[Entity, ...]] = field(default_factory=dict)
    # verbs whose candidates lie outside the named room/floor: Jev is asked whether they were meant
    outside_scope: tuple[str, ...] = ()
    # verbs over several named places mixing a set and a specific device ("Licht in der Küche
    # und Esszimmer Stehlampe"): one Choice per room — all of them / one device / none
    room_targets: Mapping[str, Mapping[str, tuple[TargetOption, ...]]] = field(default_factory=dict)
    # follow-ups: verbs whose candidates ARE the targets (the previous turn's devices), no picking
    forced: tuple[str, ...] = ()
    # follow-ups: verb -> params of the previous turn, used when this turn names none
    carried_params: Mapping[str, Mapping[str, float | str]] = field(default_factory=dict)
    # follow-ups: verbs this turn never said (borrowed from the previous turn); the follow-up
    # judgment stands in for their verb probability
    carried_verbs: tuple[str, ...] = ()
    # numeric condition: the literal numbers in the prompt Jev chooses the threshold from
    condition_literals: tuple[str, ...] = ()
    # "für"/"in" timing on a device action: "for_duration" | "delayed" | None
    timing_kind: str | None = None
    # the duration literals in the prompt; Jev is asked which (if any) is a duration and its unit
    duration_literals: tuple[DurationLiteral, ...] = ()

    def all_candidates(self) -> tuple[Entity, ...]:
        seen: dict[str, Entity] = {}
        for ents in (*self.exclude.values(), *self.collective.values(), self.condition_candidates):
            for e in ents:
                seen.setdefault(e.entity_id, e)
        for rooms in self.room_targets.values():
            for opts in rooms.values():
                for o in opts:
                    for e in o.entities:
                        seen.setdefault(e.entity_id, e)
        for opts in self.singular.values():
            for o in opts:
                for e in o.entities:
                    seen.setdefault(e.entity_id, e)
        return tuple(seen.values())


_NUMBER = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d+)?\s*(?:%|°\s*c?|grad\b|prozent\b|percent\b|degrees?\b|celsius\b)?)",
    re.I,
)


def numeric_literals(prompt: str) -> tuple[str, ...]:
    """Code looks up: every number in the prompt, with its unit if it has one, verbatim. Whether
    one of them is the value to set — and how it is meant — is Jev's judgement, not ours."""
    seen: list[str] = []
    for m in _NUMBER.finditer(prompt):
        lit = re.sub(r"\s+", " ", m.group(1).strip())
        if lit and lit not in seen:
            seen.append(lit)
    return tuple(seen)


def parse_number(literal: str) -> float | None:
    m = re.match(r"\d{1,3}(?:[.,]\d+)?", literal)
    return float(m.group(0).replace(",", ".")) if m else None


def named_place_count(home: HomeModel, prompt: str) -> int:
    """How many distinct places the prompt names out loud: a floor named as a whole counts once,
    every other named room once: 'Licht im Untergeschoss' -> 1, 'Küche und Esszimmer' -> 2."""
    named = set(verbatim_areas(home, prompt))
    if not named:
        return 0
    count = 0
    for f in home.floors:
        if f.area_ids and set(f.area_ids) <= named:
            count += 1
            named -= set(f.area_ids)
    return count + len(named)


def plan_round2(
    home: HomeModel,
    shape: Shape,
    per_verb: Mapping[str, tuple[Entity, ...]],
    thresholds: Thresholds,
    scope_cap: int,
    prompt: str = "",
    widened: frozenset[str] = frozenset(),
    forced: frozenset[str] = frozenset(),
    carried_params: Mapping[str, Mapping[str, float | str]] | None = None,
    carried_verbs: frozenset[str] = frozenset(),
    prefer_pick: frozenset[str] = frozenset(),
    timing_kind: str | None = None,
) -> Round2Plan:
    has_exception = shape.flag("has_exception") >= thresholds.flag and not shape.exception_areas
    # An exception ("außer der Stehlampe") only makes sense over a set: it implies collective
    # even when the plural flag is low ("Licht im Untergeschoss aus ausser ...").
    collective = shape.flag("collective") >= thresholds.collective or has_exception
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
    numeric: dict[str, tuple[str, ...]] = {}
    literals = numeric_literals(prompt)
    ambiguous_by_area: list[str] = []
    all_of: list[str] = []
    include: dict[str, tuple[Entity, ...]] = {}
    room_targets: dict[str, dict[str, tuple[TargetOption, ...]]] = {}
    places_named = named_place_count(home, prompt)
    for verb in shape.fired_verbs:
        cands = per_verb.get(verb.name, ())
        if not cands:
            continue
        if verb.param is not None and not (
            carried_params and verb.name in carried_params and not literals
        ):  # a follow-up naming no number keeps the previous value: nothing to ask
            params.append(verb.name)
            if literals and isinstance(verb.param, ScoreSpec):
                numeric[verb.name] = literals
        verb_collective = collective and verb.name not in prefer_pick
        if verb.name in forced:
            coll[verb.name] = cands  # a follow-up: these devices, no question about which
        elif verb_collective and has_exception:
            exclude[verb.name] = cands
        elif places_named >= 2 and not verb.is_query and len({e.area_id for e in cands}) >= 2:
            # Two rooms said: each may mean a set ("Licht in der Küche") or one device
            # ("Esszimmer Stehlampe"). Neither "pick one" nor "all of them" fits the whole
            # request, so Jev compares per room — whatever the plural/specific flags said.
            by_area: dict[str, list[Entity]] = {}
            for e in cands:
                by_area.setdefault(e.area_id or "", []).append(e)
            room_targets[verb.name] = {
                area: target_options(tuple(ents), area_names) for area, ents in by_area.items()
            }
        elif verb_collective:
            coll[verb.name] = cands
            if len({e.domain for e in cands}) > 1:
                # "kitchen lights" over lights AND a fridge switch: Jev judges each candidate.
                include[verb.name] = cands
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
                if not verb.is_query and verb.name not in prefer_pick:
                    # Jev, not code, decides "one of them or all of them" — except for an
                    # "add devices" follow-up, which names what joins: pick, never sweep.
                    all_of.append(verb.name)
    cond: tuple[Entity, ...] = ()
    cond_literals: tuple[str, ...] = ()
    if shape.condition_numeric and shape.condition_domain and literals:
        # "wenn es unter 20 Grad hat": any entity of the judged kind whose value can be read;
        # Jev picks the sensor, the number and the direction, the executor compares.
        cond_literals = literals
    dur = duration_literals(prompt) if timing_kind else ()
    numeric_ok = bool(cond_literals)
    if (
        (shape.condition_domain in DOMAIN_STATES or numeric_ok)
        and shape.condition_domain
        and shape.flag("has_condition") >= thresholds.flag
    ):
        domains = (
            shape.condition_domains
            if numeric_ok and shape.condition_domains
            else (shape.condition_domain,)
        )
        in_domain = tuple(e for e in home.entities if e.domain in domains)
        in_scope = tuple(e for e in in_domain if e.area_id in shape.scope_areas)
        # The thing observed need not sit in the room being controlled ("Rollos in der Galerie
        # zu wenn die Klimaanlage läuft"): prefer the room, fall back to the whole home.
        cond = in_scope or in_domain
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
        numeric,
        target_options(cond, area_names) if cond else (),
        tuple(ambiguous_by_area),
        tuple(all_of),
        include,
        tuple(
            v.name
            for v in shape.fired_verbs
            if v.name in widened
            and not shape.several_verbs  # Jev already said: several distinct actions
            and (
                not shape.scope_areas
                or any(e.area_id not in shape.scope_areas for e in per_verb.get(v.name, ()))
            )
        ),
        room_targets,
        tuple(v.name for v in shape.fired_verbs if v.name in forced),
        dict(carried_params or {}),
        tuple(v.name for v in shape.fired_verbs if v.name in carried_verbs),
        cond_literals if cond else (),
        timing_kind,
        dur,
    )


def scope_description(home: HomeModel, shape: Shape) -> JSON:
    """What place the request resolved to, in Jev's terms: the floors it covers completely
    (with their aliases, so 'oben' reads as the whole Obergeschoss), the rooms, or the whole
    home. Round 2 candidates come from this place; without it Jev cannot tell 'Licht oben aus'
    (every light up there) from 'Licht an' (one light, somewhere)."""
    areas = set(shape.scope_areas)
    if not areas:
        return {"place": "the whole home" if shape.whole_home else "no place named"}
    floors = [
        {"name": f.name, "aliases": list(f.aliases)}
        for f in home.floors
        if f.area_ids and set(f.area_ids) <= areas
    ]
    covered = {
        a for f in home.floors if f.area_ids and set(f.area_ids) <= areas for a in f.area_ids
    }
    rooms = [a.name for a in home.areas if a.area_id in areas and a.area_id not in covered]
    out: dict[str, JSON] = {"place": "the floors and rooms named in the request"}
    if floors:
        out["whole_floors"] = floors
    if rooms:
        out["rooms"] = rooms
    return out


def build_round2_state(
    prompt: str,
    plan: Round2Plan,
    home: HomeModel,
    shape: Shape | None = None,
    previous: PreviousTurn | None = None,
) -> JSON:
    state: dict[str, JSON] = {"request": prompt}
    if shape is not None:
        state["scope"] = scope_description(home, shape)
    if previous is not None:
        state["previous"] = previous_turn_state(home, previous)
    if plan.duration_literals:
        state["duration_literals"] = [lit.text for lit in plan.duration_literals]
    state["candidates"] = [
        {
            "name": e.name,
            "aliases": list(e.aliases),
            "area": _area_name(home, e),
            "device": e.device_name,
            "type": e.domain,
            "state": e.state,
        }
        for e in plan.all_candidates()
    ]
    return state


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
    for verb_name in plan.all_of:
        qs[f"all_of:{verb_name}"] = NoulQ(pb.all_of_question)
    for verb_name, rooms in plan.room_targets.items():
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        for area_id, opts in rooms.items():
            area = home.area_by_id(area_id) if area_id else None
            room = area.name if area else "no room"
            kind = pb.domain_label(opts[0].entities[0].domain) if opts else "device"
            qs[f"room_target:{verb_name}:{area_id}"] = ChoiceQ(
                pb.room_target_question.format(
                    room=room, kind=kind, phrasing=pb.phrasing_for(verb.name, verb.phrasing)
                ),
                (ALL_IN_ROOM, *(o.label for o in opts), NO_MATCH),
                {
                    ALL_IN_ROOM: pb.special_descriptions["all_in_room"],
                    NO_MATCH: pb.special_descriptions["none_in_room"],
                },
            )
    for verb_name, ents in plan.include.items():
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        for e in ents:
            qs[f"include:{verb_name}:{e.entity_id}"] = NoulQ(
                pb.include_question.format(
                    name=e.name,
                    type=e.domain,
                    area=_area_name(home, e),
                    phrasing=pb.phrasing_for(verb.name, verb.phrasing),
                )
            )
    for verb_name in plan.outside_scope:
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        qs[f"outside_scope:{verb_name}"] = NoulQ(
            pb.outside_scope_question.format(phrasing=pb.phrasing_for(verb.name, verb.phrasing))
        )
    for verb_name in plan.params:
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        spec = verb.param
        if verb_name in plan.numeric and isinstance(spec, ScoreSpec):
            label = pb.param_label(spec.name)
            qs[f"param_value:{verb_name}"] = ChoiceQ(
                pb.param_value_question.format(param=label),
                plan.numeric[verb_name] + (NO_MATCH,),
                {NO_MATCH: pb.param_value_descriptions.get(NO_MATCH, "")},
            )
            qs[f"param_relative:{verb_name}"] = NoulQ(pb.param_relative_question)
            if spec.name == "position":
                qs[f"param_inverted:{verb_name}"] = NoulQ(pb.param_inverted_question)
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
            {NO_MATCH: pb.special_descriptions["no_condition_subject"]},
        )
        if plan.condition_literals:
            qs["cond_threshold"] = ChoiceQ(
                pb.cond_threshold_question,
                plan.condition_literals + (NO_MATCH,),
                {NO_MATCH: pb.special_descriptions["no_condition_number"]},
            )
            qs["cond_direction"] = ChoiceQ(
                pb.cond_direction_question,
                (BELOW, ABOVE, NO_MATCH),
                {
                    BELOW: pb.special_descriptions["condition_below"],
                    ABOVE: pb.special_descriptions["condition_above"],
                    NO_MATCH: pb.special_descriptions["no_condition_direction"],
                },
            )
        else:
            states = DOMAIN_STATES[shape.condition_domain]
            state_desc = dict(pb.condition_state_descriptions.get(shape.condition_domain, {}))
            state_desc[NO_MATCH] = pb.special_descriptions["no_condition_state"]
            qs["cond_state"] = ChoiceQ(
                pb.cond_state_question,
                states + (NO_MATCH,),
                state_desc,
            )
    qs.update(duration_questions(plan.duration_literals, pb))
    return qs
