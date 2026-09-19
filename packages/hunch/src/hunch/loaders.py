"""Build a HomeModel from a registry export (see tools/export_home.py).

This is the reference for how the integration's HomeModelBuilder should derive a home:
only Assist-exposed entities, area inherited from the device when the entity has none,
user-given names preferred over integration defaults, verbs from the vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hunch.model import Area, Entity, Floor, HomeModel
from hunch.vocabulary import DEFAULT_VOCABULARY, Vocabulary, verbs_for_domain

SCENE_DOMAINS = frozenset({"scene", "script"})


def home_from_export(
    export: Mapping[str, Any], vocabulary: Vocabulary = DEFAULT_VOCABULARY
) -> HomeModel:
    areas_raw = export.get("areas", [])
    areas = tuple(
        Area(
            area_id=a["area_id"],
            name=a["name"],
            aliases=tuple(a.get("aliases") or ()),
            floor_id=a.get("floor_id"),
        )
        for a in areas_raw
    )
    floors = tuple(
        Floor(
            floor_id=f["floor_id"],
            name=f["name"],
            area_ids=tuple(a.area_id for a in areas if a.floor_id == f["floor_id"]),
        )
        for f in export.get("floors", [])
    )
    devices = {d["id"]: d for d in export.get("devices", [])}
    registry = {e["entity_id"]: e for e in export.get("entities", [])}
    states = export.get("states", {})
    exposed = list(export.get("exposed", []))

    entities: list[Entity] = []
    scenes: list[Entity] = []
    for entity_id in exposed:
        reg = registry.get(entity_id, {})
        st = states.get(entity_id, {})
        device = devices.get(reg.get("device_id") or "")
        domain = entity_id.split(".", 1)[0]
        device_name = None
        if device is not None:
            device_name = device.get("name_by_user") or device.get("name")
        name = (
            reg.get("name")
            or reg.get("original_name")
            or st.get("friendly_name")
            or device_name
            or entity_id
        )
        entity = Entity(
            entity_id=entity_id,
            domain=domain,
            name=name,
            aliases=tuple(reg.get("aliases") or ()),
            area_id=reg.get("area_id") or (device.get("area_id") if device else None),
            device_id=reg.get("device_id"),
            device_name=device_name,
            verbs=(
                frozenset({"activate"})
                if domain in SCENE_DOMAINS
                else verbs_for_domain(domain, vocabulary)
            ),
            state=st.get("state"),
        )
        (scenes if domain in SCENE_DOMAINS else entities).append(entity)

    return HomeModel(floors=floors, areas=areas, entities=tuple(entities), scenes=tuple(scenes))
