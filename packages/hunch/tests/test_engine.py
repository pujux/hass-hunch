
from hunch.client import DecisionBackendError, FakeDecisionClient
from hunch.config import EngineConfig
from hunch.engine import Engine
from hunch.questions import ChoiceA, ChoiceQ, NoulA, ScoreQ
from hunch.resolution import Escalate, NeedsClarification, Resolved
from hunch.round2 import NO_MATCH


def _scripted(round1: dict, round2: dict | None = None, device: dict | None = None):
    """Answer Round 1 by id override; default everything else to 'no'.

    Round 2 / device round answers are looked up by id as well.
    """
    calls = {"n": 0}

    def script(state, qs):
        calls["n"] += 1
        out = {}
        for qid, q in qs.items():
            if qid in round1:
                out[qid] = round1[qid]
            elif round2 and qid in round2:
                out[qid] = round2[qid]
            elif device and qid in device:
                out[qid] = device[qid]
            elif isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
            elif isinstance(q, ScoreQ):
                from hunch.questions import ScoreA
                out[qid] = ScoreA(1.0, 0.9, {})
            else:
                out[qid] = NoulA(0.05)
        return out

    return FakeDecisionClient(script), calls


async def test_collective_downstairs_resolves_in_one_round(home, vocab, config):
    client, calls = _scripted({
        "verb:turn_off": NoulA(0.95), "floor:downstairs": NoulA(0.9),
        "domain:light": NoulA(0.9), "flag:collective": NoulA(0.9),
    })
    r = await Engine(client, vocab, config).decide(home, "turn off the downstairs lights")
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling", "light.kitchen_counter", "light.living_main",
        "light.reading_lamp", "light.hallway",
    }
    assert calls["n"] == 1


async def test_exception_uses_second_round(home, vocab, config):
    client, calls = _scripted(
        {
            "verb:turn_off": NoulA(0.95), "area:kitchen": NoulA(0.9),
            "flag:collective": NoulA(0.9), "flag:has_exception": NoulA(0.85),
        },
        {"exclude:turn_off:switch.fridge": NoulA(0.93)},
    )
    r = await Engine(client, vocab, config).decide(
        home, "turn off everything in the kitchen except the fridge"
    )
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling", "light.kitchen_counter",
    }
    assert calls["n"] == 2
    assert "entities" not in client.calls[0][0] and "candidates" in client.calls[1][0]


async def test_singular_uses_choice(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:turn_on": NoulA(0.9), "area:living": NoulA(0.85),
            "domain:light": NoulA(0.8), "flag:collective": NoulA(0.1),
        },
        {"target:turn_on": ChoiceA("Reading lamp", 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "turn on the lamp in the lounge")
    assert isinstance(r, Resolved)
    assert [e.entity_id for e in r.actions[0].targets] == ["light.reading_lamp"]


async def test_timing_escalates_immediately(home, vocab, config):
    client, calls = _scripted({"verb:turn_off": NoulA(0.9), "flag:has_timing": NoulA(0.8)})
    r = await Engine(client, vocab, config).decide(home, "turn off the lights in ten minutes")
    assert isinstance(r, Escalate) and r.reason == "timing" and calls["n"] == 1


async def test_no_intent_escalates(home, vocab, config):
    client, _ = _scripted({})
    r = await Engine(client, vocab, config).decide(home, "tell me a joke")
    assert isinstance(r, Escalate) and r.reason == "no_intent"


async def test_scene_short_circuits(home, vocab, config):
    client, calls = _scripted({
        "verb:activate": NoulA(0.9),
        "scene": ChoiceA("Movie night", 0.9, {"Movie night": 0.9, "Goodnight": 0.05, "none": 0.05}),
    })
    r = await Engine(client, vocab, config).decide(home, "movie night please")
    assert isinstance(r, Resolved) and r.actions[0].targets[0].entity_id == "scene.movie_night"
    assert calls["n"] == 1


async def test_clarify_when_scope_too_wide(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=False)
    client, _ = _scripted({
        "verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.1), "domain:light": NoulA(0.3),
    })
    r = await Engine(client, vocab, cfg).decide(home, "turn on the light")
    assert isinstance(r, NeedsClarification) and r.question_key == "which_area"


async def test_device_round_when_enabled(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=True, max_rounds=3)
    client, calls = _scripted(
        {"verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.1), "domain:light": NoulA(0.3)},
        {"target:turn_on": ChoiceA("Christmas tree", 0.9, {})},
        {"device_round": ChoiceA("Christmas tree", 0.9, {})},
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on the christmas tree")
    assert isinstance(r, Resolved) and r.actions[0].targets[0].entity_id == "light.christmas_tree"
    assert calls["n"] == 2  # round 1 + device round; single-option target needs no round 2


async def test_device_round_offers_devices_not_entities(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=True, max_rounds=3)
    client, calls = _scripted(
        {"verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.9), "domain:light": NoulA(0.3)},
        None,
        {"device_round": ChoiceA("Bedside lamps", 0.9, {})},
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on both bedside lamps")
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.bedroom_left", "light.bedroom_right",
    }
    options = client.calls[1][1]["device_round"].options
    assert options.count("Bedside lamps") == 1
    assert not any(o.startswith("Bedside lamps — ") for o in options)
    assert calls["n"] == 2  # round 1 + device round; the collective path needs no round 2


async def test_device_round_no_match_escalates(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=True, max_rounds=3)
    client, _ = _scripted(
        {"verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.1), "domain:light": NoulA(0.3)},
        None,
        {"device_round": ChoiceA(NO_MATCH, 0.9, {})},
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on the thingamajig")
    assert isinstance(r, Escalate) and r.reason == "scope"


async def test_verb_with_no_candidates_is_dropped_not_fatal(home, vocab, config):
    # The home has no alarm panel, so verb:arm can never apply; turn_off still resolves.
    client, _ = _scripted({
        "verb:turn_off": NoulA(0.95), "verb:arm": NoulA(0.9),
        "area:hallway": NoulA(0.9), "flag:collective": NoulA(0.9),
    })
    r = await Engine(client, vocab, config).decide(home, "turn off the hallway and arm the alarm")
    assert isinstance(r, Resolved)
    assert [a.verb.name for a in r.actions] == ["turn_off"]
    assert [e.entity_id for e in r.actions[0].targets] == ["light.hallway"]
    assert "dropped:arm:scope" in r.trace.notes


async def test_scope_escalates_only_when_every_verb_is_dropped(home, vocab, config):
    client, _ = _scripted({"verb:arm": NoulA(0.9), "flag:collective": NoulA(0.9)})
    r = await Engine(client, vocab, config).decide(home, "arm the alarm")
    assert isinstance(r, Escalate) and r.reason == "scope"


async def test_round_budget_escalates_when_round2_does_not_fit(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", max_rounds=1)
    client, calls = _scripted({
        "verb:turn_on": NoulA(0.9), "area:living": NoulA(0.85),
        "domain:light": NoulA(0.8), "flag:collective": NoulA(0.1),
    })
    r = await Engine(client, vocab, cfg).decide(home, "turn on the lamp in the lounge")
    assert isinstance(r, Escalate) and r.reason == "round_budget"
    assert calls["n"] == 1


async def test_unresolvable_condition_is_noted(home, vocab, config):
    # The condition is about an alarm panel; the home has none, so nothing can express it.
    client, _ = _scripted({
        "verb:turn_off": NoulA(0.95), "area:hallway": NoulA(0.9), "flag:collective": NoulA(0.9),
        "flag:has_condition": NoulA(0.9),
        "condition_domain": ChoiceA("none", 0.9, {}),
    })
    r = await Engine(client, vocab, config).decide(home, "turn the hallway off if nobody is home")
    assert "condition:unresolvable" in r.trace.notes


async def test_backend_error_escalates(home, vocab, config):
    class Boom:
        async def ask(self, state, questions):
            raise DecisionBackendError("decision_backend_unavailable")
    r = await Engine(Boom(), vocab, config).decide(home, "turn off the lights")
    assert isinstance(r, Escalate) and r.reason == "decision_backend_unavailable"


async def test_partial_round1_answers_escalate_as_backend_unavailable(home, vocab, config):
    def script(state, qs):
        out = {qid: NoulA(0.05) for qid in qs if not isinstance(qs[qid], ChoiceQ)}
        out.pop("verb:turn_off", None)  # backend dropped one answer
        for qid, q in qs.items():
            if isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none", 0.9, {})
        return out

    r = await Engine(FakeDecisionClient(script), vocab, config).decide(home, "turn off the lights")
    assert isinstance(r, Escalate) and r.reason == "decision_backend_unavailable"
    assert any(n.startswith("backend_error:") for n in r.trace.notes)


async def test_prompt_prechecks(home, vocab, config):
    eng = Engine(FakeDecisionClient({}), vocab, config)
    assert isinstance(await eng.decide(home, "   "), Escalate)
    assert (await eng.decide(home, "x" * 501)).reason == "prompt_invalid"


async def test_trace_travels_with_result(home, vocab, config):
    client, _ = _scripted({
        "verb:turn_off": NoulA(0.95), "area:hallway": NoulA(0.9), "flag:collective": NoulA(0.9),
    })
    r = await Engine(client, vocab, config).decide(home, "hallway off")
    assert r.trace.models == ["fake"]
    assert any(e.question_id == "verb:turn_off" for e in r.trace.entries)
