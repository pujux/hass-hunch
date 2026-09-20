"""HomeModel from HA registries (filled in by Task 4)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from hunch import HomeModel, Vocabulary


class HomeModelBuilder:
    def __init__(self, hass: HomeAssistant, vocabulary: Vocabulary) -> None:
        self._hass = hass
        self._vocabulary = vocabulary

    @callback
    def async_start(self) -> None: ...

    @callback
    def async_stop(self) -> None: ...

    def build(self) -> HomeModel:
        return HomeModel(floors=(), areas=(), entities=(), scenes=())
