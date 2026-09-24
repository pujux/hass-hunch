from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.hunch.diagnostics import async_get_config_entry_diagnostics
from custom_components.hunch.timers import HunchTimer
from tests.integration.conftest import scripted


async def test_diagnostics_have_counts_and_traces_but_no_key(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    entry.runtime_data.traces.append(
        {"prompt": "x", "outcome": "Escalate:no_intent", "trace": {"entries": []}}
    )
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "test-key" not in str(diag)
    assert diag["home"].keys() == {"floors", "areas", "entities", "scenes"}
    assert diag["traces"][0]["prompt"] == "x"


async def test_diagnostics_list_active_timers(hass: HomeAssistant, setup_hunch):
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
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert (
        diag["timers"][0]["label"] == "Nudeln"
        and 479 <= diag["timers"][0]["remaining_seconds"] <= 480
    )
    await entry.runtime_data.timers.async_stop()
