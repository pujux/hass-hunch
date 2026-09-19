"""Turn probabilities into a Resolution. Confidence is the minimum of everything we relied on."""

from __future__ import annotations

from hunch.config import EngineConfig
from hunch.model import Entity
from hunch.questions import Answers
from hunch.resolution import (
    Action,
    Condition,
    Escalate,
    NeedsConfirmation,
    Resolution,
    Resolved,
    Trace,
)
from hunch.round1 import Shape
from hunch.round2 import NO_MATCH, Round2Plan, target_options
from hunch.vocabulary import ChoiceSpec, Risk, ScoreSpec


def score_to_value(spec: ScoreSpec, score: float) -> float:
    idx = score - 1.0  # Jev scores are 1-based over the rubric levels
    idx = max(0.0, min(float(len(spec.values) - 1), idx))  # saturate both ends
    lo = max(0, min(len(spec.values) - 1, int(idx)))
    hi = min(len(spec.values) - 1, lo + 1)
    frac = idx - lo
    return spec.values[lo] + (spec.values[hi] - spec.values[lo]) * frac


def _verb_prob(trace: Trace, verb_name: str) -> float:
    for d in reversed(trace.decisions):
        if d.name == f"verb:{verb_name}":
            return d.value
    return 1.0


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

    for verb in shape.fired_verbs:
        contributions.append(_verb_prob(trace, verb.name))
        targets: tuple[Entity, ...] = ()

        if verb.name in plan.collective:
            targets = plan.collective[verb.name]
            if len(targets) > 1:  # a single candidate never relied on the collective flag
                contributions.append(shape.flag("collective"))
        elif verb.name in plan.exclude and round2 is not None:
            kept: list[Entity] = []
            contributions.append(shape.flag("collective"))
            contributions.append(shape.flag("has_exception"))
            for e in plan.exclude[verb.name]:
                p = round2.noul(f"exclude:{verb.name}:{e.entity_id}")
                excluded = trace.decide(f"exclude:{verb.name}:{e.entity_id}", p, 0.5)
                contributions.append(p if excluded else 1.0 - p)
                if not excluded:
                    kept.append(e)
            targets = tuple(kept)
        elif verb.name in plan.singular and round2 is not None:
            c = round2.choice(f"target:{verb.name}")
            contributions.append(c.confidence)
            trace.decide(f"target:{verb.name}", c.confidence, th.target_choice_conf)
            if c.choice == NO_MATCH:
                trace.note(f"no_match:target:{verb.name}")
                targets = ()
            else:
                opt = next((o for o in plan.singular[verb.name] if o.label == c.choice), None)
                targets = opt.entities if opt else ()
        elif shape.scene is not None and verb.name == "activate":
            targets = (shape.scene,)

        params: dict[str, float | str] = {}
        if verb.param is not None and round2 is not None and f"param:{verb.name}" in round2.answers:
            if isinstance(verb.param, ScoreSpec):
                s = round2.score(f"param:{verb.name}")
                params[verb.param.name] = score_to_value(verb.param, s.score)
                contributions.append(s.confidence)
            elif isinstance(verb.param, ChoiceSpec):
                c = round2.choice(f"param:{verb.name}")
                params[verb.param.name] = c.choice
                contributions.append(c.confidence)

        if not targets:
            continue
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
            opt = next(
                (o for o in target_options(plan.condition_candidates) if o.label == subj.choice),
                None,
            )
            if opt:
                condition = Condition(opt.entities[0], state.choice)

    if not actions:
        return Escalate("low_confidence", (), trace)

    confidence = min(contributions) if contributions else 0.0
    trace.decide("confidence", confidence, th.auto_execute)

    if reasons:
        return NeedsConfirmation(tuple(actions), condition, reasons[0], trace)
    if confidence >= th.auto_execute:
        return Resolved(tuple(actions), condition, confidence, trace)
    if confidence >= th.confirm_band:
        return NeedsConfirmation(tuple(actions), condition, "confidence", trace)
    return Escalate("low_confidence", tuple(actions), trace)
