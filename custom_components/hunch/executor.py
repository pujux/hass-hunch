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
class ConditionCheck:
    holds: bool | None  # None: the subject has no state, or no number where one is needed
    value: str | None
    unit: str | None


@dataclass(frozen=True)
class StateReading:
    entity_id: str
    name: str
    area_id: str | None
    state: str | None
    unit: str | None
    device_class: str | None
    # to-do lists: the open items (their state is only a count)
    items: tuple[str, ...] | None = None


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

    def check_condition(self, condition: Condition) -> ConditionCheck:
        """Plain equality for a state condition; a numeric comparison with the live value for
        a threshold condition ("< 20")."""
        st = self._hass.states.get(condition.subject.entity_id)
        if st is None:
            return ConditionCheck(None, None, None)
        unit = st.attributes.get("unit_of_measurement")
        raw: str | None = st.state
        if condition.subject.domain == "weather":
            # a weather entity's state is the sky ("sunny"); the number people mean is its
            # temperature attribute
            temp = st.attributes.get("temperature")
            raw = None if temp is None else str(temp)
            unit = st.attributes.get("temperature_unit") or unit
        if condition.operator is not None and condition.threshold is not None:
            try:
                value = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return ConditionCheck(None, raw, unit)
            holds = (
                value < condition.threshold
                if condition.operator == "<"
                else value > condition.threshold
            )
            return ConditionCheck(holds, raw, unit)
        return ConditionCheck(st.state == condition.expected_state, st.state, unit)

    def condition_holds(self, condition: Condition) -> bool | None:
        return self.check_condition(condition).holds

    async def read_states(self, entities: Sequence[Entity]) -> list[StateReading]:
        out = []
        for e in entities:
            st = self._hass.states.get(e.entity_id)
            attrs = st.attributes if st is not None else {}
            items = await self._todo_items(e.entity_id) if e.domain == "todo" else None
            out.append(
                StateReading(
                    e.entity_id,
                    e.name,
                    e.area_id,
                    st.state if st else None,
                    attrs.get("unit_of_measurement"),
                    attrs.get("device_class"),
                    items,
                )
            )
        return out

    async def _todo_items(self, entity_id: str) -> tuple[str, ...] | None:
        """Open items of a to-do list via todo.get_items; None when the list cannot be read."""
        try:
            response = await self._hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": entity_id, "status": ["needs_action"]},
                blocking=True,
                return_response=True,
            )
        except (Unauthorized, ServiceNotFound, HomeAssistantError):
            return None
        if not isinstance(response, dict):
            return None
        entry = response.get(entity_id) or {}
        return tuple(str(i.get("summary", "")) for i in entry.get("items", []) if i.get("summary"))
