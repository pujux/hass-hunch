"""Orchestrates rounds. Decides; never executes."""

from __future__ import annotations

from hunch.client import DecisionBackendError, DecisionClient
from hunch.config import EngineConfig
from hunch.model import Entity, HomeModel
from hunch.questions import ChoiceQ, Question
from hunch.resolution import Escalate, NeedsClarification, Resolution, Trace
from hunch.resolver import resolve
from hunch.round1 import build_round1_questions, build_round1_state, interpret_round1
from hunch.round2 import (
    NO_MATCH,
    build_round2_questions,
    build_round2_state,
    device_options,
    plan_round2,
)
from hunch.scope import Candidates, Clarify, DeviceRound, ScopeEscalate, scope_candidates
from hunch.vocabulary import Vocabulary


class Engine:
    def __init__(
        self, client: DecisionClient, vocabulary: Vocabulary, config: EngineConfig
    ) -> None:
        self._client = client
        self._vocab = vocabulary
        self._config = config

    async def decide(self, home: HomeModel, prompt: str) -> Resolution:
        """Decide what `prompt` asks of `home`. Never executes, never raises on backend trouble.

        Returns one of four `Resolution` variants:

        - `Resolved(actions, condition, confidence, trace)` — execute as-is.
        - `NeedsConfirmation(actions, condition, reason, trace)` — ask the user first.
          `reason` is `"risk:confirm"`, `"blast_radius"` or `"confidence"`.
        - `NeedsClarification(question_key, candidates, trace)` — ask which one.
          `question_key` is `"which_area"` or `"which_device"`.
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
            return await self._decide(home, prompt, trace)
        except (DecisionBackendError, KeyError, TypeError) as exc:
            # KeyError/TypeError mean the backend answered with a missing id or the wrong
            # primitive; like an explicit backend error, that degrades, it never raises.
            reason = exc.reason if isinstance(exc, DecisionBackendError) else type(exc).__name__
            trace.note(f"backend_error:{reason}")
            return Escalate("decision_backend_unavailable", (), trace)

    async def _decide(self, home: HomeModel, prompt: str, trace: Trace) -> Resolution:
        th = self._config.thresholds
        rounds = 0

        answers = await self._client.ask(
            build_round1_state(home, prompt), build_round1_questions(home, self._vocab)
        )
        rounds += 1
        shape = interpret_round1(home, self._vocab, answers, th, trace)

        if trace.decide("flag:has_timing", shape.flag("has_timing"), th.flag):
            return Escalate("timing", (), trace)
        if not shape.fired_verbs:
            return Escalate("no_intent", (), trace)

        per_verb: dict[str, tuple[Entity, ...]] = {}
        for verb in shape.fired_verbs:
            if shape.scene is not None and verb.name == "activate":
                per_verb[verb.name] = (shape.scene,)
                continue
            result = scope_candidates(home, verb, shape, self._config, trace)
            if isinstance(result, Candidates):
                per_verb[verb.name] = result.entities
            elif isinstance(result, Clarify):
                return NeedsClarification(result.question_key, result.candidates, trace)
            elif isinstance(result, ScopeEscalate):
                # One verb with nothing to apply to does not abort the turn; the others may
                # still resolve. Only an empty result set overall escalates.
                trace.note(f"dropped:{verb.name}:scope")
            elif isinstance(result, DeviceRound):
                if rounds >= self._config.max_rounds:
                    return NeedsClarification("which_device", result.entities, trace) \
                        if self._config.supports_clarification else Escalate("scope", (), trace)
                chosen = await self._device_round(prompt, result.entities, trace)
                rounds += 1
                if chosen:
                    per_verb[verb.name] = chosen
                else:
                    trace.note(f"dropped:{verb.name}:scope")
        if not per_verb:
            return Escalate("scope", (), trace)

        plan = plan_round2(home, shape, per_verb, th, self._config.scope_cap)
        if shape.flag("has_condition") >= th.flag and not plan.condition_candidates:
            # The request carried a condition but nothing in scope can express it.
            trace.note("condition:unresolvable")
        questions: dict[str, Question] = build_round2_questions(shape, plan, home)
        round2 = None
        if questions:
            if rounds >= self._config.max_rounds:
                trace.note("max_rounds_reached_before_round2")
                return Escalate("round_budget", (), trace)
            round2 = await self._client.ask(build_round2_state(prompt, plan, home), questions)
            rounds += 1
        return resolve(shape, plan, round2, self._config, trace)

    async def _device_round(
        self, prompt: str, entities: tuple[Entity, ...], trace: Trace
    ) -> tuple[Entity, ...]:
        opts = device_options(entities)
        q = ChoiceQ(
            "Which device does the request refer to?",
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
