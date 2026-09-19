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
    plan_round2,
    target_options,
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
        trace = Trace()
        prompt = prompt.strip()
        if not prompt or len(prompt) > self._config.max_prompt_chars:
            return Escalate("prompt_invalid", (), trace)
        try:
            return await self._decide(home, prompt, trace)
        except DecisionBackendError as exc:
            trace.note(f"backend_error:{exc.reason}")
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
                return Escalate(result.reason, (), trace)
            elif isinstance(result, DeviceRound):
                if rounds >= self._config.max_rounds:
                    return NeedsClarification("which_device", result.entities, trace) \
                        if self._config.supports_clarification else Escalate("scope", (), trace)
                per_verb[verb.name] = await self._device_round(prompt, result.entities, trace)
                rounds += 1
                if not per_verb[verb.name]:
                    return Escalate("scope", (), trace)

        plan = plan_round2(home, shape, per_verb, th)
        questions: dict[str, Question] = build_round2_questions(shape, plan, home)
        round2 = None
        if questions:
            if rounds >= self._config.max_rounds:
                trace.note("max_rounds_reached_before_round2")
                return Escalate("low_confidence", (), trace)
            round2 = await self._client.ask(build_round2_state(prompt, plan, home), questions)
            rounds += 1
        return resolve(shape, plan, round2, self._config, trace)

    async def _device_round(
        self, prompt: str, entities: tuple[Entity, ...], trace: Trace
    ) -> tuple[Entity, ...]:
        opts = target_options(entities)
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
