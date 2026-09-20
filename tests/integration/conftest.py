"""HA-harness fixtures. Run with: uv run --group ha pytest tests/integration"""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from hunch import FakeDecisionClient
from hunch.questions import ChoiceA, ChoiceQ, NoulA, ScoreA, ScoreQ
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hunch.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def scripted(round1: dict, round2: dict | None = None, reply: dict | None = None):
    """A FakeDecisionClient answering engine questions by id, and reply-judgment questions
    (state has a 'reply' key) from `reply`. Everything unlisted answers 'no'."""
    calls: list[tuple[dict, dict]] = []

    def script(state, qs):
        calls.append((state, dict(qs)))
        out = {}
        for qid, q in qs.items():
            if "reply" in state and reply and qid in reply:
                out[qid] = reply[qid]
            elif qid in round1:
                out[qid] = round1[qid]
            elif round2 and qid in round2:
                out[qid] = round2[qid]
            elif qid in ("verb_primary", "area_primary"):
                out[qid] = ChoiceA("several", 0.9, {})
            elif isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
            elif isinstance(q, ScoreQ):
                out[qid] = ScoreA(1.0, 0.9, {})
            else:
                out[qid] = NoulA(0.05)
        return out

    return FakeDecisionClient(script), calls


@pytest.fixture
async def setup_hunch(hass: HomeAssistant):
    """Set up homeassistant + conversation, then a Hunch entry with the given fake client.
    Usage: entry, calls = await setup_hunch(client, calls, options={...})"""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})

    async def _setup(client, calls, options=None):
        entry = MockConfigEntry(
            domain=DOMAIN, data={"api_key": "test-key"}, options=options or {}, title="Hunch"
        )
        entry.add_to_hass(hass)
        with patch("custom_components.hunch.build_client", return_value=client):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return entry, calls

    return _setup
