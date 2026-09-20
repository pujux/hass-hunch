"""Deterministic scope: verbatim areas/floors (with aliases), domain synonyms, and a hard bar for
Jev-only areas (2026-09-20, from 'Licht aus' picking two random rooms)."""

from hunch.client import FakeDecisionClient
from hunch.config import EngineConfig
from hunch.engine import Engine
from hunch.loaders import home_from_export
from hunch.questions import ChoiceA, ChoiceQ, NoulA, ScoreA, ScoreQ
from hunch.resolution import Resolved
from hunch.round2 import NO_MATCH
from hunch.scope import verbatim_areas


def test_floor_aliases_survive_the_export():
    export = {
        "floors": [
            {
                "floor_id": "ug",
                "name": "Untergeschoss",
                "aliases": ["unten", "Erdgeschoss"],
                "level": 0,
            }
        ],
        "areas": [{"area_id": "k", "name": "Küche", "aliases": [], "floor_id": "ug"}],
        "devices": [],
        "entities": [],
        "exposed": [],
        "states": {},
    }
    home = home_from_export(export)
    assert home.floors[0].aliases == ("unten", "Erdgeschoss")
    assert verbatim_areas(home, "Licht unten aus") == ("k",)
    assert verbatim_areas(home, "Licht im Untergeschoss an") == ("k",)


def _script(overrides):
    def script(state, qs):
        out = {}
        for qid, q in qs.items():
            if qid in overrides:
                out[qid] = overrides[qid]
            elif qid in ("verb_primary", "area_primary"):
                out[qid] = ChoiceA("several", 0.9, {})
            elif isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
            elif isinstance(q, ScoreQ):
                out[qid] = ScoreA(1.0, 0.9, {})
            else:
                out[qid] = NoulA(0.05)
        return out

    return script


async def test_soft_jev_areas_are_dropped_without_verbatim_support(home, vocab, config):
    # "lights off": Jev floats the bedroom and the office, but also says no place was named
    # (names_place stays low) -> no area scope -> whole light domain; Jev then says "all of these".
    ov = {
        "verb:turn_off": NoulA(0.9),
        "domain:light": NoulA(0.97),
        "area:bedroom": NoulA(0.75),
        "area:office": NoulA(0.72),
        "flag:collective": NoulA(0.35),
        "area_primary": ChoiceA("none", 0.7, {}),
        "target:turn_off": ChoiceA(NO_MATCH, 0.45, {}),
        "all_of:turn_off": NoulA(0.85),
    }
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(home, "lights off")
    assert isinstance(r, Resolved) and len(r.actions[0].targets) == 9
    assert "all_of:turn_off" in r.trace.notes
    assert any(n.startswith("areas_dropped:no_place") for n in r.trace.notes)


async def test_jev_areas_count_when_the_comparison_keeps_them(home, vocab, config):
    ov = {
        "verb:turn_on": NoulA(0.95),
        "domain:light": NoulA(0.97),
        "area:bedroom": NoulA(0.96),
        "flag:collective": NoulA(0.9),
        "area_primary": ChoiceA("several", 0.9, {}),
    }
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(
        home, "lights on in the sleeping room"
    )
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.bedroom_left",
        "light.bedroom_right",
    }


def test_domain_question_carries_the_words_people_use(home, vocab):
    from hunch.round1 import build_round1_questions

    q = build_round1_questions(home, vocab)["domain:light"].instructions.casefold()
    assert "lights" in q and "licht" in q and "lampen" in q
    q = build_round1_questions(home, vocab)["domain:cover"].instructions.casefold()
    assert "blinds" in q and "rollos" in q


async def test_a_named_area_beats_a_fired_floor(home, vocab, config):
    # "Rollos im Schlafzimmer runter" with the upstairs floor also firing: only the bedroom.
    ov = {
        "verb:close": NoulA(0.92),
        "domain:cover": NoulA(0.97),
        "area:bedroom": NoulA(0.99),
        "floor:upstairs": NoulA(0.72),
        "flag:collective": NoulA(0.9),
        "area_primary": ChoiceA("several", 0.9, {}),
    }
    home2 = home
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(
        home2, "close the blinds in the bedroom"
    )
    # the fixture has no bedroom covers, so the verb is dropped -> but the scope must be the
    # bedroom only
    assert "areas_dropped:named:office" in r.trace.notes


async def test_whole_home_flag_lifts_the_area_scope(home, vocab, config):
    # "Mach alles aus": Jev floats every room AND says the whole home is meant.
    ov = {
        "verb:turn_off": NoulA(0.9),
        "domain:light": NoulA(0.95),
        "flag:collective": NoulA(0.92),
        "area_primary": ChoiceA("whole home", 0.9, {}),
        "area:kitchen": NoulA(0.85),
        "area:living": NoulA(0.88),
        "area:hallway": NoulA(0.8),
        "area:bedroom": NoulA(0.82),
        "area:office": NoulA(0.78),
    }
    cfg = EngineConfig(model="m", max_silent_targets=100)
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, cfg).decide(home, "everything off")
    assert isinstance(r, Resolved) and len(r.actions[0].targets) == 9
    assert "whole_home" in r.trace.notes


async def test_widened_verb_is_dropped_when_jev_says_it_was_not_meant(home, vocab, config):
    # "lamps in the bedroom up": `open` co-fires on "up", finds no bedroom cover, widens.
    ov = {
        "verb:turn_on": NoulA(0.95),
        "verb:open": NoulA(0.75),
        "area:bedroom": NoulA(0.99),
        "domain:light": NoulA(0.9),
        "flag:collective": NoulA(0.9),
        "verb_primary": ChoiceA("several", 0.4, {}),  # hesitant: the Noul set stands, gate is asked
        "outside_scope:open": NoulA(0.1),
    }
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(
        home, "lamps in the bedroom up"
    )
    assert isinstance(r, Resolved)
    assert [a.verb.name for a in r.actions] == ["turn_on"]
    assert "dropped:open:outside_scope" in r.trace.notes


async def test_out_of_room_verb_is_kept_when_jev_says_it_was_meant(home, vocab, config):
    # "turn off the kitchen lights and close the blinds": the blinds are in the living room.
    ov = {
        "verb:turn_off": NoulA(0.95),
        "verb:close": NoulA(0.9),
        "area:kitchen": NoulA(0.99),
        "domain:light": NoulA(0.9),
        "domain:cover": NoulA(0.9),
        "flag:collective": NoulA(0.9),
        "outside_scope:close": NoulA(0.9),
    }
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(
        home, "turn off the kitchen lights and close the blinds"
    )
    assert isinstance(r, Resolved)
    assert sorted(a.verb.name for a in r.actions) == ["close", "turn_off"]


async def test_second_action_is_not_second_guessed_when_jev_said_several(home, vocab, config):
    # "turn off the kitchen lights and close the blinds": the comparison says several actions,
    # so the blinds (outside the kitchen) are not re-asked with the out-of-room Noul.
    ov = {
        "verb:turn_off": NoulA(0.95),
        "verb:close": NoulA(0.9),
        "area:kitchen": NoulA(0.99),
        "domain:light": NoulA(0.9),
        "domain:cover": NoulA(0.9),
        "flag:collective": NoulA(0.9),
        "verb_primary": ChoiceA("several", 1.0, {}),
        "area_primary": ChoiceA("Kitchen", 0.8, {}),
        "outside_scope:close": NoulA(0.2),
    }  # would have dropped it — must not be asked
    client = FakeDecisionClient(_script(ov))
    r = await Engine(client, vocab, config).decide(
        home, "turn off the kitchen lights and close the blinds"
    )
    assert isinstance(r, Resolved)
    assert sorted(a.verb.name for a in r.actions) == ["close", "turn_off"]
    assert not any("outside_scope" in qid for _, qs in client.calls for qid in qs)
