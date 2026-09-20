from homeassistant.core import HomeAssistant

from custom_components.hunch.diagnostics import async_get_config_entry_diagnostics
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
