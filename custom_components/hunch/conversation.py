"""Hunch conversation entity: decide, execute, ask, or hand off. Spec §7."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from hunch import (
    Action,
    ActiveTimer,
    Condition,
    DecisionBackendError,
    Entity,
    Escalate,
    HomeModel,
    NeedsClarification,
    NeedsConfirmation,
    PreviousTurn,
    Resolved,
    Risk,
    TimerCommand,
    Timing,
    Trace,
)
from hunch.questions import ChoiceA, ChoiceQ
from hunch.round2 import NO_MATCH
from hunch.timing import ALL_TIMERS

from . import HunchConfigEntry, HunchRuntime
from .executor import Executor
from .pending import PendingClarify, PendingConfirm, PendingTimerPick, PendingTurn
from .responder import (
    condition_clause,
    condition_context,
    describe_action,
    describe_expected,
    describe_state,
    describe_targets,
    format_duration,
    format_value,
    pending_context,
    render,
    resolve_language,
    revert_words,
    timer_name,
    timing_clause,
    verb_phrase,
)
from .timers import (
    HunchTimer,
    PriorState,
    StoredAction,
    already_in_state,
    inverse_actions,
    new_timer_id,
    stored_actions,
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


def _response(
    language: str, text: str, *, error: bool = False, mark: Mapping[str, Any] | None = None
) -> intent.IntentResponse:
    resp = intent.IntentResponse(language=language)
    if error:
        resp.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, text)
    else:
        resp.async_set_speech(text, extra_data={"hunch": dict(mark)} if mark else None)
    return resp


def _mark_speech(response: intent.IntentResponse, mark: Mapping[str, Any]) -> None:
    """Stamp `extra_data.hunch` onto every speech variant of a response we hand back — the
    Assist debug view shows it under intent_output, so a spoken sentence can be traced to Hunch
    or to the fallback agent."""
    for variant in response.speech.values():
        if isinstance(variant, dict):
            variant["extra_data"] = {"hunch": dict(mark)}


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
        self._trace_event({"outcome": outcome, "answered_by": "hunch", "spoken": text})
        return conversation.ConversationResult(
            response=_response(
                turn.user_input.language,
                text,
                error=error,
                mark={"outcome": outcome, "answered_by": "hunch"},
            ),
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
            # Visible in the Assist debug view ("Roh"): this turn left Hunch, and why.
            self._trace_event(
                {
                    "outcome": outcome,
                    "handed_off_to": agent_id or "home_assistant_default_agent",
                    "with_context": extra_system_prompt is not None,
                }
            )
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
                _mark_speech(
                    result.response,
                    {
                        "outcome": outcome,
                        "handed_off_to": agent_id or "home_assistant_default_agent",
                        "with_context": extra_system_prompt is not None,
                    },
                )
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

    @staticmethod
    def _trace_event(data: Mapping[str, Any]) -> None:
        """An `agent_detail` event on HA's conversation trace; a no-op outside a traced run."""
        try:
            conversation.async_conversation_trace_append(
                conversation.ConversationTraceEventType.AGENT_DETAIL, {"hunch": dict(data)}
            )
        except Exception:  # noqa: BLE001 - tracing must never break a turn
            _LOGGER.debug("conversation trace unavailable", exc_info=True)

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
            parts.append(
                describe_action(
                    action.verb.name,
                    describe_targets(action.targets, areas, language),
                    action.params,
                    language,
                    done=done,
                )
            )
        return "; ".join(parts)

    # ---- executing -------------------------------------------------------------------

    def _remember(self, turn: _Turn, actions: tuple[Action, ...]) -> None:
        """A completed turn becomes the context the next sentence may lean on."""
        if actions:
            self._rt.last_turns.put(
                turn.chat_log.conversation_id, PreviousTurn(turn.user_input.text, actions)
            )

    def _forget(self, turn: _Turn) -> None:
        """A timed turn or a timer command is never context, and neither is what came before
        it: "und im Esszimmer" may not lean on a turn older than the timed one (spec §3)."""
        self._rt.last_turns.forget(turn.chat_log.conversation_id)

    async def _run(
        self,
        turn: _Turn,
        home: HomeModel,
        actions: tuple[Action, ...],
        condition: Condition | None,
        trace: Trace | None,
        outcome: str,
        timing: Timing | None = None,
    ) -> conversation.ConversationResult:
        """Step 3: check the condition, then execute or read — or, with `timing`, schedule
        ("in 10 Minuten") or execute and schedule the undo ("für 15 Minuten"). A timed turn is
        never remembered for follow-ups. The turn before it was already cleared when the timed
        request was decided (`_async_handle_message`); a confirmed or clarified timed reply only
        runs the pending turn that decision left, so nothing was remembered in between."""
        lang = turn.lang
        areas = self._area_names(home)
        executor = Executor(self.hass)
        if condition is not None:
            check = executor.check_condition(condition)
            if check.holds is not True:
                text = render(
                    "condition_not_met",
                    lang,
                    subject=self._label(condition.subject, areas),
                    expected=describe_expected(condition, lang),
                    value=format_value(check.value, check.unit, lang) if check.value else None,
                )
                return self._result(turn, text, trace, outcome)
        # A mixed plan reads its query targets *and* runs its commands; neither may swallow
        # the other (spec §9).
        queries = tuple(a for a in actions if a.verb.is_query)
        commands = tuple(a for a in actions if not a.verb.is_query)
        lines = []
        for action in queries:
            for reading in await executor.read_states(action.targets):
                room = areas.get(reading.area_id or "")
                label = f"{reading.name} ({room})" if room else reading.name
                lines.append(f"{label}: {describe_state(reading, lang)}")
        if not commands:  # an empty plan reads nothing, safely
            if timing is None:
                self._remember(turn, actions)
            return self._result(turn, render("query_answer", lang, lines=lines), trace, outcome)
        if timing is not None and timing.kind == "delayed":
            # Nothing runs now; the plan is stored and carried out when the timer is due.
            body = self._action_clauses(commands, areas, lang, done=False)
            await self._rt.timers.async_add(
                self._make_timer(
                    turn,
                    "delayed",
                    stored_actions(commands),
                    description=body,
                    duration=timing.seconds,
                )
            )
            text = render(
                "delayed_scheduled",
                lang,
                duration=format_duration(timing.seconds, lang),
                body=body,
            )
            return self._result(turn, "\n".join([*lines, text]), trace, outcome)
        before: dict[str, PriorState | None] = {}
        if timing is not None and timing.kind == "for_duration":
            # read, not judged: which targets are already where the command takes them
            before = {
                e.entity_id: (
                    (s.state, s.attributes.get("current_position"))
                    if (s := self.hass.states.get(e.entity_id))
                    else None
                )
                for a in commands
                for e in a.targets
            }
        results = await executor.execute(commands, turn.user_input.context)
        failed = [r.entity_id for r in results if not r.ok]
        if timing is None:
            self._remember(turn, actions)
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
        if timing is not None and timing.kind == "for_duration":
            # only what succeeded *and* was not already in the commanded state is changed back;
            # when nothing changed, no revert is stored and the reply is the plain one
            ok_ids = {r.entity_id for r in results if r.ok} - already_in_state(commands, before)
            revert = inverse_actions(commands, ok_ids)
            if revert:
                await self._rt.timers.async_add(
                    self._make_timer(
                        turn,
                        "revert",
                        stored_actions(revert),
                        description=self._action_clauses(revert, areas, lang, done=False),
                        duration=timing.seconds,
                    )
                )
                done = tuple(
                    Action(a.verb, ok, a.params)
                    for a in commands
                    if (ok := tuple(e for e in a.targets if e.entity_id in ok_ids))
                )
                sentence = render(
                    # after "Erledigt, außer: …" a second "Erledigt:" would repeat itself
                    "for_duration_rest" if failed else "for_duration_done",
                    lang,
                    body=self._action_clauses(done, areas, lang, done=True),
                    duration=format_duration(timing.seconds, lang),
                    revert=revert_words([a.verb.name for a in done], lang),
                )
                text = f"{text}\n{sentence}" if failed else sentence
        if lines:
            text = "\n".join([*lines, text])
        return self._result(turn, text, trace, outcome)

    # ---- timers ----------------------------------------------------------------------

    def _make_timer(
        self,
        turn: _Turn,
        kind: str,
        actions: tuple[StoredAction, ...],
        *,
        label: str | None = None,
        description: str | None = None,
        duration: float,
    ) -> HunchTimer:
        """A timer for this turn: who asked, from where, and — for stored actions — as whom
        they run later (HA's permission checks apply as they did to the immediate half)."""
        user_input = turn.user_input
        device_id = user_input.device_id
        device = dr.async_get(self.hass).async_get(device_id) if device_id else None
        return HunchTimer(
            timer_id=new_timer_id(),
            kind=kind,
            label=label,
            description=description,
            duration_seconds=duration,
            due_at=dt_util.utcnow() + timedelta(seconds=duration),
            actions=actions,
            language=turn.lang,
            conversation_id=turn.chat_log.conversation_id,
            device_id=device_id,
            satellite_id=user_input.satellite_id,
            area_id=device.area_id if device is not None else None,
            user_id=user_input.context.user_id,
        )

    def _live(self, timers: Sequence[ActiveTimer]) -> list[HunchTimer]:
        """The engine's `ActiveTimer`s are a snapshot; re-read each from the store. One that
        fired or was cancelled meanwhile is skipped."""
        live = {t.timer_id: t for t in self._rt.timers.active()}
        return [live[t.timer_id] for t in timers if t.timer_id in live]

    @staticmethod
    def _timer_name(timer: HunchTimer, lang: str) -> str:
        return timer_name(timer.label, timer.description, timer.duration_seconds, lang)

    async def _run_timer(
        self, turn: _Turn, command: TimerCommand, trace: Trace | None
    ) -> conversation.ConversationResult:
        """A timer command the engine resolved. Never remembered for follow-ups, and it clears
        the turn before it (spec §3)."""
        rt = self._rt
        lang = turn.lang
        self._forget(turn)
        if command.kind == "start":
            duration = float(command.duration_seconds or 0.0)
            timer = self._make_timer(turn, "timer", (), label=command.label, duration=duration)
            await rt.timers.async_add(timer)
            text = render(
                "timer_started",
                lang,
                name=self._timer_name(timer, lang),
                duration=format_duration(duration, lang),
            )
            return self._result(turn, text, trace, "Resolved")
        if command.kind == "cancel":
            return await self._cancel_timers(turn, command.timers, trace, "Resolved")
        lines = [
            render(
                "timer_remaining_line",
                lang,
                name=self._timer_name(t, lang),
                remaining=format_duration(rt.timers.remaining(t), lang),
            )
            for t in self._live(command.timers)
        ]
        if not lines:
            return self._result(turn, render("timer_none", lang), trace, "Resolved")
        return self._result(turn, render("timer_remaining", lang, lines=lines), trace, "Resolved")

    async def _cancel_timers(
        self, turn: _Turn, timers: Sequence[ActiveTimer], trace: Trace | None, outcome: str
    ) -> conversation.ConversationResult:
        names = []
        for t in timers:
            cancelled = await self._rt.timers.async_cancel(t.timer_id)
            if cancelled is not None:  # one that fired meanwhile is not claimed as cancelled
                names.append(self._timer_name(cancelled, turn.lang))
        if not names:
            return self._result(turn, render("timer_none", turn.lang), trace, outcome)
        return self._result(turn, render("timer_cancelled", turn.lang, names=names), trace, outcome)

    def _timer_pick(
        self, timers: Sequence[ActiveTimer], lang: str, created: float
    ) -> PendingTimerPick | None:
        """The "which timer?" question: one spoken label per timer still running ("Timer für
        Nudeln (3 Minuten 20 Sekunden)"), plus "all timers" when there are several — with one
        timer the two options are the same set and would only split the reply's judgment (the
        engine leaves it out for the same reason). None when no timer is left."""
        rt = self._rt
        live = {t.timer_id: t for t in rt.timers.active()}
        kept: list[ActiveTimer] = []
        spoken: list[str] = []
        for t in timers:
            timer = live.get(t.timer_id)
            if timer is None:
                continue
            base = (
                f"{self._timer_name(timer, lang)} "
                f"({format_duration(rt.timers.remaining(timer), lang)})"
            )
            label, n = base, 2
            while label in spoken:
                label = f"{base} #{n}"
                n += 1
            kept.append(t)
            spoken.append(label)
        if not kept:
            return None
        labels = (*spoken, ALL_TIMERS) if len(kept) > 1 else tuple(spoken)
        question = render("which_timer", lang, options=spoken)
        return PendingTimerPick(tuple(kept), labels, question, created)

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

        # Step 2: decide — with the last completed turn as context for follow-ups.
        previous = rt.last_turns.get(chat_log.conversation_id)
        result = await rt.engine.decide(
            home, user_input.text, previous, rt.timers.as_active_timers()
        )
        areas = self._area_names(home)
        # A timed request clears the turn before it, whatever becomes of it (run, declined,
        # clarified, handed off): a later "und im Esszimmer" must not lean on a turn from
        # before it (spec addendum, item 5).
        if getattr(result, "timing", None) is not None or (
            isinstance(result, Escalate) and result.reason == "timing"
        ):
            self._forget(turn)

        if isinstance(result, Resolved):  # step 3
            if result.timer is not None:
                return await self._run_timer(turn, result.timer, result.trace)
            return await self._run(
                turn,
                home,
                result.actions,
                result.condition,
                result.trace,
                "Resolved",
                timing=result.timing,
            )

        if isinstance(result, NeedsConfirmation):  # step 4
            question = self._confirm_question(
                result.actions, result.condition, result.reason, areas, lang, result.timing
            )
            rt.pending.put(
                conversation_id,
                PendingConfirm(
                    result.actions, result.condition, question, rt.pending.now(), result.timing
                ),
            )
            return self._result(turn, question, result.trace, "NeedsConfirmation", cont=True)

        if isinstance(result, NeedsClarification) and result.question_key == "which_timer":
            self._forget(turn)  # a timer command, whatever the reply picks
            pick = self._timer_pick(result.timers, lang, rt.pending.now())
            if pick is None:  # every timer asked about is gone already
                return self._result(turn, render("timer_none", lang), result.trace, "Resolved")
            rt.pending.put(conversation_id, pick)
            return self._result(turn, pick.question, result.trace, "NeedsClarification", cont=True)

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
                    result.timing,
                ),
            )
            return self._result(turn, question, result.trace, "NeedsClarification", cont=True)

        assert isinstance(result, Escalate)  # step 7
        extra = condition_context() if result.reason == "condition" else None
        return await self._escalate(turn, result.trace, f"Escalate:{result.reason}", extra)

    def _confirm_question(
        self,
        actions: Sequence[Action],
        condition: Condition | None,
        reason: str,
        areas: Mapping[str, str],
        lang: str,
        timing: Timing | None = None,
    ) -> str:
        clause = ""
        if condition is not None:
            clause = condition_clause(
                self._label(condition.subject, areas), describe_expected(condition, lang), lang
            )
        clause += timing_clause(timing, lang)
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
        self, turn: _Turn, home: HomeModel, pending: PendingTurn
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
        if answer is None and isinstance(pending, PendingTimerPick):
            # The fallback agent cannot cancel Hunch timers and might cancel something of its
            # own; a timer pick is never handed to it, not even when the judgment failed.
            return self._result(
                turn, render("cancelled", turn.lang), trace, "TimerPickJudgmentFailed"
            )
        if answer is None:
            return await self._escalate(
                turn,
                trace,
                "ReplyJudgmentFailed",
                pending_context(pending.question, self._pending_description(home, pending)),
            )
        if isinstance(pending, PendingConfirm):
            return await self._handle_confirm_reply(turn, home, pending, answer, trace)
        if isinstance(pending, PendingTimerPick):
            return await self._handle_timer_pick_reply(turn, pending, answer, trace)
        return await self._handle_clarify_reply(turn, home, pending, answer, trace)

    def _pending_description(self, home: HomeModel, pending: PendingTurn) -> str:
        areas = self._area_names(home)
        if isinstance(pending, PendingConfirm):
            return self._action_clauses(
                pending.actions, areas, "en", done=False, params=True
            ) + timing_clause(pending.timing, "en")
        if isinstance(pending, PendingTimerPick):
            return "cancel one of the timers: " + ", ".join(pending.labels)
        phrase = verb_phrase(pending.verb.name, "en", done=False)
        # the timing travels with the hand-off, or the fallback would act now on "in 15 Minuten"
        return f"{phrase} one of: {', '.join(pending.labels)}" + timing_clause(pending.timing, "en")

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
                turn,
                home,
                pending.actions,
                pending.condition,
                trace,
                "Confirmed",
                timing=pending.timing,
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
                    pending_context(
                        pending.question,
                        f"{phrase} {self._label(target, areas)}"
                        + timing_clause(pending.timing, "en"),
                    ),
                )
            action = Action(pending.verb, (target,), pending.params)
            if pending.verb.risk is Risk.CONFIRM:
                question = self._confirm_question(
                    (action,), None, "risk:confirm", areas, turn.lang, pending.timing
                )
                self._rt.pending.put(
                    turn.chat_log.conversation_id,
                    PendingConfirm(
                        (action,), None, question, self._rt.pending.now(), pending.timing
                    ),
                )
                return self._result(turn, question, trace, "NeedsConfirmation", cont=True)
            return await self._run(
                turn, home, (action,), None, trace, "Clarified", timing=pending.timing
            )
        return await self._escalate(
            turn,
            trace,
            "ClarifyOther",
            pending_context(pending.question, self._pending_description(home, pending)),
        )

    async def _handle_timer_pick_reply(
        self,
        turn: _Turn,
        pending: PendingTimerPick,
        answer: ChoiceA,
        trace: Trace | None,
    ) -> conversation.ConversationResult:
        """A sure pick cancels that timer ("all timers": every one asked about). Anything else
        changes nothing — the fallback agent cannot cancel Hunch timers, so this turn is never
        handed to it."""
        if answer.confidence >= REPLY_CONF and answer.choice == ALL_TIMERS:
            return await self._cancel_timers(turn, pending.timers, trace, "TimerPicked")
        if answer.confidence >= REPLY_CONF and answer.choice in pending.labels:
            timer = pending.timers[pending.labels.index(answer.choice)]
            return await self._cancel_timers(turn, (timer,), trace, "TimerPicked")
        return self._result(turn, render("cancelled", turn.lang), trace, "TimerPickOther")
