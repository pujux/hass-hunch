from hunch.loaders import home_from_export

EXPORT = {
    "floors": [{"floor_id": "eg", "name": "Erdgeschoss", "aliases": ["unten"], "level": 0}],
    "areas": [
        {"area_id": "kueche", "name": "Küche", "aliases": [], "floor_id": "eg"},
        {"area_id": "buero", "name": "Büro", "aliases": ["Arbeitszimmer"], "floor_id": None},
    ],
    "devices": [
        {"id": "dev1", "name": "Hue Go", "name_by_user": "Küchenlampe", "area_id": "kueche"},
        {"id": "dev2", "name": "Shelly Plug", "name_by_user": None, "area_id": None},
    ],
    "entities": [
        # inherits area from device; name from registry
        {
            "entity_id": "light.hue_go",
            "name": "Küchenlicht",
            "original_name": "Hue Go",
            "aliases": ["Lampe"],
            "area_id": None,
            "device_id": "dev1",
        },
        # own area overrides device area; no registry name -> original_name
        {
            "entity_id": "switch.shelly",
            "name": None,
            "original_name": "Shelly Plug",
            "aliases": [],
            "area_id": "buero",
            "device_id": "dev2",
        },
        # exposed scene, no device
        {
            "entity_id": "scene.film",
            "name": "Filmabend",
            "original_name": None,
            "aliases": [],
            "area_id": None,
            "device_id": None,
        },
        # NOT exposed -> must be dropped
        {
            "entity_id": "light.hidden",
            "name": "Hidden",
            "original_name": None,
            "aliases": [],
            "area_id": "kueche",
            "device_id": None,
        },
        # exposed but not in registry (e.g. YAML entity) -> friendly_name from state
        # (appears only in `exposed` and `states`)
    ],
    "exposed": ["light.hue_go", "switch.shelly", "scene.film", "sensor.yaml_temp"],
    "states": {
        "light.hue_go": {"state": "on", "friendly_name": "Hue Go"},
        "switch.shelly": {"state": "off", "friendly_name": "Shelly Plug"},
        "scene.film": {"state": "unknown", "friendly_name": "Filmabend"},
        "sensor.yaml_temp": {"state": "21.5", "friendly_name": "Außentemperatur"},
    },
}


def test_floors_get_their_areas():
    home = home_from_export(EXPORT)
    assert home.floors[0].floor_id == "eg" and home.floors[0].area_ids == ("kueche",)
    assert home.area_by_id("buero").aliases == ("Arbeitszimmer",)


def test_only_exposed_entities_survive():
    home = home_from_export(EXPORT)
    ids = {e.entity_id for e in home.entities}
    assert "light.hidden" not in ids
    assert ids == {"light.hue_go", "switch.shelly", "sensor.yaml_temp"}


def test_area_inherits_from_device_unless_entity_has_its_own():
    home = home_from_export(EXPORT)
    assert home.entity_by_id("light.hue_go").area_id == "kueche"
    assert home.entity_by_id("switch.shelly").area_id == "buero"


def test_names_and_device_names_prefer_user_values():
    home = home_from_export(EXPORT)
    lamp = home.entity_by_id("light.hue_go")
    assert (
        lamp.name == "Küchenlicht"
        and lamp.device_name == "Küchenlampe"
        and lamp.aliases == ("Lampe",)
    )
    assert home.entity_by_id("switch.shelly").name == "Shelly Plug"
    assert home.entity_by_id("sensor.yaml_temp").name == "Außentemperatur"


def test_verbs_state_and_scenes():
    home = home_from_export(EXPORT)
    assert {"turn_on", "turn_off", "set_brightness", "query_state"} <= home.entity_by_id(
        "light.hue_go"
    ).verbs
    assert home.entity_by_id("sensor.yaml_temp").verbs == frozenset({"query_state"})
    assert home.entity_by_id("light.hue_go").state == "on"
    assert [s.entity_id for s in home.scenes] == ["scene.film"]
    assert "scene" not in home.domains
