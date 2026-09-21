"""Orchestrates rounds. Decides; never executes."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from hunch.client import DecisionBackendError, DecisionClient
from hunch.config import EngineConfig
from hunch.model import Entity, HomeModel
from hunch.phrasing import EN, Phrasebook
from hunch.questions import ChoiceQ, Question
from hunch.resolution import (
    Escalate,
    NeedsClarification,
    NeedsConfirmation,
    PreviousTurn,
    Resolution,
    Resolved,
    Trace,
)
from hunch.resolver import resolve
from hunch.round1 import (
    ADD_DEVICES,
    MORE_SAME,
    SAME_DEVICES,
    build_round1_questions,
    build_round1_state,
    interpret_round1,
)
from hunch.round2 import (
    NO_MATCH,
    build_round2_questions,
    build_round2_state,
    device_options,
    plan_round2,
)
from hunch.scope import (
    Candidates,
    Clarify,
    DeviceRound,
    ScopeEscalate,
    areas_shadowed_by_device_names,
    scope_candidates,
    verbatim_areas,
)
from hunch.vocabulary import Verb, Vocabulary


class Engine:
    def __init__(
        self,
        client: DecisionClient,
        vocabulary: Vocabulary,
        config: EngineConfig,
        phrasebook: Phrasebook = EN,
    ) -> None:
        self._client = client
        self._vocab = vocabulary
        self._config = config
        self._pb = phrasebook

    async def decide(
        self, home: HomeModel, prompt: str, previous: PreviousTurn | None = None
    ) -> Resolution:
        """Decide what `prompt` asks of `home`. Never executes, never raises on backend trouble.

        Returns one of four `Resolution` variants:

        - `Resolved(actions, condition, confidence, trace)` — execute as-is.
        - `NeedsConfirmation(actions, condition, reason, trace)` — ask the user first.
          `reason` is `"risk:confirm"`, `"blast_radius"` or `"confidence"`.
        - `NeedsClarification(question_key, candidates, trace, verb, params)` — ask which one.
          `question_key` is `"which_area"` or `"which_device"`. `verb` is the verb being
          clarified and `params` are the params already resolved for it in Round 2, or `{}`
          when the clarification happened before Round 2.
        - `Escalate(reason, partial, trace)` — hand the unchanged prompt to the fallback
          agent. `reason` is drawn from a closed set:

          | reason | meaning |
          |---|---|
          | `prompt_invalid` | empty prompt, or longer than `max_prompt_chars` |
          | `timing` | the request schedules, delays or sequences something |
          | `no_intent` | no verb fired |
          | `destructive` | a `DESTRUCTIVE` verb fired, or `is_destructive` did |
          | `low_confidence` | actions were built but confidence is below `confirm_band` |
          | `scope` | every fired verb resolved to no candidates |
          | `round_budget` | Round 2 was needed but `max_rounds` was already spent |
          | `decision_backend_unavailable` | backend error, timeout, wrong model id, bad answers |

        Every variant carries the same `Trace`. `Trace` is **mutable and shared** with the
        result: it is the live object the engine wrote to, not a copy, so callers must treat
        it as read-only (or snapshot it with `trace.to_dict()`). `trace.input_tokens` holds
        one entry per round in round order — the backend's reported input-token count, or
        `None` when that response carried no usage information.
        """
        trace = Trace()
        prompt = prompt.strip()
        if not prompt or len(prompt) > self._config.max_prompt_chars:
            return Escalate("prompt_invalid", (), trace)
        try:
            return await self._decide(home, prompt, trace, previous)
        except (DecisionBackendError, KeyError, TypeError) as exc:
            # KeyError/TypeError mean the backend answered with a missing id or the wrong
            # primitive; like an explicit backend error, that degrades, it never raises.
            reason = exc.reason if isinstance(exc, DecisionBackendError) else type(exc).__name__
            trace.note(f"backend_error:{reason}")
            return Escalate("decision_backend_unavailable", (), trace)

    async def _decide(
        self, home: HomeModel, prompt: str, trace: Trace, previous: PreviousTurn | None
    ) -> Resolution:
        th = self._config.thresholds
        rounds = 0

        answers = await self._client.ask(
            build_round1_state(home, prompt, previous),
            build_round1_questions(home, self._vocab, self._pb, previous),
        )
        rounds += 1
        shape = interpret_round1(home, self._vocab, answers, th, trace)

        if trace.decide("flag:has_timing", shape.flag("has_timing"), th.flag):
            return Escalate("timing", (), trace)
        if shape.condition_numeric:
            # "wenn es wärmer als 23 Grad ist": Round 2 asks for the sensor, the number and the
            # direction; the executor compares with the live value. Unresolvable -> hand-off.
            trace.note("condition:numeric")
        # Follow-ups: the sentence leans on the previous turn; code fills the half it leaves out.
        forced: dict[str, tuple[Entity, ...]] = {}
        forced_all: set[str] = set()  # verbs whose scoped candidates are all meant, no picking
        carried: dict[str, Mapping[str, float | str]] = {}
        carried_verbs: set[str] = set()
        prefer_pick: set[str] = set()
        add_previous = False
        if previous is not None and shape.follow_up:
            prev_targets = tuple(dict.fromkeys(e for a in previous.actions for e in a.targets))
            if shape.follow_up == MORE_SAME:
                trace.note("follow_up:replay")
                return Resolved(previous.actions, None, shape.follow_up_conf, trace)
            if shape.follow_up == SAME_DEVICES:
                for verb in shape.fired_verbs:
                    forced[verb.name] = tuple(e for e in prev_targets if verb.name in e.verbs)
            else:  # SAME_ACTION / ADD_DEVICES: the previous verb(s) unless this turn names one
                if not shape.fired_verbs:
                    prev_verbs = tuple(dict.fromkeys(a.verb for a in previous.actions))
                    shape = dataclasses.replace(shape, fired_verbs=prev_verbs)
                    carried_verbs.update(v.name for v in prev_verbs)
                    trace.note("follow_up:verb_carried")
                for a in previous.actions:
                    if a.params:
                        carried[a.verb.name] = a.params
                add_previous = shape.follow_up == ADD_DEVICES
                if add_previous:
                    # "und die Spots": the sentence names what joins — pick it; the planner asks
                    # no "all of these?" for it and the pick joins the previous targets.
                    prefer_pick.update(v.name for v in shape.fired_verbs)
                if not add_previous:
                    # "und im Esszimmer": the same kind of device as before, in the new place;
                    # a set before means the whole set there now, one device before means a pick
                    # (Round 2 sees `previous` and picks the matching one, e.g. the thermometer).
                    if not shape.scope_domains:
                        prev_domains = tuple(dict.fromkeys(e.domain for e in prev_targets))
                        shape = dataclasses.replace(shape, scope_domains=prev_domains)
                        trace.note("follow_up:domains_carried")
                    if len(prev_targets) > 1:
                        forced_all.update(v.name for v in shape.fired_verbs)
        if not shape.fired_verbs:
            return Escalate("no_intent", (), trace)

        # Scope. Jev already compared the places in Round 1 (one room, a floor, several, the
        # whole home, or none). Code adds one lookup: a room or floor whose name or alias is in
        # the prompt is the scope, whatever else half-fired.
        named_areas = verbatim_areas(home, prompt)
        shadowed = areas_shadowed_by_device_names(
            home,
            prompt,
            tuple(dict.fromkeys((*named_areas, *shape.scope_areas))),
            shape.fired_verbs,
            shape.scope_domains,
        )
        if shadowed:
            # "Dachterrasse Rollo zu": the word names the blind, not the (candidate-less) terrace.
            trace.note("area_shadowed:" + ",".join(shadowed))
            named_areas = tuple(a for a in named_areas if a not in shadowed)
            shape = dataclasses.replace(
                shape, scope_areas=tuple(a for a in shape.scope_areas if a not in shadowed)
            )
        if shape.whole_home and not named_areas:
            kept: tuple[str, ...] = ()
            trace.note("whole_home")
        elif named_areas:
            kept = named_areas
            dropped = tuple(a for a in shape.scope_areas if a not in named_areas)
            if dropped:
                trace.note("areas_dropped:named:" + ",".join(dropped))
            trace.note("area_match:" + ",".join(named_areas))
        else:
            kept = shape.scope_areas
        if kept != shape.scope_areas:
            shape = dataclasses.replace(shape, scope_areas=kept)

        per_verb: dict[str, tuple[Entity, ...]] = {}
        widened: set[str] = set()
        pending_clarify: Clarify | None = None
        pending_clarify_verb: Verb | None = None
        for verb in shape.fired_verbs:
            if verb.name in forced:
                if forced[verb.name]:
                    per_verb[verb.name] = forced[verb.name]
                else:
                    trace.note(f"dropped:{verb.name}:no_previous_targets")
                continue
            if shape.scene is not None and verb.name == "activate":
                per_verb[verb.name] = (shape.scene,)
                continue
            result = scope_candidates(home, verb, shape, self._config, trace, prompt)
            if isinstance(result, Candidates):
                per_verb[verb.name] = result.entities
                if result.widened:
                    widened.add(verb.name)  # Jev is asked in Round 2 whether these were meant
            elif isinstance(result, Clarify):
                if verb.is_query:
                    # "Which windows are open?" over the whole home: summarising state is the
                    # fallback agent's strength, and "which area?" is the wrong question.
                    trace.note(f"dropped:{verb.name}:query_over_cap")
                    continue
                # Ask only if no other verb has anything to act on; a co-firing verb that had
                # to widen past the cap is noise next to one that found its targets in scope.
                if pending_clarify is None:
                    pending_clarify_verb = verb
                pending_clarify = pending_clarify or result
            elif isinstance(result, ScopeEscalate):
                # One verb with nothing to apply to does not abort the turn; the others may
                # still resolve. Only an empty result set overall escalates.
                trace.note(f"dropped:{verb.name}:scope")
            elif isinstance(result, DeviceRound):
                if rounds >= self._config.max_rounds:
                    return (
                        NeedsClarification("which_device", result.entities, trace, verb)
                        if self._config.supports_clarification
                        else Escalate("scope", (), trace)
                    )
                chosen = await self._device_round(home, prompt, result.entities, trace)
                rounds += 1
                if chosen:
                    per_verb[verb.name] = chosen
                else:
                    trace.note(f"dropped:{verb.name}:scope")
        if not per_verb:
            if pending_clarify is not None:
                return NeedsClarification(
                    pending_clarify.question_key,
                    pending_clarify.candidates,
                    trace,
                    pending_clarify_verb,
                )
            return Escalate("scope", (), trace)
        if pending_clarify is not None:
            for v in shape.fired_verbs:
                if v.name not in per_verb and f"dropped:{v.name}:scope" not in trace.notes:
                    trace.note(f"dropped:{v.name}:scope")

        plan = plan_round2(
            home,
            shape,
            per_verb,
            th,
            self._config.scope_cap,
            prompt,
            frozenset(widened),
            frozenset(forced) | frozenset(forced_all),
            carried,
            frozenset(carried_verbs),
            frozenset(prefer_pick),
        )
        collective_queries = [
            v.name
            for v in shape.fired_verbs
            if v.is_query
            and v.name in per_verb
            and shape.flag("collective")
            >= th.collective  # Jev's judgment, not the planner's bucket
        ]
        if collective_queries:
            # "Welche Fenster sind offen?", "Wie viele Lichter sind an?": Jev says the question is
            # about a set. Reading and summarising many states is the fallback agent's strength;
            # a device-level answer or a "confirm reading 26 lights?" would be wrong here.
            for name in collective_queries:
                trace.note(f"query_collective:{name}")
            return Escalate("query_collective", (), trace)
        if shape.flag("has_condition") >= th.flag and not plan.condition_candidates:
            # The request carried a condition but nothing in scope can express it.
            trace.note("condition:unresolvable")
        questions: dict[str, Question] = build_round2_questions(shape, plan, home, self._pb)
        round2 = None
        if questions:
            if rounds >= self._config.max_rounds:
                trace.note("max_rounds_reached_before_round2")
                return Escalate("round_budget", (), trace)
            round2 = await self._client.ask(
                build_round2_state(prompt, plan, home, shape, previous), questions
            )
            rounds += 1
        result = resolve(shape, plan, round2, self._config, trace, self._pb)
        if (
            add_previous
            and previous is not None
            and isinstance(result, Resolved | NeedsConfirmation)
        ):
            # "die Stehlampe auch": the previous turn's devices join the same action
            merged = []
            for a in result.actions:
                extra = tuple(
                    e
                    for p in previous.actions
                    if p.verb.name == a.verb.name
                    for e in p.targets
                    if e not in a.targets
                )
                merged.append(dataclasses.replace(a, targets=(*a.targets, *extra)) if extra else a)
            if merged != list(result.actions):
                trace.note("follow_up:added_previous_targets")
                result = dataclasses.replace(result, actions=tuple(merged))
        return result

    async def _device_round(
        self, home: HomeModel, prompt: str, entities: tuple[Entity, ...], trace: Trace
    ) -> tuple[Entity, ...]:
        opts = device_options(entities, {a.area_id: a.name for a in home.areas})
        q = ChoiceQ(
            self._pb.device_question,
            tuple(o.label for o in opts) + (NO_MATCH,),
        )
        answers = await self._client.ask(
            {"request": prompt, "devices": [o.label for o in opts]}, {"device_round": q}
        )
        trace.record(2, answers)
        c = answers.choice("device_round")
        if c.choice == NO_MATCH:
            trace.note("no_match:device_round")
            return ()
        th = self._config.thresholds.target_choice_conf
        if not trace.decide("device_round", c.confidence, th):
            return ()
        opt = next((o for o in opts if o.label == c.choice), None)
        return opt.entities if opt else ()
