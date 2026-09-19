import pytest

from hunch.model import Area, Entity, Floor, HomeModel

LIGHT_VERBS = frozenset({"turn_on", "turn_off", "set_brightness", "query_state"})
COVER_VERBS = frozenset({"open", "close", "set_position", "query_state"})
LOCK_VERBS = frozenset({"lock", "unlock", "query_state"})
SWITCH_VERBS = frozenset({"turn_on", "turn_off", "query_state"})
CLIMATE_VERBS = frozenset({"set_temperature", "query_state"})
SCENE_VERBS = frozenset({"activate"})


def _e(entity_id, name, area, device=None, verbs=LIGHT_VERBS, state="off", aliases=()):
    domain = entity_id.split(".")[0]
    return Entity(
        entity_id=entity_id,
        domain=domain,
        name=name,
        aliases=tuple(aliases),
        area_id=area,
        device_id=f"dev_{device}" if device else None,
        device_name=device,
        verbs=verbs,
        state=state,
    )


@pytest.fixture
def home() -> HomeModel:
    return HomeModel(
        floors=(
            Floor("downstairs", "Downstairs", ("kitchen", "living", "hallway")),
            Floor("upstairs", "Upstairs", ("bedroom", "office")),
        ),
        areas=(
            Area("kitchen", "Kitchen", (), "downstairs"),
            Area("living", "Living room", ("lounge",), "downstairs"),
            Area("hallway", "Hallway", (), "downstairs"),
            Area("bedroom", "Bedroom", (), "upstairs"),
            Area("office", "Office", ("study",), "upstairs"),
        ),
        entities=(
            _e("light.kitchen_ceiling", "Kitchen ceiling", "kitchen", "Kitchen ceiling"),
            _e("light.kitchen_counter", "Counter strip", "kitchen", "Counter strip"),
            _e("switch.fridge", "Fridge", "kitchen", "Fridge", SWITCH_VERBS, "on"),
            _e("light.living_main", "Living room main", "living", "Living room main"),
            _e("light.reading_lamp", "Reading lamp", "living", "Reading lamp", aliases=("lamp",)),
            _e("cover.living_blinds", "Living room blinds", "living", "Blinds", COVER_VERBS, "open"),
            _e("light.hallway", "Hallway light", "hallway", "Hallway light"),
            _e("lock.front_door", "Front door", "hallway", "Front door", LOCK_VERBS, "locked"),
            _e("light.bedroom_left", "Bedside left", "bedroom", "Bedside lamps"),
            _e("light.bedroom_right", "Bedside right", "bedroom", "Bedside lamps"),
            _e("climate.bedroom", "Bedroom thermostat", "bedroom", "Thermostat", CLIMATE_VERBS, "heat"),
            _e("light.office_desk", "Desk lamp", "office", "Desk lamp"),
            _e("light.christmas_tree", "Christmas tree", None, None),
        ),
        scenes=(
            _e("scene.movie_night", "Movie night", "living", None, SCENE_VERBS, None),
            _e("script.goodnight", "Goodnight", None, None, SCENE_VERBS, None),
        ),
    )
