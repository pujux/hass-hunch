"""Turn probabilities into a Resolution. Confidence is the minimum of everything we relied on."""

from __future__ import annotations

import re

from hunch.config import EngineConfig
from hunch.model import Entity
from hunch.phrasing import EN, Phrasebook
from hunch.questions import Answers
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
from hunch.round1 import Shape
from hunch.round2 import NO_MATCH, Round2Plan, parse_number
from hunch.vocabulary import ChoiceSpec, Risk, ScoreSpec


def score_to_value(spec: ScoreSpec, score: float) -> float:
    idx = score  # Jev scores are 0-based: a level's position in the criteria list
    idx = max(0.0, min(float(len(spec.values) - 1), idx))  # saturate both ends
    lo = max(0, min(len(spec.values) - 1, int(idx)))
    hi = min(len(spec.values) - 1, lo + 1)
    frac = idx - lo
    return spec.values[lo] + (spec.values[hi] - spec.values[lo]) * frac


def _scope_strength(shape: Shape, trace: Trace) -> float:
    """The strongest Round 1 signal that put areas into scope: an area or a floor probability."""
    best = max((shape.area_probs.get(a, 0.0) for a in shape.scope_areas), default=0.0)
    for d in trace.decisions:
        if d.name.startswith("floor:") and d.passed:
            best = max(best, d.value)
    return best


_BOUNDS = {"percent": (0.0, 100.0), "fraction": (0.0, 100.0), "degrees": (5.0, 35.0)}


def _numeric_value(spec: ScoreSpec, number: float, *, inverted: bool) -> float | None:
    """Code calculates: apply the mode Jev chose and the plausibility bounds of the unit."""
    lo, hi = _BOUNDS.get(spec.kind, (float("-inf"), float("inf")))
    if not lo <= number <= hi:
        return None
    if spec.kind == "fraction":
        number = number / 100.0
    if inverted and spec.kind == "percent":
        number = 100.0 - number
    return number


def _base_label(label: str) -> str:
    """'Dachterrasse Rollo (Galerie)' -> 'Dachterrasse Rollo'; '#2' suffixes likewise."""
    return re.sub(r" (?:\([^)]*\)|#\d+)$", "", label)


def _all_entities(options) -> tuple[Entity, ...]:
    """Every entity behind a set of options, once — a device option and its per-entity options
    describe the same lamps."""
    seen: dict[str, Entity] = {}
    for o in options:
        for e in o.entities:
            seen.setdefault(e.entity_id, e)
    return tuple(seen.values())


def _verb_prob(trace: Trace, verb_name: str) -> float:
    for d in reversed(trace.decisions):
        if d.name == f"verb:{verb_name}":
            return d.value
    # Invariant: Round 1 asks a Noul for every verb in the vocabulary and records the
    # decision, so a fired verb always has one. If it somehow does not, fail closed —
    # an unverified verb must never be treated as certain.
    return 0.0


def resolve(
    shape: Shape,
    plan: Round2Plan,
    round2: Answers | None,
    config: EngineConfig,
    trace: Trace,
    pb: Phrasebook = EN,
) -> Resolution:
    th = config.thresholds
    if round2 is not None:
        trace.record(2, round2)

    if trace.decide("flag:is_destructive", shape.flag("is_destructive"), th.flag) or any(
        v.risk is Risk.DESTRUCTIVE for v in shape.fired_verbs
    ):
        return Escalate("destructive", (), trace)

    actions: list[Action] = []
    contributions: list[float] = []
    reasons: list[str] = []
    pending_clarify: tuple[Entity, ...] | None = None  # only used if no verb yields an action
    exception_unresolved = False
    relative_change = False

    for verb in shape.fired_verbs:
        # Per-verb contributions stay local until the verb actually yields targets: a verb
        # that resolves to nothing must not drag down the confidence of the ones that did.
        local: list[float] = [_verb_prob(trace, verb.name)]
        targets: tuple[Entity, ...] = ()

        if verb.name in plan.outside_scope and round2 is not None:
            p_out = round2.noul(f"outside_scope:{verb.name}")
            if not trace.decide(f"outside_scope:{verb.name}", p_out, th.flag):
                trace.note(f"dropped:{verb.name}:outside_scope")
                continue
            local.append(p_out)

        if verb.name in plan.collective:
            targets = plan.collective[verb.name]
            if len(targets) > 1:  # a single candidate never relied on the collective flag
                collective = shape.flag("collective")
                in_scope = shape.scope_areas and all(
                    e.area_id in shape.scope_areas for e in targets
                )
                if in_scope:
                    # The room or floor was said out loud (or a floor fired) and everything we
                    # are about to touch is inside it: two signals agree on "all of them".
                    named = any(n.startswith("area_match:") for n in trace.notes)
                    backing = 1.0 if named else _scope_strength(shape, trace)
                    if backing > collective:
                        trace.note(f"collective_backed_by_scope:{verb.name}")
                        collective = backing
                local.append(collective)
        elif verb.name in plan.exclude and round2 is not None:
            kept: list[Entity] = []
            local.append(shape.flag("collective"))
            local.append(shape.flag("has_exception"))
            for e in plan.exclude[verb.name]:
                p = round2.noul(f"exclude:{verb.name}:{e.entity_id}")
                excluded = trace.decide(f"exclude:{verb.name}:{e.entity_id}", p, 0.5)
                local.append(p if excluded else 1.0 - p)
                if not excluded:
                    kept.append(e)
            targets = tuple(kept)
            if len(kept) == len(plan.exclude[verb.name]):
                # 'except X' named something we could not find: never sweep past it silently.
                trace.note(f"exception_unresolved:{verb.name}")
                exception_unresolved = True
        elif verb.name in plan.singular and round2 is not None:
            options = plan.singular[verb.name]
            c = round2.choice(f"target:{verb.name}")
            trace.decide(f"target:{verb.name}", c.confidence, th.target_choice_conf)
            ranked = sorted(
                (o for o in options if o.label != NO_MATCH),
                key=lambda o: -c.probabilities.get(o.label, 0.0),
            )
            p_all = (
                round2.noul(f"all_of:{verb.name}")
                if f"all_of:{verb.name}" in round2.answers
                else 0.0
            )
            if trace.decide(f"all_of:{verb.name}", p_all, th.collective):
                # Jev saw the candidates and says the request means every one of them.
                trace.note(f"all_of:{verb.name}")
                targets = _all_entities(options)
                backing = p_all
                if shape.scope_areas and all(e.area_id in shape.scope_areas for e in targets):
                    named = any(n.startswith("area_match:") for n in trace.notes)
                    backing = max(p_all, 1.0 if named else _scope_strength(shape, trace))
                    if backing > p_all:
                        trace.note(f"collective_backed_by_scope:{verb.name}")
                local.append(backing)
            elif c.choice == NO_MATCH:
                trace.note(f"no_match:target:{verb.name}")
                if trace.decide(
                    f"collective_fallback:{verb.name}",
                    max(shape.flag("collective"), p_all),
                    th.collective_fallback,
                ):
                    # Some plural signal, no single target: all of them — but ask first.
                    trace.note(f"collective_fallback:{verb.name}")
                    targets = _all_entities(options)
                    local.append(max(shape.flag("collective"), p_all))
                    reasons.append("collective_fallback")
                elif c.confidence < th.no_match_clarify:
                    candidates = tuple(e for o in ranked for e in o.entities)
                    trace.note(f"clarify:target:{verb.name}")
                    if pending_clarify is None:
                        pending_clarify = candidates[: config.clarify_max_candidates]
                else:
                    local.append(c.confidence)
            else:
                opt = next((o for o in options if o.label == c.choice), None)
                weak = c.confidence < th.confirm_band and len(ranked) > 1
                twins: list = []
                if opt is not None and not shape.scope_areas:
                    # Same device name in several rooms and no room said: nothing in the request
                    # can tell them apart, whatever the pick's confidence. A lookup, not a
                    # judgement.
                    base = _base_label(opt.label)
                    twins = [o for o in options if o is not opt and _base_label(o.label) == base]
                if opt is not None and (weak or twins or verb.name in plan.ambiguous_by_area):
                    if not weak:
                        trace.note(f"clarify:ambiguous_by_area:{verb.name}")
                    if twins:
                        ranked = [opt, *twins]
                    # A weak pick among real alternatives, or same-named devices in different
                    # rooms with no room said: asking beats guessing or giving up.
                    ordered = [opt, *(o for o in ranked if o is not opt)]
                    candidates = tuple(e for o in ordered for e in o.entities)
                    trace.note(f"clarify:weak_pick:{verb.name}")
                    if pending_clarify is None:
                        pending_clarify = candidates[: config.clarify_max_candidates]
                else:
                    targets = opt.entities if opt else ()
                    local.append(c.confidence)
        elif shape.scene is not None and verb.name == "activate":
            targets = (shape.scene,)

        params: dict[str, float | str] = {}
        spec = verb.param
        if (
            isinstance(spec, ScoreSpec)
            and round2 is not None
            and f"param_value:{verb.name}" in round2.answers
        ):
            # Jev judged which number (if any) is the value and how it is meant; code computes.
            picked = round2.choice(f"param_value:{verb.name}")
            number = parse_number(picked.choice) if picked.choice != NO_MATCH else None
            if number is None:
                trace.note(f"param:number_rejected:{verb.name}")
            else:
                p_rel = round2.noul(f"param_relative:{verb.name}")
                relative = trace.decide(f"param_relative:{verb.name}", p_rel, th.flag)
                if relative:
                    trace.note(f"param:relative:{verb.name}:{number}")
                    relative_change = True
                else:
                    inverted = False
                    inv_conf = 1.0
                    if f"param_inverted:{verb.name}" in round2.answers:
                        p_inv = round2.noul(f"param_inverted:{verb.name}")
                        inverted = trace.decide(f"param_inverted:{verb.name}", p_inv, 0.5)
                        inv_conf = p_inv if inverted else 1.0 - p_inv
                    value = _numeric_value(spec, number, inverted=inverted)
                    if value is None:
                        trace.note(f"param:out_of_bounds:{verb.name}")
                    else:
                        params[spec.name] = value
                        local.extend((picked.confidence, 1.0 - p_rel, inv_conf))
                        trace.note(f"param:number:{verb.name}:{value}")
        if not params and (
            spec is not None and round2 is not None and f"param:{verb.name}" in round2.answers
        ):
            if isinstance(spec, ScoreSpec):
                sc = round2.score(f"param:{verb.name}")
                params[spec.name] = score_to_value(spec, sc.score)
                local.append(sc.confidence)
            elif isinstance(spec, ChoiceSpec):
                cc = round2.choice(f"param:{verb.name}")
                params[spec.name] = cc.choice
                local.append(cc.confidence)

        if not targets:
            trace.note(f"dropped:{verb.name}")
            continue
        contributions.extend(local)
        actions.append(Action(verb, targets, params))
        if verb.risk is Risk.CONFIRM:
            reasons.append("risk:confirm")
        if len(targets) > config.max_silent_targets:
            reasons.append("blast_radius")

    condition: Condition | None = None
    if plan.condition_candidates and round2 is not None and "cond_subject" in round2.answers:
        subj = round2.choice("cond_subject")
        state = round2.choice("cond_state")
        contributions.extend((subj.confidence, state.confidence))
        if subj.choice == NO_MATCH:
            trace.note("no_match:cond_subject")
        else:
            opt = next((o for o in plan.condition_options if o.label == subj.choice), None)
            if opt:
                condition = Condition(opt.entities[0], state.choice)

    if exception_unresolved:
        return Escalate("exception", tuple(actions), trace)
    if relative_change:
        # The engine does not know current brightness/position/setpoints; the fallback agent does.
        return Escalate("relative_change", tuple(actions), trace)
    if condition is None and trace.decide(
        "flag:has_condition", shape.flag("has_condition"), th.flag
    ):
        # The request depends on something we could not turn into a Condition. Executing it
        # unconditionally would be wrong; the fallback agent can handle the 'if'.
        trace.note("condition_unresolved")
        return Escalate("condition", tuple(actions), trace)

    if not actions:
        if pending_clarify:
            # Nothing resolved and one target Choice spread its mass over real options: ask.
            return NeedsClarification("which_device", pending_clarify, trace)
        return Escalate("low_confidence", (), trace)
    if pending_clarify:
        trace.note("clarify_suppressed:other_verbs_resolved")

    confidence = min(contributions) if contributions else 0.0
    trace.decide("confidence", confidence, th.auto_execute)

    if reasons:
        return NeedsConfirmation(tuple(actions), condition, reasons[0], trace)
    if confidence >= th.auto_execute:
        return Resolved(tuple(actions), condition, confidence, trace)
    if confidence >= th.confirm_band:
        return NeedsConfirmation(tuple(actions), condition, "confidence", trace)
    return Escalate("low_confidence", tuple(actions), trace)
