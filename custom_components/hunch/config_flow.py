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
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConversationAgentSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from hunch import Thresholds

from .const import (
    CONF_API_KEY,
    DEFAULT_MODEL,
    DEFAULT_RESPONSE_LANGUAGE,
    DEFAULT_TIMEOUT_MS,
    DOMAIN,
    OPT_DEVICE_ROUND,
    OPT_FALLBACK_AGENT,
    OPT_MAX_ROUNDS,
    OPT_MAX_SILENT_TARGETS,
    OPT_MODEL,
    OPT_RESPONSE_LANGUAGE,
    OPT_TIMEOUT_MS,
    OPT_TIMER_SCRIPT,
    THRESHOLD_FIELDS,
)

PROBE_TIMEOUT_S = 5.0  # the config flow must not hang on a dead backend


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

    client = AsyncTypeSafeClient(api_key=api_key, model=model, timeout=PROBE_TIMEOUT_S)
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
        if status == 404:
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


def _options_schema() -> vol.Schema:
    defaults = Thresholds()
    fields: dict[Any, Any] = {
        vol.Optional(OPT_FALLBACK_AGENT): ConversationAgentSelector(),
        vol.Optional(OPT_TIMER_SCRIPT): EntitySelector(EntitySelectorConfig(domain="script")),
        vol.Optional(OPT_MODEL, default=DEFAULT_MODEL): TextSelector(),
        vol.Optional(OPT_RESPONSE_LANGUAGE, default=DEFAULT_RESPONSE_LANGUAGE): SelectSelector(
            SelectSelectorConfig(
                options=["auto", "en", "de"],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="response_language",
            )
        ),
        vol.Optional(OPT_TIMEOUT_MS, default=DEFAULT_TIMEOUT_MS): NumberSelector(
            NumberSelectorConfig(min=300, max=10000, step=100, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(OPT_MAX_SILENT_TARGETS, default=20): NumberSelector(
            NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(OPT_DEVICE_ROUND, default=False): BooleanSelector(),
        vol.Optional(OPT_MAX_ROUNDS, default=2): NumberSelector(
            NumberSelectorConfig(min=2, max=4, step=1, mode=NumberSelectorMode.BOX)
        ),
    }
    for name in THRESHOLD_FIELDS:
        fields[vol.Optional(f"threshold_{name}", default=getattr(defaults, name))] = NumberSelector(
            NumberSelectorConfig(min=0, max=1, step=0.05, mode=NumberSelectorMode.BOX)
        )
    return vol.Schema(fields)


class HunchOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            own = self._own_entity_id()
            if own and user_input.get(OPT_FALLBACK_AGENT) == own:
                errors[OPT_FALLBACK_AGENT] = "cannot_select_self"
            else:
                for key in (OPT_TIMEOUT_MS, OPT_MAX_SILENT_TARGETS, OPT_MAX_ROUNDS):
                    if key in user_input:
                        user_input[key] = int(user_input[key])
                return self.async_create_entry(data=user_input)
        schema = self.add_suggested_values_to_schema(
            _options_schema(), user_input or self.config_entry.options
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _own_entity_id(self) -> str | None:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(self.hass)
        return reg.async_get_entity_id("conversation", DOMAIN, self.config_entry.entry_id)
