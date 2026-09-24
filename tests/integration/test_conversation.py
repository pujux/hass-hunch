"""The conversation entity: decide, execute, ask, escalate — spec §7."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util
from hunch import DEFAULT_VOCABULARY, Action, NeedsClarification, Resolved, Timing, Trace
from hunch.questions import ChoiceA, NoulA
from hunch.round2 import NO_MATCH
from hunch.timing import (
    ALL_TIMERS,
    DELAYED,
    FOR_DURATION,
    MINUTES,
    TIMER_CANCEL,
    TIMER_REMAINING,
    TIMER_START,
)
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.hunch.const import EVENT_TIMER_FINISHED, TIMER_STORE_KEY
from custom_components.hunch.executor import TargetResult
from custom_components.hunch.pending import PendingConfirm
from custom_components.hunch.timers import HunchTimer, StoredAction
from tests.integration.conftest import scripted

AGENT = "conversation.hunch"
# Bound before any test patches the module attribute, so `_say` always reaches the real
# entry point even while `conversation.async_converse` is mocked for the fallback.
_converse = conversation.async_converse


async def _home(hass: HomeAssistant):
    """Küche with two lights and a window contact; Galerie with a door contact."""
    areas = ar.async_get(hass)
    kuche = areas.async_create("Küche")
    galerie = areas.async_create("Galerie")
    reg = er.async_get(hass)
    made = []
    for domain, uid, obj, name, area, state, attrs in (
        ("light", "1", "kuche_spots", "Spots", kuche.id, "on", {}),
        ("light", "2", "kuche_kucheninsel", "Kücheninsel", kuche.id, "off", {}),
        (
            "binary_sensor",
            "3",
            "kuche_fenster",
            "Fenster",
            kuche.id,
            "off",
            {"device_class": "window"},
        ),
        (
            "binary_sensor",
            "4",
            "galerie_tur",
            "Tür",
            galerie.id,
            "on",
            {"device_class": "door"},
        ),
    ):
        e = reg.async_get_or_create(
            domain, "test", uid, suggested_object_id=obj, original_name=name
        )
        reg.async_update_entity(e.entity_id, area_id=area)
        hass.states.async_set(e.entity_id, state, attrs)
        async_expose_entity(hass, "conversation", e.entity_id, True)
        made.append(e.entity_id)
    return made


async def _say(hass, text, conversation_id=None, language="de"):
    return await _converse(
        hass, text, conversation_id, Context(user_id="u"), language=language, agent_id=AGENT
    )


def _speech(result):
    return result.response.speech["plain"]["speech"]


def _fallback_result(conversation_id="c9"):
    return conversation.ConversationResult(
        response=intent.IntentResponse(language="de"), conversation_id=conversation_id
    )


R1_TURN_OFF_KITCHEN = {
    "verb:turn_off": NoulA(0.95),
    "domain:light": NoulA(0.95),
    "area:kuche": NoulA(0.99),
    "flag:collective": NoulA(0.9),
    "verb_primary": ChoiceA("turn_off", 0.95, {}),
    "area_primary": ChoiceA("Küche", 0.99, {}),
}

R1_TURN_ON_ONE = {
    "verb:turn_on": NoulA(0.95),
    "domain:light": NoulA(0.95),
    "area:kuche": NoulA(0.99),
    "flag:names_specific": NoulA(0.9),
    "verb_primary": ChoiceA("turn_on", 0.95, {}),
    "area_primary": ChoiceA("Küche", 0.99, {}),
}
R2_WEAK_PICK = {
    "target:turn_on": ChoiceA("Spots", 0.45, {"Spots": 0.45, "Kücheninsel": 0.4}),
    "all_of:turn_on": NoulA(0.1),
}


async def _cover(hass: HomeAssistant):
    """A Küche blind, for plans that mix verbs."""
    areas = ar.async_get(hass)
    kuche = areas.async_get_area_by_name("Küche")
    reg = er.async_get(hass)
    e = reg.async_get_or_create(
        "cover", "test", "5", suggested_object_id="kuche_rollo", original_name="Rollo"
    )
    reg.async_update_entity(e.entity_id, area_id=kuche.id)
    hass.states.async_set(e.entity_id, "open", {"device_class": "blind"})
    async_expose_entity(hass, "conversation", e.entity_id, True)
    return e.entity_id


async def test_resolved_executes_and_answers_in_german(hass: HomeAssistant, setup_hunch):
    ids = await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    result = await _say(hass, "Licht in der Küche aus")
    assert len(svc) == 1 and sorted(svc[0].data["entity_id"]) == sorted(ids[:2])
    assert svc[0].context.user_id == "u"
    speech = _speech(result)
    assert speech.startswith("Erledigt") and "ausgeschaltet" in speech
    assert result.continue_conversation is False


async def test_escalation_passes_the_unchanged_text_to_the_fallback(
    hass: HomeAssistant, setup_hunch
):
    await _home(hass)
    client, calls = scripted(
        {"flag:has_timing": NoulA(0.95), "verb:turn_off": NoulA(0.9), "domain:light": NoulA(0.9)}
    )
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    fake = AsyncMock(return_value=_fallback_result())
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        result = await _say(hass, "Licht in 10 Minuten aus", conversation_id="c9")
    assert fake.await_count == 1
    kwargs = fake.await_args.kwargs
    assert fake.await_args.args[1] == "Licht in 10 Minuten aus"
    assert fake.await_args.args[2] == "c9"
    assert kwargs["agent_id"] == "conversation.other" and kwargs.get("extra_system_prompt") is None
    assert result.conversation_id == "c9"


async def test_escalated_turn_traces_without_touching_the_chat_log(
    hass: HomeAssistant, setup_hunch
):
    await _home(hass)
    client, calls = scripted({"flag:has_timing": NoulA(0.95), "verb:turn_off": NoulA(0.9)})
    entry, _ = await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    fake = AsyncMock(return_value=_fallback_result("c8"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _say(hass, "Licht in 10 Minuten aus", conversation_id="c8")
    assert len(entry.runtime_data.traces) == 1
    assert entry.runtime_data.traces[0]["outcome"].startswith("Escalate:")
    assert entry.runtime_data.traces[0]["trace"]["entries"]


async def test_fallback_pointing_at_hunch_itself_is_refused(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted({"flag:has_timing": NoulA(0.95), "verb:turn_off": NoulA(0.9)})
    await setup_hunch(client, calls, options={"fallback_agent": AGENT})
    fake = AsyncMock(return_value=_fallback_result())
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        result = await _say(hass, "Licht in 10 Minuten aus")
    assert fake.await_count == 0
    assert "kein anderer Assistent" in _speech(result)
    assert result.response.error_code is intent.IntentResponseErrorCode.FAILED_TO_HANDLE


async def test_fallback_that_raises_answers_unavailable(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted({"flag:has_timing": NoulA(0.95), "verb:turn_off": NoulA(0.9)})
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.gone"})
    fake = AsyncMock(side_effect=ValueError("no such agent"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        result = await _say(hass, "Licht in 10 Minuten aus")
    assert "kein anderer Assistent" in _speech(result)


async def _confirm_turn(hass, setup_hunch, reply):
    """First turn asks for confirmation; second turn replies `reply`. Returns the pieces."""
    client, calls = scripted(R1_TURN_OFF_KITCHEN, reply={"reply_confirm": reply})
    entry, _ = await setup_hunch(client, calls, options={"max_silent_targets": 1})
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    first = await _say(hass, "Licht in der Küche aus", conversation_id="c1")
    assert first.continue_conversation is True and "?" in _speech(first)
    fake = AsyncMock(return_value=_fallback_result("c1"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        second = await _say(hass, "hm, eigentlich nur die Spots", conversation_id="c1")
    return entry, svc, fake, second


async def test_confirmation_yes_executes(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    entry, svc, fake, second = await _confirm_turn(
        hass, setup_hunch, ChoiceA("affirmative", 0.95, {})
    )
    assert len(svc) == 1
    assert fake.await_count == 0
    assert second.continue_conversation is False
    assert [t["outcome"] for t in entry.runtime_data.traces] == ["NeedsConfirmation", "Confirmed"]
    # the reply judgment is traced like any other Hunch-authored answer (spec §7.8)
    reply_trace = entry.runtime_data.traces[1]["trace"]
    assert [e["question_id"] for e in reply_trace["entries"]] == ["reply_confirm"]
    assert reply_trace["decisions"] == [
        {"name": "reply_confirm", "value": 0.95, "threshold": 0.7, "passed": True}
    ]


async def test_confirmation_no_cancels(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    _entry, svc, fake, second = await _confirm_turn(
        hass, setup_hunch, ChoiceA("negative", 0.95, {})
    )
    assert len(svc) == 0
    assert fake.await_count == 0
    assert second.continue_conversation is False
    assert "nichts geändert" in _speech(second)


async def test_confirmation_other_escalates_with_context(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    _entry, svc, fake, _second = await _confirm_turn(hass, setup_hunch, ChoiceA("other", 0.6, {}))
    assert len(svc) == 0
    assert fake.await_count == 1
    prompt = fake.await_args.kwargs["extra_system_prompt"]
    assert "proposed" in prompt and "turn off" in prompt
    assert fake.await_args.args[1] == "hm, eigentlich nur die Spots"


async def test_a_pending_turn_is_single_use(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        R1_TURN_OFF_KITCHEN, reply={"reply_confirm": ChoiceA("affirmative", 0.95, {})}
    )
    await setup_hunch(client, calls, options={"max_silent_targets": 1})
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Licht in der Küche aus", conversation_id="c7")
    await _say(hass, "ja", conversation_id="c7")
    assert len(svc) == 1
    # the same reply again is a fresh request, not a second execution of the stored actions
    third = await _say(hass, "ja", conversation_id="c7")
    assert len(svc) == 1
    assert third.continue_conversation is True


async def test_expired_pending_turn_prefixes_a_freshly_decided_answer(
    hass: HomeAssistant, setup_hunch
):
    await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN, reply={"reply_confirm": ChoiceA("x", 0.1, {})})
    entry, _ = await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    first = await _say(hass, "Licht in der Küche aus", conversation_id="c5")
    assert len(svc) == 1 and first.continue_conversation is False
    # park a pending turn, then make it stale
    from custom_components.hunch.pending import PendingConfirm

    rt = entry.runtime_data
    rt.pending.put("c5", PendingConfirm((), None, "Soll ich?", rt.pending.now() - 1000.0))
    result = await _say(hass, "Licht in der Küche aus", conversation_id="c5")
    assert _speech(result).startswith("Diese Frage ist abgelaufen")
    assert "Erledigt" in _speech(result)
    assert len(svc) == 2


async def test_clarification_pick_executes_that_device(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        R1_TURN_ON_ONE, R2_WEAK_PICK, reply={"reply_pick": ChoiceA("Kücheninsel (Küche)", 0.9, {})}
    )
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_on")
    first = await _say(hass, "Lampe in der Küche an", conversation_id="c2")
    assert first.continue_conversation is True
    assert "Kücheninsel (Küche)" in _speech(first)
    second = await _say(hass, "die Insel", conversation_id="c2")
    assert len(svc) == 1 and svc[0].data["entity_id"] == ["light.kuche_kucheninsel"]
    assert second.continue_conversation is False


async def test_clarification_none_of_these_escalates_with_context(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        R1_TURN_ON_ONE, R2_WEAK_PICK, reply={"reply_pick": ChoiceA(NO_MATCH, 0.9, {})}
    )
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    svc = async_mock_service(hass, "homeassistant", "turn_on")
    await _say(hass, "Lampe in der Küche an", conversation_id="c3")
    fake = AsyncMock(return_value=_fallback_result("c3"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _say(hass, "keins davon", conversation_id="c3")
    assert len(svc) == 0
    assert fake.await_count == 1
    assert "proposed" in fake.await_args.kwargs["extra_system_prompt"]


async def test_clarification_offers_the_labels_to_jev(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        R1_TURN_ON_ONE, R2_WEAK_PICK, reply={"reply_pick": ChoiceA(NO_MATCH, 0.9, {})}
    )
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    await _say(hass, "Lampe in der Küche an", conversation_id="c4")
    fake = AsyncMock(return_value=_fallback_result("c4"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _say(hass, "keins davon", conversation_id="c4")
    state, questions = calls[-1]
    assert set(state) == {"question", "reply"}
    assert state["reply"] == "keins davon"
    assert questions["reply_pick"].options == ("Spots (Küche)", "Kücheninsel (Küche)", NO_MATCH)


async def test_reply_judgment_failure_escalates_with_context(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)  # no reply script -> wrong answer primitive
    await setup_hunch(client, calls, options={"max_silent_targets": 1})
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Licht in der Küche aus", conversation_id="c6")

    async def boom(*args, **kwargs):
        raise TypeError("bad answer")

    fake = AsyncMock(return_value=_fallback_result("c6"))
    with (
        patch.object(client, "ask", boom),
        patch("custom_components.hunch.conversation.conversation.async_converse", fake),
    ):
        await _say(hass, "vielleicht", conversation_id="c6")
    assert len(svc) == 0
    assert fake.await_count == 1
    assert "proposed" in fake.await_args.kwargs["extra_system_prompt"]


async def test_condition_not_met_executes_nothing(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    r1 = {
        **R1_TURN_OFF_KITCHEN,
        "flag:has_condition": NoulA(0.95),
        "condition_domain": ChoiceA("binary_sensor", 0.95, {}),
    }
    r2 = {"cond_subject": ChoiceA("Fenster", 0.95, {}), "cond_state": ChoiceA("on", 0.9, {})}
    client, calls = scripted(r1, r2)
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    result = await _say(hass, "Licht in der Küche aus wenn das Fenster offen ist")
    assert len(svc) == 0
    assert "nicht" in _speech(result)
    assert "Fenster (Küche)" in _speech(result)


async def test_condition_met_executes(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    hass.states.async_set("binary_sensor.kuche_fenster", "on", {"device_class": "window"})
    r1 = {
        **R1_TURN_OFF_KITCHEN,
        "flag:has_condition": NoulA(0.95),
        "condition_domain": ChoiceA("binary_sensor", 0.95, {}),
    }
    r2 = {"cond_subject": ChoiceA("Fenster", 0.95, {}), "cond_state": ChoiceA("on", 0.9, {})}
    client, calls = scripted(r1, r2)
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    result = await _say(hass, "Licht in der Küche aus wenn das Fenster offen ist")
    assert len(svc) == 1
    assert _speech(result).startswith("Erledigt")


async def test_query_answers_with_state_words(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    r1 = {
        "verb:query_state": NoulA(0.95),
        "domain:binary_sensor": NoulA(0.95),
        "area:galerie": NoulA(0.99),
        "flag:names_specific": NoulA(0.9),
        "verb_primary": ChoiceA("query_state", 0.95, {}),
        "area_primary": ChoiceA("Galerie", 0.99, {}),
    }
    r2 = {"target:query_state": ChoiceA("Tür", 0.95, {})}
    client, calls = scripted(r1, r2)
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_on")
    result = await _say(hass, "Ist die Tür in der Galerie offen?")
    assert _speech(result) == "Tür (Galerie): offen"
    assert len(svc) == 0


async def test_execution_failure_names_the_target(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    await setup_hunch(client, calls)
    async_mock_service(
        hass, "homeassistant", "turn_off", raise_exception=HomeAssistantError("nope")
    )
    result = await _say(hass, "Licht in der Küche aus")
    speech = _speech(result)
    assert speech.startswith("Erledigt, außer:")
    assert "Spots (Küche)" in speech and "Kücheninsel (Küche)" in speech


async def test_trace_is_attached_to_the_chat_log(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    entry, _ = await setup_hunch(client, calls)
    async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Licht in der Küche aus", conversation_id="c10")
    assert len(entry.runtime_data.traces) == 1
    assert entry.runtime_data.traces[0]["outcome"] == "Resolved"
    assert entry.runtime_data.traces[0]["prompt"] == "Licht in der Küche aus"
    assert "entries" in entry.runtime_data.traces[0]["trace"]
    log = hass.data[conversation.chat_log.DATA_CHAT_LOGS]["c10"]
    assistant = [c for c in log.content if c.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].agent_id == AGENT
    assert "entries" in assistant[0].native


def _clarification(home, candidates, verb_name="turn_on", question_key="which_device"):
    """A NeedsClarification the engine itself would not produce, to reach step 5's edges."""
    by_id = {e.entity_id: e for e in home.entities}
    return NeedsClarification(
        question_key,
        tuple(by_id[i] for i in candidates),
        Trace(),
        DEFAULT_VOCABULARY.by_name(verb_name) if verb_name else None,
        {},
    )


async def _clarify_from(hass, rt, result, conversation_id):
    with patch.object(rt.engine, "decide", AsyncMock(return_value=result)):
        return await _say(hass, "Lampe in der Küche an", conversation_id=conversation_id)


async def test_clarification_disambiguates_duplicate_labels(hass: HomeAssistant, setup_hunch):
    ids = await _home(hass)
    client, calls = scripted(R1_TURN_ON_ONE)
    entry, _ = await setup_hunch(client, calls)
    rt = entry.runtime_data
    # the same entity twice: the duplicate label gets its entity id appended
    result = _clarification(rt.builder.build(), [ids[0], ids[0]])
    first = await _clarify_from(hass, rt, result, "c11")
    assert first.continue_conversation is True
    pending = rt.pending.take("c11")
    assert pending.labels == ("Spots (Küche)", f"Spots (Küche) [{ids[0]}]")
    assert f"Spots (Küche) [{ids[0]}]" in _speech(first)


async def test_more_candidates_than_the_cap_escalates_instead_of_truncating(
    hass: HomeAssistant, setup_hunch
):
    ids = await _home(hass)
    client, calls = scripted(R1_TURN_ON_ONE)
    entry, _ = await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    rt = entry.runtime_data
    rt.clarify_max_candidates = 1
    result = _clarification(rt.builder.build(), [ids[0], ids[1]])
    fake = AsyncMock(return_value=_fallback_result("c14"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _clarify_from(hass, rt, result, "c14")
    assert fake.await_count == 1
    assert fake.await_args.kwargs.get("extra_system_prompt") is None
    assert rt.pending.take("c14") is None


async def test_which_area_clarification_escalates(hass: HomeAssistant, setup_hunch):
    ids = await _home(hass)
    client, calls = scripted(R1_TURN_ON_ONE)
    entry, _ = await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    rt = entry.runtime_data
    result = _clarification(rt.builder.build(), [ids[0]], question_key="which_area")
    fake = AsyncMock(return_value=_fallback_result("c15"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _clarify_from(hass, rt, result, "c15")
    assert fake.await_count == 1
    assert fake.await_args.kwargs.get("extra_system_prompt") is None
    assert rt.pending.take("c15") is None
    assert [t["outcome"] for t in rt.traces] == ["NeedsClarification"]


async def test_a_picked_target_whose_verb_still_needs_a_value_escalates(
    hass: HomeAssistant, setup_hunch
):
    ids = await _home(hass)
    client, calls = scripted(
        R1_TURN_ON_ONE, reply={"reply_pick": ChoiceA("Spots (Küche)", 0.9, {})}
    )
    entry, _ = await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    rt = entry.runtime_data
    svc = async_mock_service(hass, "light", "turn_on")
    # the engine builds scope-time clarifications with params={}, so a param verb lands here
    result = _clarification(rt.builder.build(), [ids[0]], verb_name="set_brightness")
    await _clarify_from(hass, rt, result, "c16")
    fake = AsyncMock(return_value=_fallback_result("c16"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _say(hass, "die Spots", conversation_id="c16")
    assert len(svc) == 0
    assert fake.await_count == 1
    prompt = fake.await_args.kwargs["extra_system_prompt"]
    assert "Spots (Küche)" in prompt and "brightness" in prompt


async def test_a_failure_after_the_judgment_is_not_mistaken_for_a_judgment_failure(
    hass: HomeAssistant, setup_hunch
):
    """Only the Jev call is guarded; a later KeyError must not re-offer the proposal."""
    await _home(hass)
    client, calls = scripted(
        R1_TURN_OFF_KITCHEN, reply={"reply_confirm": ChoiceA("affirmative", 0.95, {})}
    )
    await setup_hunch(client, calls, options={"max_silent_targets": 1})
    async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Licht in der Küche aus", conversation_id="c17")
    fake = AsyncMock(return_value=_fallback_result("c17"))
    with (
        patch(
            "custom_components.hunch.conversation.describe_action",
            side_effect=KeyError("no phrase"),
        ),
        patch("custom_components.hunch.conversation.conversation.async_converse", fake),
        pytest.raises(KeyError),
    ):
        await _say(hass, "ja", conversation_id="c17")
    assert fake.await_count == 0


async def test_clarification_without_a_verb_escalates(hass: HomeAssistant, setup_hunch):
    ids = await _home(hass)
    client, calls = scripted(R1_TURN_ON_ONE)
    entry, _ = await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    rt = entry.runtime_data
    result = _clarification(rt.builder.build(), [ids[0]], verb_name=None)
    fake = AsyncMock(return_value=_fallback_result("c12"))
    with (
        patch.object(rt.engine, "decide", AsyncMock(return_value=result)),
        patch("custom_components.hunch.conversation.conversation.async_converse", fake),
    ):
        await _say(hass, "Lampe an", conversation_id="c12")
    assert fake.await_count == 1
    assert fake.await_args.kwargs.get("extra_system_prompt") is None
    assert rt.pending.take("c12") is None
    assert [t["outcome"] for t in rt.traces] == ["NeedsClarification"]


async def test_a_confirm_tier_verb_picked_by_clarification_asks_again(
    hass: HomeAssistant, setup_hunch
):
    ids = await _home(hass)
    client, calls = scripted(
        R1_TURN_ON_ONE, reply={"reply_pick": ChoiceA("Spots (Küche)", 0.9, {})}
    )
    entry, _ = await setup_hunch(client, calls)
    rt = entry.runtime_data
    svc = async_mock_service(hass, "homeassistant", "turn_on")
    result = _clarification(rt.builder.build(), [ids[0]], verb_name="arm")
    with patch.object(rt.engine, "decide", AsyncMock(return_value=result)):
        await _say(hass, "scharf schalten", conversation_id="c13")
    second = await _say(hass, "die Spots", conversation_id="c13")
    assert len(svc) == 0
    assert second.continue_conversation is True
    assert "?" in _speech(second)
    assert isinstance(rt.pending.take("c13"), PendingConfirm)


R1_TWO_VERBS = {
    "verb:turn_off": NoulA(0.95),
    "verb:close": NoulA(0.95),
    "domain:light": NoulA(0.95),
    "domain:cover": NoulA(0.95),
    "area:kuche": NoulA(0.99),
    "flag:collective": NoulA(0.9),
    "verb_primary": ChoiceA("several", 0.95, {}),
    "area_primary": ChoiceA("Küche", 0.99, {}),
}


async def test_a_two_verb_plan_names_both_verbs_and_runs_both(hass: HomeAssistant, setup_hunch):
    """`verb_primary` = "several": every action gets its own clause, not just the first."""
    ids = await _home(hass)
    rollo = await _cover(hass)
    client, calls = scripted(R1_TWO_VERBS)
    await setup_hunch(client, calls)
    off = async_mock_service(hass, "homeassistant", "turn_off")
    close = async_mock_service(hass, "cover", "close_cover")
    result = await _say(hass, "Licht aus und Rollo zu in der Küche")
    assert len(off) == 1 and sorted(off[0].data["entity_id"]) == sorted(ids[:2])
    assert len(close) == 1 and close[0].data["entity_id"] == [rollo]
    speech = _speech(result)
    assert "ausgeschaltet" in speech and "geschlossen" in speech
    assert "Rollo (Küche)" in speech and "Spots (Küche)" in speech
    assert speech.startswith("Erledigt: ") and "; " in speech


async def test_a_two_verb_confirmation_names_both_verbs(hass: HomeAssistant, setup_hunch):
    """A mixed plan's confirmation question must not label one action with the other's verb."""
    await _home(hass)
    await _cover(hass)
    client, calls = scripted(R1_TWO_VERBS, reply={"reply_confirm": ChoiceA("negative", 0.95, {})})
    await setup_hunch(client, calls, options={"max_silent_targets": 1})
    first = await _say(hass, "Licht aus und Rollo zu in der Küche", conversation_id="c20")
    question = _speech(first)
    assert first.continue_conversation is True
    assert "ausschalten" in question and "schließen" in question
    assert "Rollo (Küche)" in question and "; " in question


async def test_a_query_beside_a_command_is_answered_and_executed(hass: HomeAssistant, setup_hunch):
    """A plan that both reads and commands says the reading *and* the command sentence."""
    ids = await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    entry, _ = await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    rt = entry.runtime_data
    home = rt.builder.build()
    by_id = {e.entity_id: e for e in home.entities}
    mixed = Resolved(
        (
            Action(DEFAULT_VOCABULARY.by_name("turn_off"), (by_id[ids[0]],), {}),
            Action(DEFAULT_VOCABULARY.by_name("query_state"), (by_id[ids[3]],), {}),
        ),
        None,
        0.9,
        Trace(),
    )
    with patch.object(rt.engine, "decide", AsyncMock(return_value=mixed)):
        result = await _say(hass, "Spots aus, und ist die Tür offen?", conversation_id="c21")
    assert len(svc) == 1 and svc[0].data["entity_id"] == ["light.kuche_spots"]
    assert _speech(result) == "Tür (Galerie): offen\nErledigt: Spots (Küche) ausgeschaltet."


async def test_a_condition_hand_off_tells_the_fallback_to_check_it_first(
    hass: HomeAssistant, setup_hunch
):
    await _home(hass)
    client, calls = scripted(
        {
            **R1_TURN_OFF_KITCHEN,
            "flag:has_condition": NoulA(0.95),
            "flag:condition_numeric": NoulA(0.95),
            "condition_domain": ChoiceA("sensor", 0.95, {}),
        }
    )
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    fake = AsyncMock(return_value=_fallback_result())
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _say(hass, "Licht in der Küche aus wenn es unter 20 Grad hat", conversation_id="c7")
    assert fake.await_count == 1
    extra = fake.await_args.kwargs["extra_system_prompt"]
    assert extra and "condition" in extra and "act only if it holds" in extra


async def test_a_follow_up_leans_on_the_previous_turn(hass: HomeAssistant, setup_hunch):
    # "Licht in der Küche aus" then "und die Galerie?" — the second sentence names no action;
    # the engine borrows turn_off from the first turn (scripted follow-up verdict).
    from hunch.round1 import SAME_ACTION

    await _home(hass)
    reg = er.async_get(hass)
    e = reg.async_get_or_create(
        "light", "test", "9", suggested_object_id="galerie_lampe", original_name="Lampe"
    )
    reg.async_update_entity(
        e.entity_id, area_id=ar.async_get(hass).async_get_area_by_name("Galerie").id
    )
    hass.states.async_set(e.entity_id, "on")
    async_expose_entity(hass, "conversation", e.entity_id, True)
    calls_seen: list[dict] = []
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    inner = client._script

    def script(state, qs):
        calls_seen.append(dict(state))
        out = inner(state, qs)
        if "follow_up" in qs:  # second turn: no verb fires, the room is the Galerie
            out["follow_up"] = ChoiceA(SAME_ACTION, 0.95, {})
            out["verb:turn_off"] = NoulA(0.05)
            out["verb_primary"] = ChoiceA("none", 0.9, {})
            out["area:kuche"] = NoulA(0.05)
            out["area:galerie"] = NoulA(0.98)
            out["area_primary"] = ChoiceA("Galerie", 0.98, {})
            out["domain:light"] = NoulA(0.05)
        return out

    client._script = script
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Licht in der Küche aus", conversation_id="f1")
    assert len(svc) == 1 and "previous" not in calls_seen[0]
    second = await _say(hass, "und die Galerie?", conversation_id="f1")
    assert calls_seen[-1].get("previous", {}).get("request") == "Licht in der Küche aus"
    assert len(svc) == 2 and svc[1].data["entity_id"] == [e.entity_id]
    assert "ausgeschaltet" in second.response.speech["plain"]["speech"]


async def test_a_numeric_condition_is_checked_live_and_the_value_is_spoken(
    hass: HomeAssistant, setup_hunch
):
    from hunch.round2 import BELOW

    ids = await _home(hass)
    reg = er.async_get(hass)
    t = reg.async_get_or_create(
        "sensor", "test", "t1", suggested_object_id="kuche_temperatur", original_name="Temperatur"
    )
    reg.async_update_entity(
        t.entity_id, area_id=ar.async_get(hass).async_get_area_by_name("Küche").id
    )
    hass.states.async_set(t.entity_id, "24.5", {"unit_of_measurement": "°C"})
    async_expose_entity(hass, "conversation", t.entity_id, True)
    r1 = {
        **R1_TURN_OFF_KITCHEN,
        "flag:has_condition": NoulA(0.95),
        "flag:condition_numeric": NoulA(0.95),
        "condition_domain": ChoiceA("sensor", 0.95, {"sensor": 0.95}),
    }
    r2 = {
        "cond_subject": ChoiceA("Temperatur", 0.95, {}),
        "cond_threshold": ChoiceA("20 Grad", 0.95, {}),
        "cond_direction": ChoiceA(BELOW, 0.93, {}),
    }
    client, calls = scripted(r1, r2)
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    result = await _say(hass, "Licht in der Küche aus wenn es unter 20 Grad hat")
    assert len(svc) == 0
    speech = result.response.speech["plain"]["speech"]
    assert "24,5 °C" in speech and "unter 20" in speech
    hass.states.async_set(t.entity_id, "18.0", {"unit_of_measurement": "°C"})
    result = await _say(hass, "Licht in der Küche aus wenn es unter 20 Grad hat")
    assert len(svc) == 1 and sorted(svc[0].data["entity_id"]) == sorted(ids[:2])
    assert result.response.speech["plain"]["speech"].startswith("Erledigt")


async def test_every_turn_leaves_an_agent_detail_event_on_the_conversation_trace(
    hass: HomeAssistant, setup_hunch
):
    from homeassistant.components.conversation import trace as ctrace

    await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Licht in der Küche aus")
    events = [
        e["data"]["hunch"]
        for e in ctrace.async_get_traces()[-1].as_dict()["events"]
        if e["event_type"] == ctrace.ConversationTraceEventType.AGENT_DETAIL
        and "hunch" in e["data"]
    ]
    assert events and events[-1]["outcome"] == "Resolved"
    assert events[-1]["answered_by"] == "hunch"

    client2, calls2 = scripted({"flag:has_timing": NoulA(0.95), "verb:turn_off": NoulA(0.9)})
    entry = hass.config_entries.async_entries("hunch")[0]
    entry.runtime_data.client = client2
    entry.runtime_data.engine._client = client2  # swap the fake for the hand-off case
    fake = AsyncMock(return_value=_fallback_result())
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        await _say(hass, "Licht in 10 Minuten aus")
    events = [
        e["data"]["hunch"]
        for e in ctrace.async_get_traces()[-1].as_dict()["events"]
        if e["event_type"] == ctrace.ConversationTraceEventType.AGENT_DETAIL
        and "hunch" in e["data"]
    ]
    assert events and events[-1]["outcome"] == "Escalate:timing"
    assert events[-1]["handed_off_to"] == "conversation.other"


async def test_the_spoken_response_says_who_answered(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    async_mock_service(hass, "homeassistant", "turn_off")
    own = await _say(hass, "Licht in der Küche aus")
    assert own.response.speech["plain"]["extra_data"]["hunch"] == {
        "outcome": "Resolved",
        "answered_by": "hunch",
    }
    client2, _ = scripted({"flag:has_timing": NoulA(0.95), "verb:turn_off": NoulA(0.9)})
    entry = hass.config_entries.async_entries("hunch")[0]
    entry.runtime_data.client = client2
    entry.runtime_data.engine._client = client2
    fake_response = _fallback_result()
    fake_response.response.async_set_speech("Timer gestellt.")
    fake = AsyncMock(return_value=fake_response)
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        handed = await _say(hass, "Licht in 10 Minuten aus")
    mark = handed.response.speech["plain"]["extra_data"]["hunch"]
    assert mark["outcome"] == "Escalate:timing" and mark["handed_off_to"] == "conversation.other"
    assert handed.response.speech["plain"]["speech"] == "Timer gestellt."


# ---- timers and timed plans (spec §5.4) ---------------------------------------------------


def _stored_timer(timer_id, label, seconds, duration=None):
    return HunchTimer(
        timer_id=timer_id,
        kind="timer",
        label=label,
        description=None,
        duration_seconds=duration or seconds,
        due_at=dt_util.utcnow() + timedelta(seconds=seconds),
        actions=(),
        language="de",
        conversation_id=None,
        device_id=None,
        satellite_id=None,
        area_id=None,
        user_id=None,
    )


async def test_timer_start_is_stored_and_spoken(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.9, {})},
        {"duration:0": ChoiceA(MINUTES, 0.9, {}), "timer_label": ChoiceA("Nudeln", 0.9, {})},
    )
    entry, _ = await setup_hunch(client, calls)
    result = await _say(hass, "Timer für die Nudeln 8 Minuten", conversation_id="t1")
    assert _speech(result) == "Timer für Nudeln gestellt, 8 Minuten."
    active = entry.runtime_data.timers.active()
    assert len(active) == 1 and active[0].label == "Nudeln" and active[0].kind == "timer"
    assert active[0].conversation_id == "t1" and active[0].language == "de"
    assert active[0].user_id == "u" and active[0].duration_seconds == 480
    assert result.response.speech["plain"]["extra_data"]["hunch"]["outcome"] == "Resolved"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_timer_fires_event_and_script(hass: HomeAssistant, setup_hunch, freezer):
    await _home(hass)
    client, calls = scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.9, {})}, {"duration:0": ChoiceA(MINUTES, 0.9, {})}
    )
    entry, _ = await setup_hunch(client, calls, options={"timer_script": "script.ansage"})
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    script_calls = async_mock_service(hass, "script", "turn_on")
    await _say(hass, "Timer 2 Minuten")
    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["duration_seconds"] == 120 and events[0].data["overdue"] is False
    assert events[0].data["label"] is None and events[0].data["kind"] == "timer"
    assert len(script_calls) == 1 and script_calls[0].data["entity_id"] == "script.ansage"
    assert script_calls[0].data["variables"]["duration_seconds"] == 120
    assert entry.runtime_data.timers.active() == ()


async def test_remaining_and_cancel_and_none(hass: HomeAssistant, setup_hunch, freezer):
    await _home(hass)
    answers = {"timing_kind": ChoiceA(TIMER_REMAINING, 0.9, {})}
    round2: dict = {}
    client, calls = scripted(answers, round2)  # both dicts are read by reference per call
    entry, _ = await setup_hunch(client, calls)
    assert _speech(await _say(hass, "wie lange noch?")) == "Es läuft kein Timer."
    await entry.runtime_data.timers.async_add(_stored_timer("a", "Nudeln", 200, 480))
    assert (
        _speech(await _say(hass, "wie lange noch?"))
        == "Timer für Nudeln: noch 3 Minuten 20 Sekunden."
    )
    answers["timing_kind"] = ChoiceA(TIMER_CANCEL, 0.9, {})
    round2["timer_pick"] = ChoiceA("Nudeln (3:20 left)", 0.9, {})
    assert _speech(await _say(hass, "Timer abbrechen")) == "Abgebrochen: Timer für Nudeln."
    assert entry.runtime_data.timers.active() == ()
    assert _speech(await _say(hass, "Timer abbrechen")) == "Es läuft kein Timer."


async def _two_timers_asked(hass, setup_hunch, reply):
    await _home(hass)
    client, calls = scripted(
        {"timing_kind": ChoiceA(TIMER_CANCEL, 0.9, {})},
        {"timer_pick": ChoiceA("Nudeln (3:20 left)", 0.5, {})},
        reply=reply,
    )
    entry, _ = await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    timers = entry.runtime_data.timers
    await timers.async_add(_stored_timer("a", "Nudeln", 200))
    await timers.async_add(_stored_timer("b", "Reis", 600))
    first = await _say(hass, "Timer abbrechen", conversation_id="w1")
    assert _speech(first) == (
        "Welchen Timer meinst du: Timer für Nudeln (3 Minuten 20 Sekunden), "
        "Timer für Reis (10 Minuten)?"
    )
    assert first.continue_conversation is True
    fake = AsyncMock(return_value=_fallback_result("w1"))
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        second = await _say(hass, "den für die Nudeln", conversation_id="w1")
    return entry, calls, fake, second


async def test_two_timers_which_timer_clarification_cancels_the_pick(
    hass: HomeAssistant, setup_hunch, freezer
):
    entry, calls, fake, second = await _two_timers_asked(
        hass,
        setup_hunch,
        {"reply_pick": ChoiceA("Timer für Nudeln (3 Minuten 20 Sekunden)", 0.9, {})},
    )
    assert _speech(second) == "Abgebrochen: Timer für Nudeln."
    assert second.continue_conversation is False
    assert [t.label for t in entry.runtime_data.timers.active()] == ["Reis"]
    assert fake.await_count == 0
    # the reply was judged over exactly the labels the user heard, plus all/none
    state, qs = calls[-1]
    assert state["question"].startswith("Welchen Timer meinst du")
    assert qs["reply_pick"].options == (
        "Timer für Nudeln (3 Minuten 20 Sekunden)",
        "Timer für Reis (10 Minuten)",
        ALL_TIMERS,
        NO_MATCH,
    )
    await hass.config_entries.async_unload(entry.entry_id)


async def test_which_timer_reply_all_timers_cancels_both(hass: HomeAssistant, setup_hunch, freezer):
    entry, _calls, fake, second = await _two_timers_asked(
        hass, setup_hunch, {"reply_pick": ChoiceA(ALL_TIMERS, 0.9, {})}
    )
    assert _speech(second) == "Abgebrochen: Timer für Nudeln, Timer für Reis."
    assert entry.runtime_data.timers.active() == () and fake.await_count == 0


async def test_which_timer_reply_matching_nothing_changes_nothing(
    hass: HomeAssistant, setup_hunch, freezer
):
    entry, _calls, fake, second = await _two_timers_asked(
        hass, setup_hunch, {"reply_pick": ChoiceA(NO_MATCH, 0.9, {})}
    )
    assert _speech(second) == "Okay, ich habe nichts geändert."
    assert len(entry.runtime_data.timers.active()) == 2
    assert fake.await_count == 0
    assert entry.runtime_data.traces[-1]["outcome"] == "TimerPickOther"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_overdue_by_hours_skips_the_action(hass: HomeAssistant, setup_hunch, hass_storage):
    await _home(hass)
    hass_storage[TIMER_STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": TIMER_STORE_KEY,
        "data": {
            "timers": [
                {
                    "timer_id": "late",
                    "kind": "delayed",
                    "label": None,
                    "description": "Spots (Küche) ausschalten",
                    "duration_seconds": 600,
                    "due_at": (dt_util.utcnow() - timedelta(hours=2)).isoformat(),
                    "actions": [
                        {"verb": "turn_off", "entity_ids": ["light.kuche_spots"], "params": {}}
                    ],
                    "language": "de",
                    "conversation_id": None,
                    "device_id": None,
                    "satellite_id": None,
                    "area_id": None,
                    "user_id": None,
                }
            ]
        },
    }
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["skipped"] is True and events[0].data["executed"] == []
    assert events[0].data["overdue"] is True
    assert svc == []
    assert entry.runtime_data.timers.active() == ()


R1_TURN_ON_FOR_DURATION = {
    "verb:turn_on": NoulA(0.95),
    "domain:light": NoulA(0.95),
    "area:kuche": NoulA(0.99),
    "verb_primary": ChoiceA("turn_on", 0.95, {}),
    "area_primary": ChoiceA("Küche", 0.99, {}),
    "timing_kind": ChoiceA(FOR_DURATION, 0.95, {}),
}


async def test_for_duration_turns_on_now_and_off_later(hass: HomeAssistant, setup_hunch, freezer):
    await _home(hass)
    client, calls = scripted(
        R1_TURN_ON_FOR_DURATION,
        {
            "target:turn_on": ChoiceA("Kücheninsel", 0.95, {}),
            "duration:0": ChoiceA(MINUTES, 0.95, {}),
        },
    )
    entry, _ = await setup_hunch(client, calls)
    on = async_mock_service(hass, "homeassistant", "turn_on")
    off = async_mock_service(hass, "homeassistant", "turn_off")
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    result = await _say(hass, "Kücheninsel für 15 Minuten an", conversation_id="d1")
    assert len(on) == 1 and on[0].data["entity_id"] == ["light.kuche_kucheninsel"]
    assert off == []
    assert (
        _speech(result) == "Erledigt: Kücheninsel (Küche) eingeschaltet, in 15 Minuten wieder aus."
    )
    (timer,) = entry.runtime_data.timers.active()
    assert timer.kind == "revert" and timer.user_id == "u"
    assert timer.actions == (StoredAction("turn_off", ("light.kuche_kucheninsel",), {}),)
    assert timer.description == "Kücheninsel (Küche) ausschalten"
    assert entry.runtime_data.last_turns.get("d1") is None
    freezer.tick(timedelta(seconds=901))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(off) == 1 and off[0].data["entity_id"] == ["light.kuche_kucheninsel"]
    assert off[0].context.user_id == "u"
    assert len(events) == 1 and events[0].data["executed"] == ["light.kuche_kucheninsel"]
    assert len(on) == 1


async def test_for_duration_reverts_only_what_succeeded(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        {**R1_TURN_OFF_KITCHEN, "timing_kind": ChoiceA(FOR_DURATION, 0.95, {})},
        {"duration:0": ChoiceA(MINUTES, 0.95, {})},
    )
    entry, _ = await setup_hunch(client, calls)

    async def execute(self, actions, context):
        return [
            TargetResult("light.kuche_spots", True, None),
            TargetResult("light.kuche_kucheninsel", False, "boom"),
        ]

    with patch("custom_components.hunch.conversation.Executor.execute", execute):
        result = await _say(hass, "Licht in der Küche für 10 Minuten aus")
    speech = _speech(result)
    assert "Kücheninsel (Küche)" in speech.splitlines()[0]
    assert speech.splitlines()[-1].endswith("in 10 Minuten wieder an.")
    (timer,) = entry.runtime_data.timers.active()
    assert timer.actions == (StoredAction("turn_on", ("light.kuche_spots",), {}),)
    await hass.config_entries.async_unload(entry.entry_id)


R1_DELAYED_OFF = {**R1_TURN_OFF_KITCHEN, "timing_kind": ChoiceA(DELAYED, 0.95, {})}


async def test_delayed_turn_off_runs_nothing_now(hass: HomeAssistant, setup_hunch, freezer):
    ids = await _home(hass)
    client, calls = scripted(R1_DELAYED_OFF, {"duration:0": ChoiceA(MINUTES, 0.95, {})})
    entry, _ = await setup_hunch(client, calls)
    off = async_mock_service(hass, "homeassistant", "turn_off")
    result = await _say(hass, "Licht in der Küche in 10 Minuten aus", conversation_id="d2")
    assert off == []
    assert _speech(result) == "In 10 Minuten: Spots (Küche), Kücheninsel (Küche) ausschalten."
    (timer,) = entry.runtime_data.timers.active()
    assert timer.kind == "delayed" and timer.duration_seconds == 600
    freezer.tick(timedelta(seconds=601))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(off) == 1 and sorted(off[0].data["entity_id"]) == sorted(ids[:2])


async def test_confirmed_plan_keeps_its_timing(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        R1_DELAYED_OFF,
        {"duration:0": ChoiceA(MINUTES, 0.95, {})},
        reply={"reply_confirm": ChoiceA("affirmative", 0.95, {})},
    )
    entry, _ = await setup_hunch(client, calls, options={"max_silent_targets": 1})
    off = async_mock_service(hass, "homeassistant", "turn_off")
    first = await _say(hass, "Licht in der Küche in 10 Minuten aus", conversation_id="d3")
    question = _speech(first)
    assert question.startswith("Soll ich ") and ", in 10 Minuten?" in question
    assert first.continue_conversation is True
    second = await _say(hass, "ja", conversation_id="d3")
    assert off == []
    assert _speech(second) == "In 10 Minuten: Spots (Küche), Kücheninsel (Küche) ausschalten."
    (timer,) = entry.runtime_data.timers.active()
    assert timer.kind == "delayed"
    assert entry.runtime_data.last_turns.get("d3") is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_timed_turns_are_not_remembered_for_follow_ups(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    answers = {"timing_kind": ChoiceA(TIMER_START, 0.9, {})}
    round2 = {"duration:0": ChoiceA(MINUTES, 0.9, {})}
    client, calls = scripted(answers, round2)
    entry, _ = await setup_hunch(client, calls)
    async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Timer 8 Minuten", conversation_id="r1")
    assert entry.runtime_data.last_turns.get("r1") is None
    answers.clear()
    answers.update(R1_DELAYED_OFF)
    await _say(hass, "Licht in der Küche in 10 Minuten aus", conversation_id="r1")
    assert entry.runtime_data.last_turns.get("r1") is None
    assert len(entry.runtime_data.timers.active()) == 2
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_lone_timer_pick_offers_no_all_timers(hass: HomeAssistant, setup_hunch, freezer):
    await _home(hass)
    client, calls = scripted(
        {"timing_kind": ChoiceA(TIMER_CANCEL, 0.9, {})},
        {"timer_pick": ChoiceA("Nudeln (3:20 left)", 0.5, {})},
        reply={"reply_pick": ChoiceA("Timer für Nudeln (3 Minuten 20 Sekunden)", 0.9, {})},
    )
    entry, _ = await setup_hunch(client, calls)
    await entry.runtime_data.timers.async_add(_stored_timer("a", "Nudeln", 200))
    first = await _say(hass, "Timer abbrechen", conversation_id="w2")
    assert _speech(first) == "Welchen Timer meinst du: Timer für Nudeln (3 Minuten 20 Sekunden)?"
    second = await _say(hass, "ja den", conversation_id="w2")
    assert calls[-1][1]["reply_pick"].options == (
        "Timer für Nudeln (3 Minuten 20 Sekunden)",
        NO_MATCH,
    )
    assert _speech(second) == "Abgebrochen: Timer für Nudeln."
    assert entry.runtime_data.timers.active() == ()


async def test_a_clarified_timed_pick_keeps_its_timing_through_a_confirm_re_ask(
    hass: HomeAssistant, setup_hunch
):
    ids = await _home(hass)
    client, calls = scripted(
        R1_TURN_ON_ONE, reply={"reply_pick": ChoiceA("Spots (Küche)", 0.9, {})}
    )
    entry, _ = await setup_hunch(client, calls)
    rt = entry.runtime_data
    svc = async_mock_service(hass, "homeassistant", "turn_on")
    plain = _clarification(rt.builder.build(), [ids[0], ids[1]], verb_name="arm")
    timed = NeedsClarification(
        plain.question_key, plain.candidates, plain.trace, plain.verb, {}, Timing("delayed", 600)
    )
    with patch.object(rt.engine, "decide", AsyncMock(return_value=timed)):
        await _say(hass, "in 10 Minuten scharf schalten", conversation_id="c30")
    second = await _say(hass, "die Spots", conversation_id="c30")
    assert len(svc) == 0
    assert ", in 10 Minuten?" in _speech(second)
    pending = rt.pending.take("c30")
    assert isinstance(pending, PendingConfirm) and pending.timing == Timing("delayed", 600)
