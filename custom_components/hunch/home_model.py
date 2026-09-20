"""HomeModel from HA registries: cached skeleton, live states stamped per request."""

from __future__ import annotations

from typing import Any

from homeassistant.components.homeassistant.exposed_entities import (
    async_listen_entity_updates,
    async_should_expose,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from hunch import HomeModel, Vocabulary, home_from_export

from .home_shape import export_shape

ASSISTANT = "conversation"


class HomeModelBuilder:
    def __init__(self, hass: HomeAssistant, vocabulary: Vocabulary) -> None:
        self._hass = hass
        self._vocabulary = vocabulary
        self._skeleton: dict[str, Any] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    @callback
    def async_start(self) -> None:
        bus = self._hass.bus
        for event in (
            ar.EVENT_AREA_REGISTRY_UPDATED,
            fr.EVENT_FLOOR_REGISTRY_UPDATED,
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
        ):
            self._unsubs.append(bus.async_listen(event, self._on_event))
        self._unsubs.append(async_listen_entity_updates(self._hass, ASSISTANT, self.invalidate))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _on_event(self, _event: Event) -> None:
        self.invalidate()

    @callback
    def invalidate(self) -> None:
        self._skeleton = None

    def snapshot(self) -> dict[str, Any]:
        if self._skeleton is None:
            entities = er.async_get(self._hass).entities.values()
            exposed = {
                e.entity_id
                for e in entities
                if async_should_expose(self._hass, ASSISTANT, e.entity_id)
            }
            self._skeleton = export_shape(
                fr.async_get(self._hass).async_list_floors(),
                ar.async_get(self._hass).async_list_areas(),
                dr.async_get(self._hass).devices.values(),
                entities,
                exposed,
            )
        return self._skeleton

    def build(self) -> HomeModel:
        shape = dict(self.snapshot())
        states: dict[str, dict[str, Any]] = {}
        for eid in shape["exposed"]:
            st = self._hass.states.get(eid)
            if st is not None:
                states[eid] = {"state": st.state, "friendly_name": st.name}
        shape["states"] = states
        return home_from_export(shape, self._vocabulary)
