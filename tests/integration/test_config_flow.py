from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hunch.config_flow import CannotConnect, InvalidAuth, UnknownModel
from custom_components.hunch.const import DOMAIN


async def test_user_step_creates_the_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with (
        patch("custom_components.hunch.config_flow.async_validate_api_key", return_value=None),
        patch("custom_components.hunch.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": "k"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hunch"
    assert result["data"] == {"api_key": "k"}


async def test_errors_map_to_form_errors(hass: HomeAssistant):
    for exc, key in (
        (InvalidAuth, "invalid_auth"),
        (UnknownModel, "unknown_model"),
        (CannotConnect, "cannot_connect"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        with patch("custom_components.hunch.config_flow.async_validate_api_key", side_effect=exc):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"api_key": "k"}
            )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": key}


async def test_single_instance(hass: HomeAssistant, setup_hunch):
    from tests.integration.conftest import scripted

    client, calls = scripted({})
    await setup_hunch(client, calls)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
