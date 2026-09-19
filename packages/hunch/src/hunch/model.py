"""Immutable per-request snapshot of a home. Built by the integration; hand-built in tests."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class Floor:
    floor_id: str
    name: str
    area_ids: tuple[str, ...]


@dataclass(frozen=True)
class Area:
    area_id: str
    name: str
    aliases: tuple[str, ...]
    floor_id: str | None


@dataclass(frozen=True)
class Entity:
    entity_id: str
    domain: str
    name: str
    aliases: tuple[str, ...]
    area_id: str | None
    device_id: str | None
    device_name: str | None
    verbs: frozenset[str]
    state: str | None


@dataclass(frozen=True)
class HomeModel:
    floors: tuple[Floor, ...]
    areas: tuple[Area, ...]
    entities: tuple[Entity, ...]
    scenes: tuple[Entity, ...]

    def area_by_id(self, area_id: str) -> Area | None:
        return self._areas.get(area_id)

    def areas_for_floor(self, floor_id: str) -> tuple[str, ...]:
        for floor in self.floors:
            if floor.floor_id == floor_id:
                return floor.area_ids
        return ()

    def entity_by_id(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    @cached_property
    def domains(self) -> tuple[str, ...]:
        return tuple(sorted({e.domain for e in self.entities}))

    @cached_property
    def _areas(self) -> dict[str, Area]:
        return {a.area_id: a for a in self.areas}

    @cached_property
    def _entities(self) -> dict[str, Entity]:
        return {e.entity_id: e for e in (*self.entities, *self.scenes)}
