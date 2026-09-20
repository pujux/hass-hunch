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
    hass.states.async_set(e1.entity_id, "off")
    hass.states.async_set(e2.entity_id, "on")
    return e1, e2


async def test_builder_uses_exposed_entities_and_live_state(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    e1, e2 = await _populate(hass)
    async_expose_entity(hass, "conversation", e1.entity_id, True)
    b = HomeModelBuilder(hass, DEFAULT_VOCABULARY)
    b.async_start()
    home = b.build()
    assert [e.entity_id for e in home.entities] == [e1.entity_id]
    ent = home.entities[0]
    assert ent.area_id is not None and ent.device_name == "Kücheninsel" and ent.state == "off"
    assert home.floors[0].aliases == ("unten",)
    hass.states.async_set(e1.entity_id, "on")
    assert b.build().entities[0].state == "on"  # states are never cached
    # exposure change invalidates the skeleton
    async_expose_entity(hass, "conversation", e2.entity_id, True)
    await hass.async_block_till_done()
    assert {e.entity_id for e in b.build().entities} == {e1.entity_id, e2.entity_id}
    b.async_stop()
