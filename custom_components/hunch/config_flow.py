"""Config flow: the API key. Everything else lives in the options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import CONF_API_KEY, DEFAULT_MODEL, DOMAIN


class InvalidAuth(HomeAssistantError):
    """The key was rejected."""


class UnknownModel(HomeAssistantError):
    """The pinned model does not exist."""


class CannotConnect(HomeAssistantError):
    """Network or timeout."""


async def async_validate_api_key(hass: HomeAssistant, api_key: str, model: str) -> None:
    """One cheap Jev call. Raises one of the three errors above.

    Talks to the SDK client directly (rather than through
    `hunch.client.TypeSafeDecisionClient`) so the HTTP status code on a failure is
    still visible here; `TypeSafeDecisionClient.ask` collapses every `TypeSafeError`
    into a single `DecisionBackendError` reason, which loses the distinction between
    "bad key", "unknown model", and "network/timeout" that the three exceptions below
    need.
    """
    from typesafe_sdk import AsyncTypeSafeClient, Noul, TypeSafeError

    client = AsyncTypeSafeClient(api_key=api_key, model=model)
    try:
        await client.system_one(
            state={"probe": True},
            questions={"probe": Noul(instructions="Is `probe` true?")},
            model=model,
        )
    except TypeSafeError as err:
        status = getattr(err, "status", None)
        if status in (401, 403):
            raise InvalidAuth from err
        if status == 404 or "model" in str(err).lower():
            raise UnknownModel from err
        raise CannotConnect from err
    except Exception as err:  # noqa: BLE001 - timeouts and transport errors
        raise CannotConnect from err
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()


STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


class HunchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # raise_on_progress=False: this flow only cares about an already-*configured*
        # entry (handled by `_abort_if_unique_id_configured` below); concurrent
        # in-progress attempts are not a concern for a single-instance integration.
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await async_validate_api_key(self.hass, user_input[CONF_API_KEY], DEFAULT_MODEL)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except UnknownModel:
                errors["base"] = "unknown_model"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Hunch", data={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HunchOptionsFlow:
        return HunchOptionsFlow()


class HunchOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Task 9 fills the form; for now: save whatever comes in.
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))
