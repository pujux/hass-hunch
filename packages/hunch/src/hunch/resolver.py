"""Turn probabilities into a Resolution. Confidence is the minimum of everything we relied on."""

from __future__ import annotations

from hunch.config import EngineConfig
from hunch.model import Entity
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
from hunch.round2 import NO_MATCH, Round2Plan
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


def _verb_prob(trace: Trace, verb_name: str) -> float:
    for d in reversed(trace.decisions):
        if d.name == f"verb:{verb_name}":
            return d.value
    # Invariant: Round 1 asks a Noul for every verb in the vocabulary and records the
    # decision, so a fired verb always has one. If it somehow does not, fail closed —
    # an unverified verb must never be treated as certain.
    return 0.0


def resolve(
    shape: Shape, plan: Round2Plan, round2: Answers | None, config: EngineConfig, trace: Trace
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

    for verb in shape.fired_verbs:
        # Per-verb contributions stay local until the verb actually yields targets: a verb
        # that resolves to nothing must not drag down the confidence of the ones that did.
        local: list[float] = [_verb_prob(trace, verb.name)]
        targets: tuple[Entity, ...] = ()

        if verb.name in plan.collective:
            targets = plan.collective[verb.name]
            if verb.name in plan.excluded_by_name:
                trace.note(f"exception_match:{verb.name}:{len(plan.excluded_by_name[verb.name])}")
                local.append(shape.flag("has_exception"))
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
            c = round2.choice(f"target:{verb.name}")
            trace.decide(f"target:{verb.name}", c.confidence, th.target_choice_conf)
            options = plan.singular[verb.name]
            ranked = sorted(
                (o for o in options if o.label != NO_MATCH),
                key=lambda o: -c.probabilities.get(o.label, 0.0),
            )
            if c.choice == NO_MATCH:
                trace.note(f"no_match:target:{verb.name}")
                named_device = shape.flag("names_specific") >= th.confirm_band
                if named_device:
                    trace.note(f"unknown_device:{verb.name}")
                if verb.name in plan.scoped_sweep_ok and not named_device:
                    # Whole area/floor named, no device named: all of them, on the scope's word.
                    trace.note(f"scoped_sweep:{verb.name}")
                    targets = tuple(e for o in options for e in o.entities)
                    local.append(_scope_strength(shape, trace))
                elif verb.name in plan.domain_sweep_ok or trace.decide(
                    f"collective_fallback:{verb.name}",
                    shape.flag("collective"),
                    th.collective_fallback,
                ):
                    # Plural hint under threshold but present: the user meant all of them. Ask.
                    trace.note(f"collective_fallback:{verb.name}")
                    targets = tuple(e for o in options for e in o.entities)
                    local.append(c.confidence)
                    reasons.append("collective_fallback")
                elif c.confidence < th.no_match_clarify:
                    # Mass spread across real options: ask which one, don't give up.
                    candidates = tuple(e for o in ranked for e in o.entities)
                    trace.note(f"clarify:target:{verb.name}")
                    if pending_clarify is None:
                        pending_clarify = candidates[: config.clarify_max_candidates]
                else:
                    local.append(c.confidence)
            else:
                opt = next((o for o in options if o.label == c.choice), None)
                weak = c.confidence < th.confirm_band and len(ranked) > 1
                named_device = shape.flag("names_specific") >= th.confirm_band
                if (
                    opt is not None
                    and weak
                    and verb.name in plan.scoped_sweep_ok
                    and not named_device
                ):
                    # A weak pick inside a named area/floor: the user meant all of them.
                    trace.note(f"scoped_sweep:{verb.name}")
                    targets = tuple(e for o in options for e in o.entities)
                    local.append(_scope_strength(shape, trace))
                elif opt is not None and (weak or verb.name in plan.ambiguous_by_area):
                    if not weak:
                        trace.note(f"clarify:ambiguous_by_area:{verb.name}")
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

        params: dict[str, float | str] = dict(plan.explicit.get(verb.name, {}))
        if params:
            trace.note(f"param:explicit:{verb.name}")
        elif (
            verb.param is not None and round2 is not None and f"param:{verb.name}" in round2.answers
        ):
            if isinstance(verb.param, ScoreSpec):
                s = round2.score(f"param:{verb.name}")
                params[verb.param.name] = score_to_value(verb.param, s.score)
                local.append(s.confidence)
            elif isinstance(verb.param, ChoiceSpec):
                c = round2.choice(f"param:{verb.name}")
                params[verb.param.name] = c.choice
                local.append(c.confidence)

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
