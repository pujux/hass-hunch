from types import SimpleNamespace as NS

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
