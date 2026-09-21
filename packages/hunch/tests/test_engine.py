from hunch.client import DecisionBackendError, FakeDecisionClient
from hunch.config import EngineConfig
from hunch.engine import Engine
from hunch.questions import ChoiceA, ChoiceQ, NoulA, ScoreA, ScoreQ
from hunch.resolution import Action, Escalate, NeedsClarification, NeedsConfirmation, Resolved
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


async def test_collective_downstairs_resolves_in_one_round(home, vocab, config):
    client, calls = _scripted(
        {
            "verb:turn_off": NoulA(0.95),
            "floor:downstairs": NoulA(0.9),
            "domain:light": NoulA(0.9),
            "flag:collective": NoulA(0.9),
            "area_primary": ChoiceA("several", 0.9, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "turn off the downstairs lights")
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
        "light.living_main",
        "light.reading_lamp",
        "light.hallway",
    }
    assert calls["n"] == 1


async def test_exception_uses_second_round(home, vocab, config):
    # The excepted device is NOT named verbatim ("the cold one"), so code cannot resolve the
    # exception and Round 2 asks one exclusion Noul per kitchen candidate.
    client, calls = _scripted(
        {
            "verb:turn_off": NoulA(0.95),
            "area:kitchen": NoulA(0.9),
            "flag:collective": NoulA(0.9),
            "flag:has_exception": NoulA(0.85),
            "area_primary": ChoiceA("several", 0.9, {}),
        },
        {"exclude:turn_off:switch.fridge": NoulA(0.93)},
    )
    r = await Engine(client, vocab, config).decide(
        home, "turn off everything in the kitchen except the cold one"
    )
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
    }
    assert calls["n"] == 2
    assert "entities" not in client.calls[0][0] and "candidates" in client.calls[1][0]


async def test_singular_uses_choice(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:turn_on": NoulA(0.9),
            "area:living": NoulA(0.85),
            "domain:light": NoulA(0.8),
            "flag:collective": NoulA(0.1),
            "area_primary": ChoiceA("several", 0.9, {}),
        },
        {"target:turn_on": ChoiceA("Reading lamp", 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "turn on the light in the lounge")
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
    client, calls = _scripted(
        {
            "verb:activate": NoulA(0.9),
            "scene": ChoiceA(
                "Movie night", 0.9, {"Movie night": 0.9, "Goodnight": 0.05, "none": 0.05}
            ),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "movie night please")
    assert isinstance(r, Resolved) and r.actions[0].targets[0].entity_id == "scene.movie_night"
    assert calls["n"] == 1


async def test_clarify_when_scope_too_wide(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=False)
    client, _ = _scripted(
        {
            "verb:turn_on": NoulA(0.9),
            "flag:collective": NoulA(0.1),
            "domain:light": NoulA(0.3),
        }
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on the light")
    assert isinstance(r, NeedsClarification) and r.question_key == "which_area"


async def test_device_round_when_enabled(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=True, max_rounds=3)
    client, calls = _scripted(
        {"verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.1), "domain:light": NoulA(0.3)},
        {"target:turn_on": ChoiceA("Christmas tree", 0.9, {})},
        {"device_round": ChoiceA("Christmas tree", 0.9, {})},
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on the festive thing")
    assert isinstance(r, Resolved) and r.actions[0].targets[0].entity_id == "light.christmas_tree"
    assert calls["n"] == 2  # round 1 + device round; single-option target needs no round 2


async def test_device_round_offers_devices_not_entities(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=True, max_rounds=3)
    client, calls = _scripted(
        {"verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.9), "domain:light": NoulA(0.3)},
        None,
        {"device_round": ChoiceA("Bedside lamps", 0.9, {})},
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on both of them by the bed")
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.bedroom_left",
        "light.bedroom_right",
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
    client, _ = _scripted(
        {
            "verb:turn_off": NoulA(0.95),
            "verb:arm": NoulA(0.9),
            "area:hallway": NoulA(0.9),
            "flag:collective": NoulA(0.9),
            "area_primary": ChoiceA("several", 0.9, {}),
        }
    )
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
    client, calls = _scripted(
        {
            "verb:turn_on": NoulA(0.9),
            "area:living": NoulA(0.85),
            "domain:light": NoulA(0.8),
            "flag:collective": NoulA(0.1),
            "area_primary": ChoiceA("several", 0.9, {}),
        }
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on the light in the lounge")
    assert isinstance(r, Escalate) and r.reason == "round_budget"
    assert calls["n"] == 1


async def test_unresolvable_condition_is_noted(home, vocab, config):
    # The condition is about an alarm panel; the home has none, so nothing can express it.
    client, _ = _scripted(
        {
            "verb:turn_off": NoulA(0.95),
            "area:hallway": NoulA(0.9),
            "flag:collective": NoulA(0.9),
            "flag:has_condition": NoulA(0.9),
            "condition_domain": ChoiceA("none", 0.9, {}),
        }
    )
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
    client, _ = _scripted(
        {
            "verb:turn_off": NoulA(0.95),
            "area:hallway": NoulA(0.9),
            "flag:collective": NoulA(0.9),
            "area_primary": ChoiceA("several", 0.9, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "hallway off")
    assert r.trace.models == ["fake"]
    assert any(e.question_id == "verb:turn_off" for e in r.trace.entries)


# --- end-to-end paths through the whole pipeline -------------------------------------------


async def test_confirm_tier_verb_ends_in_needs_confirmation(home, vocab, config):
    client, calls = _scripted(
        {
            "verb:lock": NoulA(0.95),
            "area:hallway": NoulA(0.9),
            "domain:lock": NoulA(0.9),
            "flag:collective": NoulA(0.9),
            "area_primary": ChoiceA("several", 0.9, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "lock the front door")
    assert isinstance(r, NeedsConfirmation) and r.reason == "risk:confirm"
    assert [e.entity_id for e in r.actions[0].targets] == ["lock.front_door"]
    assert calls["n"] == 1


async def test_blast_radius_ends_in_needs_confirmation(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", max_silent_targets=2)
    client, _ = _scripted(
        {
            "verb:turn_off": NoulA(0.95),
            "floor:downstairs": NoulA(0.9),
            "domain:light": NoulA(0.9),
            "flag:collective": NoulA(0.95),
            "area_primary": ChoiceA("several", 0.9, {}),
        }
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn everything off")
    assert isinstance(r, NeedsConfirmation) and r.reason == "blast_radius"
    assert len(r.actions[0].targets) > 2


async def test_param_verb_resolves_with_params_populated(home, vocab, config):
    client, calls = _scripted(
        {
            "verb:set_brightness": NoulA(0.9),
            "area:office": NoulA(0.9),
            "domain:light": NoulA(0.9),
            "flag:collective": NoulA(0.1),
            "area_primary": ChoiceA("several", 0.9, {}),
        },
        {"param:set_brightness": ScoreA(3.0, 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "dim the desk lamp")
    assert isinstance(r, Resolved)
    assert [e.entity_id for e in r.actions[0].targets] == ["light.office_desk"]
    assert r.actions[0].params == {"brightness_pct": 50.0}
    assert calls["n"] == 2  # the param Choice is the only thing Round 2 is spent on


async def test_condition_rides_along_on_the_result(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:lock": NoulA(0.95),
            "domain:lock": NoulA(0.9),
            "flag:has_condition": NoulA(0.9),
            "condition_domain": ChoiceA("cover", 0.9, {"cover": 0.9}),
        },
        {
            "cond_subject": ChoiceA("Blinds", 0.9, {"Blinds": 0.9}),
            "cond_state": ChoiceA("closed", 0.9, {"closed": 0.9}),
        },
    )
    r = await Engine(client, vocab, config).decide(
        home, "if the blinds are closed lock the front door"
    )
    assert isinstance(r, NeedsConfirmation)
    assert r.condition is not None
    assert r.condition.subject.entity_id == "cover.living_blinds"
    assert r.condition.expected_state == "closed"


async def test_destructive_flag_escalates(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:turn_off": NoulA(0.9),
            "area:hallway": NoulA(0.9),
            "flag:is_destructive": NoulA(0.85),
            "area_primary": ChoiceA("several", 0.9, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "disable the smoke alarm")
    assert isinstance(r, Escalate) and r.reason == "destructive" and r.partial == ()


async def test_multi_verb_request_yields_one_action_per_verb(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:turn_off": NoulA(0.95),
            "verb:close": NoulA(0.95),
            "area:kitchen": NoulA(0.9),
            "area:living": NoulA(0.9),
            "domain:light": NoulA(0.9),
            "domain:cover": NoulA(0.9),
            "flag:collective": NoulA(0.9),
            "area_primary": ChoiceA("several", 0.9, {}),
            "outside_scope:close": NoulA(0.9),
        }
    )
    r = await Engine(client, vocab, config).decide(
        home, "turn off the kitchen lights and close the blinds"
    )
    assert isinstance(r, Resolved)
    assert {a.verb.name for a in r.actions} == {"turn_off", "close"}
    by_verb = {a.verb.name: {e.entity_id for e in a.targets} for a in r.actions}
    assert by_verb["close"] == {"cover.living_blinds"}
    assert "light.kitchen_ceiling" in by_verb["turn_off"]
    assert "switch.fridge" not in by_verb["turn_off"]


async def test_scope_clarify_for_one_verb_does_not_abort_when_another_resolves(home, vocab):
    # "Mach das Rollo in der Küche auf": `open` has one kitchen cover; a co-firing `turn_on` has
    # nothing in scope, widens past the cap and would ask "which area?" — it must just be dropped.
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2)
    client, _ = _scripted(
        {
            "verb:open": NoulA(0.93),
            "verb:turn_on": NoulA(0.73),
            "area:living": NoulA(0.99),
            "domain:cover": NoulA(0.96),
            "area_primary": ChoiceA("several", 0.9, {}),
        },
    )
    r = await Engine(client, vocab, cfg).decide(home, "open the blinds in the living room")
    assert isinstance(r, Resolved)
    assert [a.verb.name for a in r.actions] == ["open"]
    assert "dropped:turn_on:scope" in r.trace.notes


async def test_numeric_condition_hands_off_before_round2(home, vocab, config):
    # "close the blinds if it is warmer than 23 degrees": Jev says the condition compares a
    # measurement with a number; no two-slot Condition can hold that, so the engine stops here.
    client, calls = _scripted(
        {
            "verb:close": NoulA(0.95),
            "domain:cover": NoulA(0.95),
            "flag:collective": NoulA(0.9),
            "flag:has_condition": NoulA(0.95),
            "flag:condition_numeric": NoulA(0.9),
            "condition_domain": ChoiceA("climate", 0.9, {"climate": 0.9}),
        }
    )
    r = await Engine(client, vocab, config).decide(
        home, "close the blinds if it is warmer than 23 degrees"
    )
    assert isinstance(r, Escalate) and r.reason == "condition"
    assert "condition:numeric" in r.trace.notes
    assert calls["n"] == 1


async def test_state_none_of_these_means_no_condition(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:lock": NoulA(0.95),
            "domain:lock": NoulA(0.9),
            "flag:has_condition": NoulA(0.9),
            "condition_domain": ChoiceA("cover", 0.9, {"cover": 0.9}),
        },
        {
            "cond_subject": ChoiceA("Blinds", 0.9, {"Blinds": 0.9}),
            "cond_state": ChoiceA(NO_MATCH, 0.9, {NO_MATCH: 0.9}),
        },
    )
    r = await Engine(client, vocab, config).decide(home, "lock the door if the blinds are weird")
    assert isinstance(r, Escalate) and r.reason == "condition"
    assert "no_match:cond_state" in r.trace.notes


async def test_a_question_about_a_set_goes_to_the_fallback_agent(home, vocab, config):
    # "which windows are open?": Jev says collective + query; summarising is the LLM's job.
    client, calls = _scripted(
        {
            "verb:query_state": NoulA(0.95),
            "domain:cover": NoulA(0.9),
            "flag:collective": NoulA(0.8),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "which blinds are open?")
    assert isinstance(r, Escalate) and r.reason == "query_collective"
    assert "query_collective:query_state" in r.trace.notes
    assert calls["n"] == 1  # no Round 2 spent on it


async def test_clarification_carries_the_verb_and_params(home, vocab, config):
    # weak pick among real options -> clarify; the caller needs verb + params to act on the pick
    client, _ = _scripted(
        {
            "verb:set_brightness": NoulA(0.95),
            "domain:light": NoulA(0.95),
            "area:bedroom": NoulA(0.99),
            "flag:names_specific": NoulA(0.9),
            "area_primary": ChoiceA("Bedroom", 0.99, {"Bedroom": 0.99}),
        },
        {
            # options for two same-device entities: the device itself plus each entity
            "target:set_brightness": ChoiceA(
                "Bedside lamps — Bedside left",
                0.45,
                {"Bedside lamps — Bedside left": 0.45, "Bedside lamps — Bedside right": 0.4},
            ),
            "all_of:set_brightness": NoulA(0.1),
            "param_value:set_brightness": ChoiceA("50%", 0.95, {"50%": 0.95}),
            "param_relative:set_brightness": NoulA(0.05),
            "param:set_brightness": ScoreA(3.0, 0.9, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(home, "bedside lamp to 50% in the bedroom")
    assert isinstance(r, NeedsClarification)
    assert r.verb is not None and r.verb.name == "set_brightness"
    assert r.params == {"brightness_pct": 50.0}


async def test_scope_time_clarification_has_verb_but_no_params(home, vocab):
    # scope_cap=1 with two bedroom lights and no device round: strict candidates (2) exceed
    # the cap, widening can't shrink them, and with device_round off the scope chain clarifies
    # before Round 2 ever runs -> verb is known, params never had a chance to resolve.
    from hunch.config import EngineConfig

    cfg = EngineConfig(model="m", scope_cap=1, device_round=False)
    client, calls = _scripted(
        {
            "verb:turn_on": NoulA(0.95),
            "domain:light": NoulA(0.95),
            "area:bedroom": NoulA(0.99),
            "flag:names_specific": NoulA(0.9),
            "area_primary": ChoiceA("Bedroom", 0.99, {"Bedroom": 0.99}),
        }
    )
    r = await Engine(client, vocab, cfg).decide(home, "bedside lamp on")
    assert isinstance(r, NeedsClarification)
    assert r.verb is not None and r.verb.name == "turn_on"
    assert r.params == {}
    assert calls["n"] == 1  # scope-time clarification: no Round 2 spent


async def test_a_set_in_one_room_and_a_device_in_another(home, vocab, config):
    # "turn on the kitchen lights and the reading lamp in the living room"
    from hunch.round2 import ALL_IN_ROOM

    client, calls = _scripted(
        {
            "verb:turn_on": NoulA(0.97),
            "domain:light": NoulA(0.97),
            "area:kitchen": NoulA(0.98),
            "area:living": NoulA(0.98),
            "flag:collective": NoulA(0.4),
            "flag:names_specific": NoulA(0.75),
            "verb_primary": ChoiceA("turn_on", 0.98, {}),
            "area_primary": ChoiceA("several", 0.95, {}),
        },
        {
            "room_target:turn_on:kitchen": ChoiceA(ALL_IN_ROOM, 0.93, {}),
            "room_target:turn_on:living": ChoiceA("Reading lamp", 0.96, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(
        home, "turn on the kitchen lights and the reading lamp in the living room"
    )
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
        "light.reading_lamp",
    }
    assert "room_all:turn_on:kitchen" in r.trace.notes
    assert "room_pick:turn_on:living" in r.trace.notes
    assert r.confidence >= 0.9  # the room Choices carry it, not the 0.4 collective flag
    assert calls["n"] == 2


async def test_a_hesitant_room_verdict_asks_instead_of_dropping_the_room(home, vocab, config):
    from hunch.round2 import ALL_IN_ROOM, NO_MATCH

    client, _ = _scripted(
        {
            "verb:turn_on": NoulA(0.97),
            "domain:light": NoulA(0.97),
            "area:kitchen": NoulA(0.98),
            "area:living": NoulA(0.98),
            "flag:collective": NoulA(0.4),
            "flag:names_specific": NoulA(0.75),
            "verb_primary": ChoiceA("turn_on", 0.98, {}),
            "area_primary": ChoiceA("several", 0.95, {}),
        },
        {
            "room_target:turn_on:kitchen": ChoiceA(
                NO_MATCH, 0.55, {NO_MATCH: 0.55, ALL_IN_ROOM: 0.4}
            ),
            "room_target:turn_on:living": ChoiceA("Reading lamp", 0.96, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(
        home, "turn on the kitchen lights and the reading lamp in the living room"
    )
    assert isinstance(r, NeedsConfirmation)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
        "light.reading_lamp",
    }
    assert "room_unsure:turn_on:kitchen" in r.trace.notes


async def test_two_rooms_both_as_sets_go_through_the_room_choices(home, vocab, config):
    from hunch.round2 import ALL_IN_ROOM

    client, calls = _scripted(
        {
            "verb:turn_on": NoulA(0.97),
            "domain:light": NoulA(0.97),
            "area:kitchen": NoulA(0.98),
            "area:living": NoulA(0.98),
            "flag:collective": NoulA(0.8),
            "verb_primary": ChoiceA("turn_on", 0.98, {}),
            "area_primary": ChoiceA("several", 0.95, {}),
        },
        {
            "room_target:turn_on:kitchen": ChoiceA(ALL_IN_ROOM, 0.95, {}),
            "room_target:turn_on:living": ChoiceA(ALL_IN_ROOM, 0.93, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(
        home, "lights on in the kitchen and in the living room"
    )
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
        "light.living_main",
        "light.reading_lamp",
    }
    assert calls["n"] == 2


# ---- follow-ups: the sentence leans on the previous turn ----------------------------------


def _previous(home, verb, *ids, params=None):
    from hunch import PreviousTurn

    return PreviousTurn(
        "lights on in the kitchen",
        (Action(vocab_verb(verb), tuple(home.entity_by_id(i) for i in ids), params or {}),),
    )


def vocab_verb(name):
    from hunch import DEFAULT_VOCABULARY

    return DEFAULT_VOCABULARY.by_name(name)


async def test_no_previous_turn_asks_no_follow_up_question(home, vocab, config):
    client, calls_ = _scripted({"verb:turn_on": NoulA(0.9), "domain:light": NoulA(0.9)})
    seen = []
    inner = client._script

    def spy(state, qs):
        seen.append((dict(state), set(qs)))
        return inner(state, qs)

    client._script = spy
    await Engine(client, vocab, config).decide(home, "lights on")
    assert "follow_up" not in seen[0][1] and "previous" not in seen[0][0]


async def test_same_devices_new_action_reuses_the_previous_targets(home, vocab, config):
    from hunch.round1 import SAME_DEVICES

    prev = _previous(home, "turn_on", "light.kitchen_ceiling", "light.kitchen_counter")
    client, calls = _scripted(
        {
            "verb:turn_off": NoulA(0.9),
            "verb_primary": ChoiceA("turn_off", 0.95, {}),
            "area_primary": ChoiceA("none", 0.9, {}),
            "follow_up": ChoiceA(SAME_DEVICES, 0.92, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "aus", prev)
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
    }
    assert r.actions[0].verb.name == "turn_off"
    assert calls["n"] == 1  # no Round 2 needed: nothing to pick, no parameter
    assert "follow_up:same devices, new action" in r.trace.notes


async def test_same_action_other_place_carries_the_verb_and_params(home, vocab, config):
    from hunch.round1 import SAME_ACTION

    prev = _previous(
        home, "set_brightness", "light.kitchen_ceiling", params={"brightness_pct": 50.0}
    )
    client, calls = _scripted(
        {
            "area:living": NoulA(0.98),
            "flag:collective": NoulA(0.8),
            "verb_primary": ChoiceA("none", 0.9, {}),
            "area_primary": ChoiceA("Living room (lounge)", 0.95, {}),
            "follow_up": ChoiceA(SAME_ACTION, 0.9, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "und im Wohnzimmer", prev)
    assert isinstance(r, Resolved), r
    assert r.actions[0].verb.name == "set_brightness"
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.living_main",
        "light.reading_lamp",
    }
    assert r.actions[0].params == {"brightness_pct": 50.0}
    assert "follow_up:verb_carried" in r.trace.notes
    assert "param:carried:set_brightness" in r.trace.notes


async def test_add_devices_unions_with_the_previous_targets(home, vocab, config):
    from hunch.round1 import ADD_DEVICES

    prev = _previous(home, "turn_on", "light.kitchen_ceiling", "light.kitchen_counter")
    client, _ = _scripted(
        {
            "area:living": NoulA(0.98),
            "flag:names_specific": NoulA(0.9),
            "verb_primary": ChoiceA("none", 0.9, {}),
            "area_primary": ChoiceA("Living room (lounge)", 0.95, {}),
            "follow_up": ChoiceA(ADD_DEVICES, 0.9, {}),
        },
        {"target:turn_on": ChoiceA("Reading lamp", 0.95, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "die Leselampe auch", prev)
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.reading_lamp",
        "light.kitchen_ceiling",
        "light.kitchen_counter",
    }
    assert "follow_up:added_previous_targets" in r.trace.notes


async def test_more_about_the_same_replays_the_previous_action(home, vocab, config):
    from hunch.round1 import MORE_SAME

    prev = _previous(home, "query_state", "lock.front_door")
    client, calls = _scripted(
        {
            "verb_primary": ChoiceA("none", 0.9, {}),
            "area_primary": ChoiceA("none", 0.9, {}),
            "follow_up": ChoiceA(MORE_SAME, 0.9, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "was genau", prev)
    assert isinstance(r, Resolved)
    assert r.actions == prev.actions and calls["n"] == 1
    assert "follow_up:replay" in r.trace.notes


async def test_a_hesitant_follow_up_verdict_is_a_new_request(home, vocab, config):
    from hunch.round1 import SAME_DEVICES

    prev = _previous(home, "turn_on", "light.kitchen_ceiling")
    client, _ = _scripted(
        {
            "verb_primary": ChoiceA("none", 0.9, {}),
            "area_primary": ChoiceA("none", 0.9, {}),
            "follow_up": ChoiceA(SAME_DEVICES, 0.5, {}),
        }
    )
    r = await Engine(client, vocab, config).decide(home, "hm", prev)
    assert isinstance(r, Escalate) and r.reason == "no_intent"  # today's behaviour, unchanged


async def test_a_device_named_after_a_room_does_not_scope_to_that_room(vocab, config):
    # 'close the terrace blind': two blinds are called 'Terrace blind' (bedroom, gallery) and a
    # room 'Terrace' exists without blinds. Jev's room pick would strand the request; the word
    # belongs to the device, so the two twins are offered instead.
    from hunch.model import Area, Entity, Floor, HomeModel

    def e(eid, name, area, verbs):
        return Entity(eid, eid.split(".")[0], name, (), area, None, name, verbs, None)

    home = HomeModel(
        floors=(Floor("up", "Upstairs", ("bedroom", "gallery", "terrace")),),
        areas=(
            Area("bedroom", "Bedroom", (), "up"),
            Area("gallery", "Gallery", (), "up"),
            Area("terrace", "Terrace", (), "up"),
        ),
        entities=(
            e("cover.b", "Terrace blind", "bedroom", frozenset({"close", "open"})),
            e("cover.g", "Terrace blind", "gallery", frozenset({"close", "open"})),
            e("light.t", "Terrace light", "terrace", frozenset({"turn_on", "turn_off"})),
        ),
        scenes=(),
    )
    client, _ = _scripted(
        {
            "verb:close": NoulA(0.97),
            "domain:cover": NoulA(0.97),
            "area:terrace": NoulA(0.9),
            "flag:names_specific": NoulA(0.8),
            "verb_primary": ChoiceA("close", 0.98, {}),
            "area_primary": ChoiceA("Terrace", 0.96, {}),
        },
        {
            "target:close": ChoiceA("Terrace blind (Gallery)", 0.6, {}),
            "all_of:close": NoulA(0.1),
            "outside_scope:close": NoulA(0.4),
        },
    )
    r = await Engine(client, vocab, config).decide(home, "close the terrace blind")
    assert "area_shadowed:terrace" in r.trace.notes
    assert isinstance(r, NeedsClarification)
    assert {c.entity_id for c in r.candidates} == {"cover.b", "cover.g"}
