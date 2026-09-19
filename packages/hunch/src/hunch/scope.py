"""Between rounds: turn the request shape into a candidate set.

Widens, never narrows, never hard-fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hunch.config import EngineConfig
from hunch.model import Entity, HomeModel
from hunch.phrasing import Phrasebook
from hunch.resolution import Trace
from hunch.round1 import Shape
from hunch.vocabulary import Verb


@dataclass(frozen=True)
class Candidates:
    entities: tuple[Entity, ...]
    widened: bool


@dataclass(frozen=True)
class DeviceRound:
    entities: tuple[Entity, ...]


@dataclass(frozen=True)
class Clarify:
    question_key: str
    candidates: tuple[Entity, ...]


@dataclass(frozen=True)
class ScopeEscalate:
    reason: str


ScopeResult = Candidates | DeviceRound | Clarify | ScopeEscalate

_WIDEN_FLOOR = 0.1  # probabilities at or below this are noise; never widen on them


def _words(text: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", text.casefold()) + " "


def verbatim_matches(entities: tuple[Entity, ...], prompt: str) -> tuple[Entity, ...]:
    """Code calculates: entities whose name, alias or device name appears whole-word in the
    prompt. Deterministic and language-agnostic; Jev is never asked what code can look up."""
    if not prompt:
        return ()
    text = _words(prompt)
    hits: list[Entity] = []
    for e in entities:
        for label in (e.name, e.device_name, *e.aliases):
            if label and _words(label) in text:
                hits.append(e)
                break
    return tuple(hits)


def verbatim_areas(home: HomeModel, prompt: str) -> tuple[str, ...]:
    """Areas the prompt names outright — by area name or alias, or by a floor's name or alias
    (which scopes all of the floor's areas). Only if none is named in full, a stem shared by
    several areas counts: "Badezimmer" scopes both "Badezimmer Oben" and "Badezimmer Unten",
    but "Badezimmer unten" scopes just the one."""
    if not prompt:
        return ()
    text = _words(prompt)
    hits: list[str] = []
    for a in home.areas:
        if any(_words(lbl) in text for lbl in (a.name, *a.aliases) if lbl):
            hits.append(a.area_id)
    for f in home.floors:
        if any(_words(lbl) in text for lbl in (f.name, *f.aliases) if lbl):
            hits.extend(a for a in f.area_ids if a not in hits)
    if hits:
        return tuple(hits)
    stems: list[str] = []
    for a in home.areas:
        parts = a.name.split()
        if len(parts) > 1 and len(parts[0]) >= 4 and _words(parts[0]) in text:
            stems.append(a.area_id)
    return tuple(stems)


def verbatim_domains(prompt: str, phrasebooks: tuple[Phrasebook, ...]) -> tuple[str, ...]:
    """Domains the prompt names by a known word in any supported language ("Licht", "lights",
    "Rollos", "Fernseher"). Empty when nothing matches — Jev's domain judgement then stands."""
    if not prompt:
        return ()
    text = _words(prompt)
    found: list[str] = []
    for pb in phrasebooks:
        for domain, words in pb.domain_synonyms.items():
            if domain not in found and any(_words(w) in text for w in words):
                found.append(domain)
    return tuple(found)


def device_label(e: Entity) -> str:
    return e.device_name or e.name


def _applicable(home: HomeModel, verb: Verb) -> tuple[Entity, ...]:
    return tuple(e for e in home.entities if verb.name in e.verbs)


def _filter(
    entities: tuple[Entity, ...], areas: tuple[str, ...], domains: tuple[str, ...]
) -> tuple[Entity, ...]:
    return tuple(
        e
        for e in entities
        if (not areas or e.area_id in areas) and (not domains or e.domain in domains)
    )


def strict_candidates(home: HomeModel, verb: Verb, shape: Shape) -> tuple[Entity, ...]:
    """Verb ∧ area-scope ∧ domain-scope.

    With *no* scope signal at all there is nothing strict about it:
    return () so the widening chain (and its cap) decides.
    """
    if not shape.scope_areas and not shape.scope_domains:
        return ()
    return _filter(_applicable(home, verb), shape.scope_areas, shape.scope_domains)


def ranked_widen(home: HomeModel, verb: Verb, shape: Shape, trace: Trace) -> tuple[Entity, ...]:
    """Add areas in descending probability until non-empty; then domains; then all applicable."""
    applicable = _applicable(home, verb)
    if not applicable:
        return ()

    areas = list(shape.scope_areas)
    for area_id, p in sorted(shape.area_probs.items(), key=lambda kv: -kv[1]):
        if area_id in areas or p <= _WIDEN_FLOOR:
            continue
        areas.append(area_id)
        found = _filter(applicable, tuple(areas), shape.scope_domains)
        if found:
            trace.note(f"widen:{verb.name}:area:{area_id}")
            return found

    domains = list(shape.scope_domains)
    for domain, p in sorted(shape.domain_probs.items(), key=lambda kv: -kv[1]):
        if domain in domains or p <= _WIDEN_FLOOR:
            continue
        domains.append(domain)
        found = _filter(applicable, (), tuple(domains))
        if found:
            trace.note(f"widen:{verb.name}:domain:{domain}")
            return found

    trace.note(f"widen:{verb.name}:all")
    return applicable


def scope_candidates(
    home: HomeModel,
    verb: Verb,
    shape: Shape,
    config: EngineConfig,
    trace: Trace,
    prompt: str = "",
) -> ScopeResult:
    found = strict_candidates(home, verb, shape)
    widened = not found
    if widened:
        found = ranked_widen(home, verb, shape, trace)
        if not found:
            return ScopeEscalate("scope")

    # The cap guards against context rot in the Round 2 Choice, which an oversized *strict*
    # set causes just as surely as an oversized widened one.
    named = verbatim_matches(found, prompt)
    has_exception = shape.flag("has_exception") >= config.thresholds.flag
    if named and len(named) < len(found) and not has_exception:
        # The prompt names some of the candidates outright: those are the candidates.
        # (Not when it names an exception — then the named thing is what to leave alone.)
        trace.note(f"name_scope:{verb.name}:{len(named)}")
        found = named
    if len(found) <= config.scope_cap:
        return Candidates(found, widened=widened)

    trace.note(f"scope_cap:{verb.name}:{len(found)}>{config.scope_cap}")
    if config.device_round:
        return DeviceRound(found)
    if config.supports_clarification:
        return Clarify("which_area", found)
    return ScopeEscalate("scope")
