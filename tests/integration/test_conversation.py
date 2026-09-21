"""The conversation entity: decide, execute, ask, escalate — spec §7."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from hunch import DEFAULT_VOCABULARY, Action, NeedsClarification, Resolved, Trace
from hunch.questions import ChoiceA, NoulA
from hunch.round2 import NO_MATCH
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hunch.pending import PendingConfirm
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
            "custom_components.hunch.conversation.verb_phrase", side_effect=KeyError("no phrase")
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
