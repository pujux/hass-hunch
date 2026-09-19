"""Between rounds: turn the request shape into a candidate set.

Widens, never narrows, never hard-fails.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunch.config import EngineConfig
from hunch.model import Entity, HomeModel
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
    home: HomeModel, verb: Verb, shape: Shape, config: EngineConfig, trace: Trace
) -> ScopeResult:
    found = strict_candidates(home, verb, shape)
    widened = not found
    if widened:
        found = ranked_widen(home, verb, shape, trace)
        if not found:
            return ScopeEscalate("scope")

    # The cap guards against context rot in the Round 2 Choice, which an oversized *strict*
    # set causes just as surely as an oversized widened one.
    if len(found) <= config.scope_cap:
        return Candidates(found, widened=widened)

    trace.note(f"scope_cap:{verb.name}:{len(found)}>{config.scope_cap}")
    if config.device_round:
        return DeviceRound(found)
    if config.supports_clarification:
        return Clarify("which_area", found)
    return ScopeEscalate("scope")
