"""Round 1: judge the *shape* of the request over a tiny state. Never shows Jev an entity list."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hunch.config import Thresholds
from hunch.model import Area, Entity, HomeModel
from hunch.questions import JSON, Answers, ChoiceQ, NoulQ, Question
from hunch.resolution import Trace
from hunch.vocabulary import Verb, Vocabulary

FLAGS = ("collective", "has_exception", "has_condition", "is_query", "has_timing", "is_destructive")

_FLAG_INSTRUCTIONS = {
    "collective": (
        "Does the request target every matching device in its scope for at least one of its "
        "actions — signaled by a plain plural (e.g. 'the kitchen lights'), or a word like "
        "'all', 'both' or 'every' — rather than exactly one specific device?"
    ),
    "has_exception": (
        "Does the request exclude something, e.g. 'except', 'but not', 'apart from', 'other than'?"
    ),
    "has_condition": (
        "Does the request make the action depend on a condition, "
        "e.g. 'if', 'when', 'unless', 'only if'?"
    ),
    "is_query": (
        "Is the request asking about the current state of something, "
        "rather than asking to change it?"
    ),
    "has_timing": (
        "Does the request ask to delay, schedule, sequence or time a device action, "
        "e.g. 'in ten minutes', 'after', 'later', 'then' — as opposed to merely mentioning "
        "a future time in a request that is not about controlling a device (such as asking "
        "about tomorrow's weather)?"
    ),
    "is_destructive": (
        "Would fulfilling the request cause irreversible, unsafe or security-relevant effects "
        "beyond an ordinary lock, unlock, arm or disarm action (which are handled separately) — "
        "for example, disabling safety equipment, or leaving the home open to unauthorized entry?"
    ),
}


def _label(area: Area) -> str:
    return f"{area.name} ({', '.join(area.aliases)})" if area.aliases else area.name


def build_round1_state(home: HomeModel, prompt: str) -> JSON:
    return {
        "request": prompt,
        "floors": [f.name for f in home.floors],
        "areas": [_label(a) for a in home.areas],
        "domains": list(home.domains),
        "scenes": [s.name for s in home.scenes],
    }


def build_round1_questions(home: HomeModel, vocab: Vocabulary) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    for v in vocab.verbs:
        qs[f"verb:{v.name}"] = NoulQ(f"Does the request ask to {v.phrasing}?")
    for f in home.floors:
        qs[f"floor:{f.floor_id}"] = NoulQ(
            f"Does the request refer to the floor '{f.name}' or to all of it?"
        )
    for a in home.areas:
        qs[f"area:{a.area_id}"] = NoulQ(f"Does the request refer to the area '{_label(a)}'?")
    for d in home.domains:
        qs[f"domain:{d}"] = NoulQ(f"Does the request involve devices of type '{d}'?")
    for flag in FLAGS:
        qs[f"flag:{flag}"] = NoulQ(_FLAG_INSTRUCTIONS[flag])
    if home.scenes:
        qs["scene"] = ChoiceQ(
            "Which scene or script does the request name, if any?",
            tuple(s.name for s in home.scenes) + ("none",),
        )
    qs["condition_domain"] = ChoiceQ(
        "If the request contains a condition, which device type is the condition about?",
        tuple(home.domains) + ("none",),
    )
    return qs


@dataclass(frozen=True)
class Shape:
    fired_verbs: tuple[Verb, ...]
    scope_areas: tuple[str, ...]
    scope_domains: tuple[str, ...]
    area_probs: Mapping[str, float]
    domain_probs: Mapping[str, float]
    flags: Mapping[str, float]
    scene: Entity | None
    condition_domain: str | None

    def flag(self, name: str) -> float:
        return self.flags[name]


def interpret_round1(
    home: HomeModel, vocab: Vocabulary, answers: Answers, thresholds: Thresholds, trace: Trace
) -> Shape:
    trace.record(1, answers)

    fired_verbs = tuple(
        v for v in vocab.verbs
        if trace.decide(f"verb:{v.name}", answers.noul(f"verb:{v.name}"), thresholds.verb_fire)
    )

    area_probs: dict[str, float] = {
        a.area_id: answers.noul(f"area:{a.area_id}") for a in home.areas
    }
    scope_areas: list[str] = []
    for f in home.floors:
        floor_fires = trace.decide(
            f"floor:{f.floor_id}", answers.noul(f"floor:{f.floor_id}"), thresholds.scope_fire
        )
        if floor_fires:
            scope_areas.extend(f.area_ids)
    for a in home.areas:
        area_fires = trace.decide(f"area:{a.area_id}", area_probs[a.area_id], thresholds.scope_fire)
        if area_fires and a.area_id not in scope_areas:
            scope_areas.append(a.area_id)

    domain_probs = {d: answers.noul(f"domain:{d}") for d in home.domains}
    scope_domains = tuple(
        d for d in home.domains
        if trace.decide(f"domain:{d}", domain_probs[d], thresholds.scope_fire)
    )

    flags = {flag: answers.noul(f"flag:{flag}") for flag in FLAGS}

    scene: Entity | None = None
    if "scene" in answers.answers:
        c = answers.choice("scene")
        if c.choice != "none" and trace.decide(
            "scene", c.confidence, thresholds.target_choice_conf
        ):
            scene = next((s for s in home.scenes if s.name == c.choice), None)

    condition_domain: str | None = None
    if trace.decide("flag:has_condition", flags["has_condition"], thresholds.flag):
        c = answers.choice("condition_domain")
        if c.choice != "none" and trace.decide(
            "condition_domain", c.confidence, thresholds.target_choice_conf
        ):
            condition_domain = c.choice

    return Shape(
        fired_verbs=fired_verbs,
        scope_areas=tuple(scope_areas),
        scope_domains=scope_domains,
        area_probs=area_probs,
        domain_probs=domain_probs,
        flags=flags,
        scene=scene,
        condition_domain=condition_domain,
    )
