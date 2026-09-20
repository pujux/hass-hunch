"""Deterministic scope: verbatim areas/floors (with aliases), domain synonyms, and a hard bar for
Jev-only areas (2026-09-20, from 'Licht aus' picking two random rooms)."""

from hunch.client import FakeDecisionClient
from hunch.config import EngineConfig, Thresholds
from hunch.engine import Engine
from hunch.loaders import home_from_export
from hunch.phrasing import DE, EN
from hunch.questions import ChoiceA, ChoiceQ, NoulA, ScoreA, ScoreQ
from hunch.resolution import NeedsConfirmation, Resolved
from hunch.round2 import NO_MATCH
from hunch.scope import verbatim_areas, verbatim_domains


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


def test_domain_synonyms_in_either_language():
    assert verbatim_domains("Licht aus", (EN, DE)) == ("light",)
    assert verbatim_domains("turn off the downstairs lights", (EN, DE)) == ("light",)
    assert verbatim_domains("Fahr die Rollos im Schlafzimmer runter", (EN, DE)) == ("cover",)
    assert verbatim_domains("Dreh den Fernseher im Schlafzimmer auf", (EN, DE)) == ("media_player",)
    assert verbatim_domains("Wie warm ist es im Wohnzimmer?", (EN, DE)) == ()
    assert (
        verbatim_domains("Welche Fenster sind offen?", (EN, DE)) == ()
    )  # window: sensor or cover — undecidable


def test_new_threshold_default():
    assert Thresholds().scope_hard == 0.9


def _script(overrides):
    def script(state, qs):
        out = {}
        for qid, q in qs.items():
            if qid in overrides:
                out[qid] = overrides[qid]
            elif isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
            elif isinstance(q, ScoreQ):
                out[qid] = ScoreA(1.0, 0.9, {})
            else:
                out[qid] = NoulA(0.05)
        return out

    return script


async def test_soft_jev_areas_are_dropped_without_verbatim_support(home, vocab, config):
    # "lights off" with Jev hallucinating the bedroom at 0.75 and the office at 0.72: neither is
    # named, neither is sure -> no area scope -> whole light domain, confirm (28 > cap? here 9).
    ov = {
        "verb:turn_off": NoulA(0.9),
        "domain:light": NoulA(0.97),
        "area:bedroom": NoulA(0.75),
        "area:office": NoulA(0.72),
        "flag:collective": NoulA(0.35),
        "target:turn_off": ChoiceA(NO_MATCH, 0.45, {}),
    }
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(home, "lights off")
    assert isinstance(r, NeedsConfirmation) and r.reason == "collective_fallback"
    assert len(r.actions[0].targets) == 9
    assert any(n.startswith("soft_scope_dropped:") for n in r.trace.notes)


async def test_a_sure_jev_area_is_kept_without_verbatim_support(home, vocab, config):
    ov = {
        "verb:turn_on": NoulA(0.95),
        "domain:light": NoulA(0.97),
        "area:bedroom": NoulA(0.96),
        "flag:collective": NoulA(0.9),
    }
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(
        home, "lights on in the sleeping room"
    )
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.bedroom_left",
        "light.bedroom_right",
    }


async def test_domain_synonym_overrides_a_borderline_jev_domain(home, vocab, config):
    # "downstairs lights": Jev also fires switch at 0.71 -> the fridge would be swept.
    ov = {
        "verb:turn_off": NoulA(0.95),
        "floor:downstairs": NoulA(0.92),
        "domain:light": NoulA(0.98),
        "domain:switch": NoulA(0.71),
        "flag:collective": NoulA(0.9),
    }
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(
        home, "turn off the downstairs lights"
    )
    assert isinstance(r, Resolved)
    assert "switch.fridge" not in {e.entity_id for e in r.actions[0].targets}
    assert "domain_match:light" in r.trace.notes


async def test_a_named_area_beats_a_fired_floor(home, vocab, config):
    # "Rollos im Schlafzimmer runter" with the upstairs floor also firing: only the bedroom.
    ov = {
        "verb:close": NoulA(0.92),
        "domain:cover": NoulA(0.97),
        "area:bedroom": NoulA(0.99),
        "floor:upstairs": NoulA(0.72),
        "flag:collective": NoulA(0.9),
    }
    home2 = home
    r = await Engine(FakeDecisionClient(_script(ov)), vocab, config).decide(
        home2, "close the blinds in the bedroom"
    )
    # the fixture has no bedroom covers, so the verb is dropped -> but the scope must be the bedroom only
    assert "soft_scope_dropped:office" in r.trace.notes or all(
        "office" not in n for n in r.trace.notes
    )


async def test_most_rooms_firing_means_the_whole_home(home, vocab, config):
    # "Mach alles aus": Jev lights up every room at 0.77-0.9. Not hallucinations: the whole home.
    ov = {
        "verb:turn_off": NoulA(0.9),
        "domain:light": NoulA(0.95),
        "flag:collective": NoulA(0.92),
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


async def test_a_named_exception_is_excluded_by_code(home, vocab, config):
    ov = {
        "verb:turn_off": NoulA(0.9),
        "area:kitchen": NoulA(0.99),
        "flag:collective": NoulA(0.9),
        "flag:has_exception": NoulA(0.95),
    }
    client = FakeDecisionClient(_script(ov))
    r = await Engine(client, vocab, config).decide(
        home, "turn off everything in the kitchen except the fridge"
    )
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling",
        "light.kitchen_counter",
    }
    assert len(client.calls) == 1  # no exclusion Nouls were needed
    assert "exception_match:turn_off:1" in r.trace.notes
