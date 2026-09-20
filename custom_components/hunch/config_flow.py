"""Config flow for Hunch (filled in by Task 3).

HA 2026.9 requires a `config_flow` platform module to be importable for any domain
whose manifest sets `"config_flow": true`, even before a config entry's user-facing
setup steps exist -- `hass.config_entries.async_setup` fails immediately otherwise.
This is the minimal stub that satisfies that; Task 3 replaces the body with the
actual user step(s).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class HunchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
