"""Round 1: judge the *shape* of the request over a tiny state. Never shows Jev an entity list."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hunch.config import Thresholds
from hunch.model import Area, Entity, HomeModel
from hunch.phrasing import EN, Phrasebook
from hunch.questions import JSON, Answers, ChoiceQ, NoulQ, Question
from hunch.resolution import Trace
from hunch.vocabulary import Verb, Vocabulary

FLAGS = (
    "collective",
    "names_specific",
    "has_exception",
    "has_condition",
    "has_timing",
    "is_destructive",
)


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


def build_round1_questions(
    home: HomeModel, vocab: Vocabulary, pb: Phrasebook = EN
) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    for v in vocab.verbs:
        qs[f"verb:{v.name}"] = NoulQ(
            pb.verb_question.format(phrasing=pb.phrasing_for(v.name, v.phrasing))
        )
    for f in home.floors:
        qs[f"floor:{f.floor_id}"] = NoulQ(pb.floor_question.format(name=f.name))
    for a in home.areas:
        qs[f"area:{a.area_id}"] = NoulQ(pb.area_question.format(label=_label(a)))
    for d in home.domains:
        qs[f"domain:{d}"] = NoulQ(pb.domain_question.format(domain=pb.domain_label(d)))
    for flag in FLAGS:
        qs[f"flag:{flag}"] = NoulQ(pb.flags[flag])
    if home.scenes:
        qs["scene"] = ChoiceQ(
            pb.scene_question,
            tuple(s.name for s in home.scenes) + ("none",),
        )
    if home.domains:
        qs["condition_domain"] = ChoiceQ(
            pb.condition_domain_question,
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
        return self.flags.get(name, 0.0)


def interpret_round1(
    home: HomeModel, vocab: Vocabulary, answers: Answers, thresholds: Thresholds, trace: Trace
) -> Shape:
    trace.record(1, answers)

    fired_verbs = tuple(
        v
        for v in vocab.verbs
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
        d
        for d in home.domains
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
    if (
        trace.decide("flag:has_condition", flags["has_condition"], thresholds.flag)
        and "condition_domain" in answers.answers
    ):
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
