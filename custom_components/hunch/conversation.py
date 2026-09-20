"""Hunch conversation entity."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HunchConversationEntity(entry)])


class HunchConversationEntity(conversation.ConversationEntity):
    _attr_has_entity_name = False
    _attr_name = "Hunch"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        return await conversation.async_converse(
            self.hass,
            user_input.text,
            user_input.conversation_id,
            user_input.context,
            language=user_input.language,
            agent_id=None,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
        )
