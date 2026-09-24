"""Diagnostics: options (no key), home size, recent traces."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HunchConfigEntry
from .const import CONF_API_KEY


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HunchConfigEntry
) -> dict[str, Any]:
    rt = entry.runtime_data
    home = rt.builder.build()
    return {
        "options": async_redact_data(entry.options, {CONF_API_KEY}),
        "home": {
            "floors": len(home.floors),
            "areas": len(home.areas),
            "entities": len(home.entities),
            "scenes": len(home.scenes),
        },
        "traces": list(rt.traces),
        "timers": [
            {
                "kind": t.kind,
                "label": t.label,
                "description": t.description,
                "remaining_seconds": round(rt.timers.remaining(t)),
            }
            for t in rt.timers.active()
        ],
    }
