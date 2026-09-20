from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.setup import async_setup_component
from hunch import DEFAULT_VOCABULARY
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hunch.home_model import HomeModelBuilder


async def _populate(hass: HomeAssistant):
    floor = fr.async_get(hass).async_create("Untergeschoss", aliases={"unten"})
    area = ar.async_get(hass).async_create("Küche", floor_id=floor.floor_id)
    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("test", "d1")}, name="Hue island"
    )
    dr.async_get(hass).async_update_device(dev.id, area_id=area.id, name_by_user="Kücheninsel")
    reg = er.async_get(hass)
    e1 = reg.async_get_or_create(
        "light",
        "test",
        "1",
        suggested_object_id="kuche_kucheninsel",
        original_name="Kücheninsel",
        device_id=dev.id,
        config_entry=None,
    )
    e2 = reg.async_get_or_create(
        "light", "test", "2", suggested_object_id="hidden", original_name="Hidden"
    )
    # e3: a light (default-exposed domain), never explicitly exposed/hidden.
    e3 = reg.async_get_or_create(
        "light", "test", "3", suggested_object_id="default_exposed", original_name="Default Exposed"
    )
    # e4: a sensor (not in HA's default-exposed domains), never explicitly exposed/hidden.
    e4 = reg.async_get_or_create(
        "sensor",
        "test",
        "4",
        suggested_object_id="not_default_exposed",
        original_name="Not Default Exposed",
    )
    hass.states.async_set(e1.entity_id, "off")
    hass.states.async_set(e2.entity_id, "on")
    hass.states.async_set(e3.entity_id, "on")
    hass.states.async_set(e4.entity_id, "42")
    return e1, e2, e3, e4


async def test_builder_uses_exposed_entities_and_live_state(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    e1, e2, e3, e4 = await _populate(hass)
    area = ar.async_get(hass).async_get_area_by_name("Küche")
    async_expose_entity(hass, "conversation", e1.entity_id, True)
    # e2 is a light (default-exposed domain) — hide it explicitly so the initial
    # "only e1" assertion below stays meaningful despite default exposure.
    async_expose_entity(hass, "conversation", e2.entity_id, False)
    b = HomeModelBuilder(hass, DEFAULT_VOCABULARY)
    b.async_start()
    home = b.build()
    # e1 explicitly exposed, e3 default-exposed (light), e2 explicitly hidden,
    # e4 not default-exposed (sensor) and never touched.
    assert {e.entity_id for e in home.entities} == {e1.entity_id, e3.entity_id}
    ent = next(e for e in home.entities if e.entity_id == e1.entity_id)
    assert ent.area_id == area.id and ent.device_name == "Kücheninsel" and ent.state == "off"
    assert home.floors[0].aliases == ("unten",)
    hass.states.async_set(e1.entity_id, "on")
    assert next(e for e in b.build().entities if e.entity_id == e1.entity_id).state == "on"
    # states are never cached
    # exposure change invalidates the skeleton
    async_expose_entity(hass, "conversation", e2.entity_id, True)
    await hass.async_block_till_done()
    assert {e.entity_id for e in b.build().entities} == {e1.entity_id, e2.entity_id, e3.entity_id}
    b.async_stop()
