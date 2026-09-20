"""Runs planned service calls with the caller's context; reads states for queries/conditions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound, Unauthorized
from hunch import Action, Condition, Entity

from .service_map import plan_calls


@dataclass(frozen=True)
class TargetResult:
    entity_id: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class StateReading:
    entity_id: str
    name: str
    area_id: str | None
    state: str | None
    unit: str | None
    device_class: str | None


class Executor:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def execute(self, actions: Sequence[Action], context: Context) -> list[TargetResult]:
        results: list[TargetResult] = []
        for action in actions:
            for call in plan_calls(action):
                ids: list[str] = call.data["entity_id"]
                try:
                    await self._hass.services.async_call(
                        call.domain, call.service, call.data, blocking=True, context=context
                    )
                except (Unauthorized, ServiceNotFound, HomeAssistantError) as err:
                    results.extend(TargetResult(i, False, type(err).__name__) for i in ids)
                else:
                    results.extend(TargetResult(i, True) for i in ids)
        return results

    def condition_holds(self, condition: Condition) -> bool | None:
        st = self._hass.states.get(condition.subject.entity_id)
        if st is None:
            return None
        return st.state == condition.expected_state

    def read_states(self, entities: Sequence[Entity]) -> list[StateReading]:
        out = []
        for e in entities:
            st = self._hass.states.get(e.entity_id)
            attrs = st.attributes if st is not None else {}
            out.append(
                StateReading(
                    e.entity_id,
                    e.name,
                    e.area_id,
                    st.state if st else None,
                    attrs.get("unit_of_measurement"),
                    attrs.get("device_class"),
                )
            )
        return out
