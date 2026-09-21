"""Hunch: a Jev-backed fast path in front of your conversation agent."""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from hunch import DEFAULT_VOCABULARY, DecisionClient, Engine, EngineConfig, Thresholds
from hunch.client import TypeSafeDecisionClient

from .const import (
    CONF_API_KEY,
    DEFAULT_MODEL,
    DEFAULT_RESPONSE_LANGUAGE,
    DEFAULT_TIMEOUT_MS,
    OPT_DEVICE_ROUND,
    OPT_FALLBACK_AGENT,
    OPT_MAX_ROUNDS,
    OPT_MAX_SILENT_TARGETS,
    OPT_MODEL,
    OPT_RESPONSE_LANGUAGE,
    OPT_TIMEOUT_MS,
    THRESHOLD_FIELDS,
    TRACE_BUFFER,
)
from .home_model import HomeModelBuilder
from .pending import LastTurnStore, PendingStore

PLATFORMS = [Platform.CONVERSATION]


@dataclass
class HunchRuntime:
    client: DecisionClient
    engine: Engine
    builder: HomeModelBuilder
    pending: PendingStore
    last_turns: LastTurnStore
    traces: deque[dict[str, Any]]
    fallback_agent_id: str | None
    response_language: str
    clarify_max_candidates: int


type HunchConfigEntry = ConfigEntry[HunchRuntime]


def build_engine_config(options: Mapping[str, Any]) -> EngineConfig:
    """Build an EngineConfig from a config entry's options.

    Thresholds are stored flat as `threshold_<name>` keys; any name absent from
    `options` keeps its `Thresholds` default.
    """
    defaults = Thresholds()
    overrides = {
        name: float(options[f"threshold_{name}"])
        for name in THRESHOLD_FIELDS
        if f"threshold_{name}" in options
    }
    thresholds = dataclasses.replace(defaults, **overrides)
    device_round = bool(options.get(OPT_DEVICE_ROUND, False))
    max_rounds = int(options.get(OPT_MAX_ROUNDS, 2))
    if device_round and max_rounds < 3:
        max_rounds = 3
    return EngineConfig(
        model=options.get(OPT_MODEL, DEFAULT_MODEL),
        thresholds=thresholds,
        max_rounds=max_rounds,
        max_silent_targets=int(options.get(OPT_MAX_SILENT_TARGETS, 20)),
        device_round=device_round,
    )


def build_client(entry: ConfigEntry) -> DecisionClient:
    """The real Jev client. Tests patch this to inject a FakeDecisionClient."""
    return TypeSafeDecisionClient(
        model=entry.options.get(OPT_MODEL, DEFAULT_MODEL),
        api_key=entry.data[CONF_API_KEY],
        timeout_ms=int(entry.options.get(OPT_TIMEOUT_MS, DEFAULT_TIMEOUT_MS)),
    )


async def async_setup_entry(hass: HomeAssistant, entry: HunchConfigEntry) -> bool:
    client = build_client(entry)
    builder = HomeModelBuilder(hass, DEFAULT_VOCABULARY)
    builder.async_start()
    entry.async_on_unload(builder.async_stop)
    engine_config = build_engine_config(entry.options)
    entry.runtime_data = HunchRuntime(
        client=client,
        engine=Engine(client, DEFAULT_VOCABULARY, engine_config),
        builder=builder,
        pending=PendingStore(),
        last_turns=LastTurnStore(),
        traces=deque(maxlen=TRACE_BUFFER),
        fallback_agent_id=entry.options.get(OPT_FALLBACK_AGENT) or None,
        response_language=entry.options.get(OPT_RESPONSE_LANGUAGE, DEFAULT_RESPONSE_LANGUAGE),
        clarify_max_candidates=engine_config.clarify_max_candidates,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HunchConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not ok:  # the entry stays loaded; a closed client would make it useless
        return False
    aclose = getattr(entry.runtime_data.client, "aclose", None)
    if aclose is not None:
        await aclose()
    return True
