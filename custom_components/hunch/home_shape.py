"""Registry entries -> the export dict shape that hunch.loaders.home_from_export reads.

Pure: takes anything with the registry entries' attribute names (works with HA's entries and
with SimpleNamespace in tests). Only exposed entities are included.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _aliases(raw: Iterable[Any] | None) -> list[str]:
    """Only real strings. HA stores a COMPUTED_NAME sentinel in `aliases` to mean "the
    computed full name is an alias"; the name itself is exported separately, and a non-string
    has no place in a JSON export shape."""
    return sorted(a for a in (raw or ()) if isinstance(a, str))


def export_shape(
    floors: Iterable[Any],
    areas: Iterable[Any],
    devices: Iterable[Any],
    entities: Iterable[Any],
    exposed_ids: set[str],
) -> dict[str, Any]:
    exposed = [e.entity_id for e in entities if e.entity_id in exposed_ids]
    return {
        "floors": [
            {"floor_id": f.floor_id, "name": f.name, "aliases": _aliases(f.aliases)} for f in floors
        ],
        "areas": [
            {
                "area_id": a.id,
                "name": a.name,
                "aliases": _aliases(a.aliases),
                "floor_id": a.floor_id,
            }
            for a in areas
        ],
        "devices": [
            {"id": d.id, "name": d.name, "name_by_user": d.name_by_user, "area_id": d.area_id}
            for d in devices
        ],
        "entities": [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "original_name": e.original_name,
                "aliases": _aliases(e.aliases),
                "area_id": e.area_id,
                "device_id": e.device_id,
            }
            for e in entities
            if e.entity_id in exposed_ids
        ],
        "exposed": exposed,
        "states": {},
    }
