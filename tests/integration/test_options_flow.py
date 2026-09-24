from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.integration.conftest import scripted


async def test_options_reload_the_engine_and_reject_self_as_fallback(
    hass: HomeAssistant, setup_hunch
):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    bad = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "fallback_agent": "conversation.hunch",
            "model": "jev-1.13.0",
            "response_language": "de",
            "timeout_ms": 1500,
            "max_silent_targets": 5,
            "device_round": True,
            "max_rounds": 2,
            "threshold_auto_execute": 0.8,
        },
    )
    assert bad["type"] is FlowResultType.FORM and bad["errors"] == {
        "fallback_agent": "cannot_select_self"
    }
    with patch("custom_components.hunch.build_client", return_value=client):
        ok = await hass.config_entries.options.async_configure(
            bad["flow_id"],
            {
                "model": "jev-1.13.0",
                "response_language": "de",
                "timeout_ms": 1500,
                "max_silent_targets": 5,
                "device_round": True,
                "max_rounds": 2,
                "threshold_auto_execute": 0.8,
                "timer_script": "script.timer_ansage",
            },
        )
        await hass.async_block_till_done()
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    rt = entry.runtime_data
    assert rt.response_language == "de"
    assert entry.options["max_rounds"] == 2 and entry.options["device_round"] is True
    assert entry.options["timer_script"] == "script.timer_ansage"
    assert rt.timer_script == "script.timer_ansage"
    # build_engine_config forces max_rounds to 3 with device_round on
    from custom_components.hunch import build_engine_config

    assert build_engine_config(entry.options).max_rounds == 3
    assert build_engine_config(entry.options).thresholds.auto_execute == 0.8
