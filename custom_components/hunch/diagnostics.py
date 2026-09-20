"""Diagnostics: options (no key), home size, recent traces."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HunchConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HunchConfigEntry
) -> dict[str, Any]:
    rt = entry.runtime_data
    home = rt.builder.build()
    return {
        "options": dict(entry.options),
        "home": {
            "floors": len(home.floors),
            "areas": len(home.areas),
            "entities": len(home.entities),
            "scenes": len(home.scenes),
        },
        "traces": list(rt.traces),
    }
