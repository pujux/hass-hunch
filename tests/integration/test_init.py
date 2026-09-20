from homeassistant.core import HomeAssistant

from tests.integration.conftest import scripted


async def test_entry_sets_up_a_conversation_entity(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    state = hass.states.get("conversation.hunch")
    assert state is not None
    assert entry.runtime_data.engine is not None
    assert entry.runtime_data.fallback_agent_id is None


async def test_unload_removes_the_entity(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("conversation.hunch") is None
