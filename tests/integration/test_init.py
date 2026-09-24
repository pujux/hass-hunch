from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.hunch.const import TIMER_STORE_KEY
from custom_components.hunch.timers import HunchTimer
from tests.integration.conftest import scripted


async def test_entry_sets_up_a_conversation_entity(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    state = hass.states.get("conversation.hunch")
    assert state is not None
    assert entry.runtime_data.engine is not None
    assert entry.runtime_data.fallback_agent_id is None


async def test_unload_leaves_the_entity_unavailable(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("conversation.hunch")
    assert state is not None
    assert state.state == "unavailable"


async def test_unload_closes_the_client_and_removes_the_registry_listeners(
    hass: HomeAssistant, setup_hunch
):
    client, calls = scripted({})
    client.aclose = AsyncMock()
    entry, _ = await setup_hunch(client, calls)
    builder = entry.runtime_data.builder
    assert builder._unsubs  # the registry listeners are live while the entry is loaded
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert client.aclose.await_count == 1
    assert builder._unsubs == []


async def test_a_failed_platform_unload_leaves_the_client_open(hass: HomeAssistant, setup_hunch):
    """A half-unloaded entry keeps serving turns; a closed client would break them."""
    client, calls = scripted({})
    client.aclose = AsyncMock()
    entry, _ = await setup_hunch(client, calls)
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert not await hass.config_entries.async_unload(entry.entry_id)
    assert client.aclose.await_count == 0


async def test_unload_keeps_the_timer_store_file(hass: HomeAssistant, setup_hunch, hass_storage):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    await entry.runtime_data.timers.async_add(
        HunchTimer(
            timer_id="t",
            kind="timer",
            label="Nudeln",
            description=None,
            duration_seconds=480,
            due_at=dt_util.utcnow() + timedelta(seconds=480),
            actions=(),
            language="de",
            conversation_id=None,
            device_id=None,
            satellite_id=None,
            area_id=None,
            user_id=None,
        )
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass_storage[TIMER_STORE_KEY]["data"]["timers"]) == 1


async def test_entity_declares_home_control(hass: HomeAssistant, setup_hunch):
    from homeassistant.components import conversation

    client, calls = scripted({})
    await setup_hunch(client, calls)
    state = hass.states.get("conversation.hunch")
    assert state is not None
    assert state.attributes["supported_features"] & conversation.ConversationEntityFeature.CONTROL
