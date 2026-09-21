import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from hunch import home_from_export

from custom_components.hunch.home_shape import export_shape


def test_export_shape_matches_the_exporter():
    floors = [NS(floor_id="ug", name="Untergeschoss", aliases={"unten"})]
    areas = [
        NS(id="kuche", name="Küche", aliases=set(), floor_id="ug"),
        NS(id="loose", name="Virtuell", aliases={"virtual"}, floor_id=None),
    ]
    devices = [NS(id="d1", name="Hue island", name_by_user="Kücheninsel", area_id="kuche")]
    entities = [
        NS(
            entity_id="light.kuche_kucheninsel",
            name=None,
            original_name="Kücheninsel",
            aliases=set(),
            area_id=None,
            device_id="d1",
        ),
        NS(
            entity_id="light.hidden",
            name="Hidden",
            original_name=None,
            aliases=set(),
            area_id="kuche",
            device_id=None,
        ),
        NS(
            entity_id="scene.abend",
            name="Abend",
            original_name=None,
            aliases=set(),
            area_id=None,
            device_id=None,
        ),
    ]
    shape = export_shape(
        floors, areas, devices, entities, {"light.kuche_kucheninsel", "scene.abend"}
    )
    assert shape["floors"] == [{"floor_id": "ug", "name": "Untergeschoss", "aliases": ["unten"]}]
    assert shape["areas"][0] == {
        "area_id": "kuche",
        "name": "Küche",
        "aliases": [],
        "floor_id": "ug",
    }
    assert shape["devices"] == [
        {"id": "d1", "name": "Hue island", "name_by_user": "Kücheninsel", "area_id": "kuche"}
    ]
    assert [e["entity_id"] for e in shape["entities"]] == [
        "light.kuche_kucheninsel",
        "scene.abend",
    ]  # exposed only
    assert shape["exposed"] == ["light.kuche_kucheninsel", "scene.abend"]
    shape["states"] = {"light.kuche_kucheninsel": {"state": "off", "friendly_name": "Kücheninsel"}}
    home = home_from_export(shape)
    e = home.entity_by_id("light.kuche_kucheninsel")
    assert e.area_id == "kuche" and e.device_name == "Kücheninsel" and e.state == "off"
    assert [s.entity_id for s in home.scenes] == ["scene.abend"]
    assert home.floors[0].aliases == ("unten",) and home.floors[0].area_ids == ("kuche",)


GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "homes" / "julian.json"


def test_golden_export_parity():
    """Spec §6: fed the registry entries behind a real export, `export_shape` must reproduce
    the export the same `home_from_export` reads.

    `golden/homes/` is gitignored, so this runs only on a machine that has an export.
    """
    if not GOLDEN.exists():
        pytest.skip(f"no golden export at {GOLDEN}")
    export = json.loads(GOLDEN.read_text(encoding="utf-8"))
    floors = [
        NS(floor_id=f["floor_id"], name=f["name"], aliases=f.get("aliases"))
        for f in export["floors"]
    ]
    areas = [
        NS(id=a["area_id"], name=a["name"], aliases=a.get("aliases"), floor_id=a.get("floor_id"))
        for a in export["areas"]
    ]
    devices = [
        NS(
            id=d["id"],
            name=d["name"],
            name_by_user=d.get("name_by_user"),
            area_id=d.get("area_id"),
        )
        for d in export["devices"]
    ]
    entities = [
        NS(
            entity_id=e["entity_id"],
            name=e.get("name"),
            original_name=e.get("original_name"),
            aliases=e.get("aliases"),
            area_id=e.get("area_id"),
            device_id=e.get("device_id"),
            disabled_by=e.get("disabled_by"),
            hidden_by=e.get("hidden_by"),
        )
        for e in export["entities"]
    ]
    shape = export_shape(floors, areas, devices, entities, set(export["exposed"]))
    shape["states"] = export["states"]

    # `export_shape` sees the entity registry, so an exposed entity with no registry entry
    # (a YAML entity) cannot appear; the exporter lists it. Compare the same exposed set,
    # in the same order, and name the difference explicitly.
    registry_ids = {e["entity_id"] for e in export["entities"]}
    assert set(export["exposed"]) - set(shape["exposed"]) == {
        e for e in export["exposed"] if e not in registry_ids
    }
    assert home_from_export(shape) == home_from_export({**export, "exposed": shape["exposed"]})


def test_export_shape_carries_disabled_and_hidden_markers():
    from enum import StrEnum

    class Disabler(StrEnum):
        USER = "user"

    ents = [
        NS(
            entity_id="light.a",
            name=None,
            original_name="A",
            aliases=set(),
            area_id="k",
            device_id=None,
            disabled_by=Disabler.USER,
            hidden_by=None,
        ),
        NS(
            entity_id="light.b",
            name=None,
            original_name="B",
            aliases=set(),
            area_id="k",
            device_id=None,
            disabled_by=None,
            hidden_by=None,
        ),
    ]
    areas = [NS(id="k", name="Küche", aliases=set(), floor_id=None)]
    shape = export_shape([], areas, [], ents, {"light.a", "light.b"})
    by_id = {e["entity_id"]: e for e in shape["entities"]}
    assert by_id["light.a"]["disabled_by"] == "user" and by_id["light.b"]["disabled_by"] is None
    home = home_from_export(shape)
    assert [e.entity_id for e in home.entities] == ["light.b"]
