"""Hunch conversation entity: decide, execute, ask, or hand off. Spec §7."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from hunch import (
    Action,
    Condition,
    DecisionBackendError,
    Entity,
    Escalate,
    HomeModel,
    NeedsClarification,
    NeedsConfirmation,
    Resolved,
    Risk,
    Trace,
)
from hunch.questions import ChoiceA, ChoiceQ
from hunch.round2 import NO_MATCH

from . import HunchConfigEntry, HunchRuntime
from .executor import Executor
from .pending import PendingClarify, PendingConfirm
from .responder import (
    action_clause,
    condition_clause,
    describe_state,
    describe_targets,
    pending_context,
    render,
    resolve_language,
    verb_phrase,
)

_LOGGER = logging.getLogger(__name__)

CONFIRM_QID = "reply_confirm"
CLARIFY_QID = "reply_pick"
REPLY_CONF = 0.7

CONFIRM_INSTRUCTIONS = (
    "The assistant asked the user `question` and the user replied `reply`. Does the reply "
    "agree to go ahead, decline, or say something else (a change, a different request, a "
    "question)?"
)
CONFIRM_DESCRIPTIONS = {
    "affirmative": "yes, go ahead, do it, ja, mach, passt",
    "negative": "no, stop, don't, cancel, nein, lass",
    "other": "anything that is not a plain yes or no: a modification, a new request, a question",
}
CLARIFY_INSTRUCTIONS = (
    "The assistant asked `question`, listing options. Which option does the user's `reply` pick?"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HunchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HunchConversationEntity(entry)])


def _response(language: str, text: str, *, error: bool = False) -> intent.IntentResponse:
    resp = intent.IntentResponse(language=language)
    if error:
        resp.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, text)
    else:
        resp.async_set_speech(text)
    return resp


@dataclass(frozen=True)
class _Turn:
    """Everything one request carries through the steps of §7."""

    user_input: conversation.ConversationInput
    chat_log: conversation.ChatLog
    lang: str
    prefix: str  # the `expired` sentence, when the turn replaced a stale pending question


class HunchConversationEntity(conversation.ConversationEntity):
    # Without CONTROL, Assist shows "this assistant can't control your home".
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL
    _attr_has_entity_name = False

    def __init__(self, entry: HunchConfigEntry) -> None:
        self._entry = entry
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    # ---- helpers ---------------------------------------------------------------------

    @property
    def _rt(self) -> HunchRuntime:
        return self._entry.runtime_data

    def _area_names(self, home: HomeModel) -> dict[str, str]:
        return {a.area_id: a.name for a in home.areas}

    def _label(self, entity: Entity, areas: Mapping[str, str]) -> str:
        """The name Hunch offers to the user and to Jev: "Name (Room)"."""
        room = areas.get(entity.area_id or "")
        return f"{entity.name} ({room})" if room else entity.name

    def _labels(self, entities: Sequence[Entity], areas: Mapping[str, str]) -> tuple[str, ...]:
        """Labels for a candidate list; duplicates get their entity id appended."""
        out: list[str] = []
        for entity in entities:
            label = self._label(entity, areas)
            while label in out:  # the same id twice would otherwise repeat an option
                label = f"{label} [{entity.entity_id}]"
            out.append(label)
        return tuple(out)

    def _result(
        self,
        turn: _Turn,
        text: str,
        trace: Trace | None,
        outcome: str,
        *,
        cont: bool = False,
        error: bool = False,
    ) -> conversation.ConversationResult:
        """Hunch's own answer: chat log (with the trace, when there is one) + trace buffer."""
        text = turn.prefix + text
        payload = trace.to_dict() if trace is not None else None
        turn.chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=self.entity_id, content=text, native=payload)
        )
        self._rt.traces.append(
            {"prompt": turn.user_input.text, "outcome": outcome, "trace": payload}
        )
        return conversation.ConversationResult(
            response=_response(turn.user_input.language, text, error=error),
            conversation_id=turn.chat_log.conversation_id,
            continue_conversation=cont,
        )

    async def _escalate(
        self,
        turn: _Turn,
        trace: Trace | None,
        outcome: str,
        extra_system_prompt: str | None = None,
    ) -> conversation.ConversationResult:
        """Step 7: hand the unchanged text to the fallback agent, never to ourselves."""
        agent_id = self._rt.fallback_agent_id
        user_input = turn.user_input
        if agent_id != self.entity_id:
            try:
                result = await conversation.async_converse(
                    self.hass,
                    user_input.text,
                    turn.chat_log.conversation_id,
                    user_input.context,
                    language=user_input.language,
                    agent_id=agent_id,
                    device_id=user_input.device_id,
                    satellite_id=user_input.satellite_id,
                    extra_system_prompt=extra_system_prompt,
                )
            except (ValueError, HomeAssistantError) as err:
                _LOGGER.warning("Fallback agent %s failed: %s", agent_id, err)
            else:
                # The fallback writes its own chat-log entry; only the trace buffer is ours.
                self._rt.traces.append(
                    {
                        "prompt": user_input.text,
                        "outcome": outcome,
                        "trace": trace.to_dict() if trace is not None else None,
                    }
                )
                return result
        return self._result(
            turn, render("fallback_unavailable", turn.lang), trace, outcome, error=True
        )

    def _action_clauses(
        self,
        actions: Sequence[Action],
        areas: Mapping[str, str],
        language: str,
        *,
        done: bool,
        params: bool = False,
    ) -> str:
        """One clause per action, joined with "; ".

        A plan with several verbs (`verb_primary` = "several") is described by all of them,
        never by the first verb with everyone's targets flattened behind it (spec §9).
        """
        parts = []
        for action in actions:
            clause = action_clause(
                verb_phrase(action.verb.name, language, done=done),
                describe_targets(action.targets, areas, language),
                language,
            )
            if params:
                values = " ".join(
                    f"{v:g}" if isinstance(v, float) else str(v) for v in action.params.values()
                )
                if values:
                    clause += f" -> {values}"
            parts.append(clause)
        return "; ".join(parts)

    # ---- executing -------------------------------------------------------------------

    async def _run(
        self,
        turn: _Turn,
        home: HomeModel,
        actions: tuple[Action, ...],
        condition: Condition | None,
        trace: Trace | None,
        outcome: str,
    ) -> conversation.ConversationResult:
        """Step 3: check the condition, then execute or read."""
        lang = turn.lang
        areas = self._area_names(home)
        executor = Executor(self.hass)
        if condition is not None and executor.condition_holds(condition) is not True:
            text = render(
                "condition_not_met",
                lang,
                subject=self._label(condition.subject, areas),
                expected=condition.expected_state,
            )
            return self._result(turn, text, trace, outcome)
        # A mixed plan reads its query targets *and* runs its commands; neither may swallow
        # the other (spec §9).
        queries = tuple(a for a in actions if a.verb.is_query)
        commands = tuple(a for a in actions if not a.verb.is_query)
        lines = []
        for action in queries:
            for reading in executor.read_states(action.targets):
                room = areas.get(reading.area_id or "")
                label = f"{reading.name} ({room})" if room else reading.name
                lines.append(f"{label}: {describe_state(reading, lang)}")
        if not commands:  # an empty plan reads nothing, safely
            return self._result(turn, render("query_answer", lang, lines=lines), trace, outcome)
        results = await executor.execute(commands, turn.user_input.context)
        failed = [r.entity_id for r in results if not r.ok]
        if failed:
            by_id = {e.entity_id: e for a in commands for e in a.targets}
            text = render(
                "execution_failed",
                lang,
                failed=[self._label(by_id[i], areas) for i in failed if i in by_id],
            )
        else:
            text = render(
                "action_done",
                lang,
                phrase="",
                targets=self._action_clauses(commands, areas, lang, done=True),
            )
        if lines:
            text = "\n".join([*lines, text])
        return self._result(turn, text, trace, outcome)

    # ---- the turn --------------------------------------------------------------------

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        rt = self._rt
        lang = resolve_language(rt.response_language, user_input.language)
        conversation_id = chat_log.conversation_id
        # Step 1: a live pending turn answers this text; an expired one only prefixes the answer.
        expired = rt.pending.peek_expired(conversation_id)
        pending = rt.pending.take(conversation_id)
        turn = _Turn(user_input, chat_log, lang, render("expired", lang) + " " if expired else "")
        home = rt.builder.build()
        if pending is not None:
            return await self._handle_reply(turn, home, pending)

        # Step 2: decide.
        result = await rt.engine.decide(home, user_input.text)
        areas = self._area_names(home)

        if isinstance(result, Resolved):  # step 3
            return await self._run(
                turn, home, result.actions, result.condition, result.trace, "Resolved"
            )

        if isinstance(result, NeedsConfirmation):  # step 4
            question = self._confirm_question(
                result.actions, result.condition, result.reason, areas, lang
            )
            rt.pending.put(
                conversation_id,
                PendingConfirm(result.actions, result.condition, question, rt.pending.now()),
            )
            return self._result(turn, question, result.trace, "NeedsConfirmation", cont=True)

        if isinstance(result, NeedsClarification):  # step 5
            if (
                result.verb is None
                or not result.candidates
                # "which area?" is the wrong question to read out, and a set larger than the
                # cap cannot be asked about without hiding the right answer (§7.5).
                or result.question_key == "which_area"
                or len(result.candidates) > rt.clarify_max_candidates
            ):
                return await self._escalate(turn, result.trace, "NeedsClarification")
            candidates = tuple(result.candidates[: rt.clarify_max_candidates])
            labels = self._labels(candidates, areas)
            question = render("clarify", lang, options=labels)
            rt.pending.put(
                conversation_id,
                PendingClarify(
                    result.verb,
                    dict(result.params),
                    candidates,
                    labels,
                    question,
                    rt.pending.now(),
                ),
            )
            return self._result(turn, question, result.trace, "NeedsClarification", cont=True)

        assert isinstance(result, Escalate)  # step 7
        return await self._escalate(turn, result.trace, f"Escalate:{result.reason}")

    def _confirm_question(
        self,
        actions: Sequence[Action],
        condition: Condition | None,
        reason: str,
        areas: Mapping[str, str],
        lang: str,
    ) -> str:
        clause = ""
        if condition is not None:
            clause = condition_clause(
                self._label(condition.subject, areas), condition.expected_state, lang
            )
        return render(
            "confirm",
            lang,
            phrase="",
            targets=self._action_clauses(actions, areas, lang, done=False),
            reason=reason,
            condition=clause,
        )

    # ---- step 6: the reply to a pending question --------------------------------------

    async def _judge(
        self, state: dict, question_id: str, question: ChoiceQ
    ) -> tuple[ChoiceA | None, Trace | None]:
        """The one Jev call of a reply turn, and the only thing a reply turn guards.

        Everything the answer then leads to — service calls, the hand-off — must fail on its
        own terms: swallowing a later KeyError/TypeError here would hand the fallback a
        proposal it might execute a second time.

        The judgment is traced like any other Hunch-authored answer (spec §7.8): the reply
        question and the confidence gate it was held to.
        """
        trace = Trace()
        try:
            answers = await self._rt.client.ask(state, {question_id: question})
            trace.record(1, answers)
            choice = answers.choice(question_id)
        except (DecisionBackendError, KeyError, TypeError) as err:
            _LOGGER.warning("Reply judgment failed: %s", err)
            return None, (trace if trace.entries else None)
        trace.decide(question_id, choice.confidence, REPLY_CONF)
        return choice, trace

    async def _handle_reply(
        self, turn: _Turn, home: HomeModel, pending: PendingConfirm | PendingClarify
    ) -> conversation.ConversationResult:
        state = {"question": pending.question, "reply": turn.user_input.text}
        confirming = isinstance(pending, PendingConfirm)
        if confirming:
            question_id = CONFIRM_QID
            question = ChoiceQ(
                CONFIRM_INSTRUCTIONS,
                ("affirmative", "negative", "other"),
                CONFIRM_DESCRIPTIONS,
            )
        else:
            question_id = CLARIFY_QID
            question = ChoiceQ(CLARIFY_INSTRUCTIONS, (*pending.labels, NO_MATCH))
        answer, trace = await self._judge(state, question_id, question)
        if answer is None:
            return await self._escalate(
                turn,
                trace,
                "ReplyJudgmentFailed",
                pending_context(pending.question, self._pending_description(home, pending)),
            )
        if confirming:
            return await self._handle_confirm_reply(turn, home, pending, answer, trace)
        return await self._handle_clarify_reply(turn, home, pending, answer, trace)

    def _pending_description(
        self, home: HomeModel, pending: PendingConfirm | PendingClarify
    ) -> str:
        areas = self._area_names(home)
        if isinstance(pending, PendingConfirm):
            return self._action_clauses(pending.actions, areas, "en", done=False, params=True)
        phrase = verb_phrase(pending.verb.name, "en", done=False)
        return f"{phrase} one of: {', '.join(pending.labels)}"

    async def _handle_confirm_reply(
        self,
        turn: _Turn,
        home: HomeModel,
        pending: PendingConfirm,
        answer: ChoiceA,
        trace: Trace | None,
    ) -> conversation.ConversationResult:
        if answer.confidence >= REPLY_CONF and answer.choice == "affirmative":
            # Exactly the stored actions; the reply text is never re-parsed into actions.
            return await self._run(
                turn, home, pending.actions, pending.condition, trace, "Confirmed"
            )
        if answer.confidence >= REPLY_CONF and answer.choice == "negative":
            return self._result(turn, render("cancelled", turn.lang), trace, "Cancelled")
        return await self._escalate(
            turn,
            trace,
            "ConfirmOther",
            pending_context(pending.question, self._pending_description(home, pending)),
        )

    async def _handle_clarify_reply(
        self,
        turn: _Turn,
        home: HomeModel,
        pending: PendingClarify,
        answer: ChoiceA,
        trace: Trace | None,
    ) -> conversation.ConversationResult:
        areas = self._area_names(home)
        if answer.confidence >= REPLY_CONF and answer.choice in pending.labels:
            target = pending.candidates[pending.labels.index(answer.choice)]
            if pending.verb.param is not None and not pending.params:
                # The verb needs a value nobody has resolved; guessing one would be worse.
                phrase = verb_phrase(pending.verb.name, "en", done=False)
                return await self._escalate(
                    turn,
                    trace,
                    "ClarifyNeedsParam",
                    pending_context(pending.question, f"{phrase} {self._label(target, areas)}"),
                )
            action = Action(pending.verb, (target,), pending.params)
            if pending.verb.risk is Risk.CONFIRM:
                question = self._confirm_question((action,), None, "risk:confirm", areas, turn.lang)
                self._rt.pending.put(
                    turn.chat_log.conversation_id,
                    PendingConfirm((action,), None, question, self._rt.pending.now()),
                )
                return self._result(turn, question, trace, "NeedsConfirmation", cont=True)
            return await self._run(turn, home, (action,), None, trace, "Clarified")
        return await self._escalate(
            turn,
            trace,
            "ClarifyOther",
            pending_context(pending.question, self._pending_description(home, pending)),
        )
