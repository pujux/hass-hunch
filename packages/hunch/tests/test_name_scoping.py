"""Deterministic name matching before Jev judges (2026-09-20, from 'Badezimmer Rollos auf 15%',
'Ist die Dachterrassentür offen?' and 'Licht aus')."""

from hunch.config import EngineConfig
from hunch.model import Area, Entity, Floor, HomeModel
from hunch.questions import Answers, ChoiceA
from hunch.resolution import NeedsConfirmation, Trace
from hunch.resolver import resolve
from hunch.round1 import FLAGS, Shape
from hunch.round2 import NO_MATCH, plan_round2
from hunch.scope import Candidates, scope_candidates, verbatim_areas, verbatim_matches
from hunch.vocabulary import DEFAULT_VOCABULARY as V

COVER = frozenset({"open", "close", "set_position", "query_state"})
BIN = frozenset({"query_state"})


def _e(eid, name, area, device=None, verbs=COVER):
    return Entity(
        eid,
        eid.split(".")[0],
        name,
        (),
        area,
        f"dev_{device}" if device else None,
        device,
        verbs,
        "open",
    )


def _german_home():
    return HomeModel(
        floors=(
            Floor("og", "Obergeschoss", ("bad_oben", "schlafzimmer", "galerie")),
            Floor("ug", "Untergeschoss", ("bad_unten", "kueche")),
        ),
        areas=(
            Area("bad_oben", "Badezimmer Oben", (), "og"),
            Area("bad_unten", "Badezimmer Unten", (), "ug"),
            Area("schlafzimmer", "Schlafzimmer", (), "og"),
            Area("galerie", "Galerie", (), "og"),
            Area("kueche", "Küche", (), "ug"),
            Area("virtuell", "Virtuell", (), None),
        ),
        entities=(
            _e("cover.bo_links", "Fenster Rollo Links", "bad_oben"),
            _e("cover.bo_mitte", "Fenster Rollo Mitte", "bad_oben"),
            _e("cover.bo_rechts", "Fenster Rollo Rechts", "bad_oben"),
            _e("cover.kueche", "Fenster Rollo", "kueche"),
            _e("cover.virtuell_rollos", "Rollos", "virtuell"),
            _e("binary_sensor.sz_tuer", "Tür", "schlafzimmer", "Dachterrassentür", BIN),
            _e("binary_sensor.ga_tuer", "Tür", "galerie", "Dachterrassentür", BIN),
            _e("binary_sensor.kueche_fenster", "Fenster", "kueche", None, BIN),
        ),
        scenes=(),
    )


def _shape(verbs, areas=(), domains=(), flags=None):
    f = {k: 0.05 for k in FLAGS}
    f.update(flags or {})
    return Shape(
        tuple(V.by_name(v) for v in verbs), tuple(areas), tuple(domains), {}, {}, f, None, None
    )


def test_verbatim_areas_matches_full_names_and_shared_stems():
    home = _german_home()
    assert verbatim_areas(home, "Badezimmer Rollos auf 15%") == ("bad_oben", "bad_unten")
    assert verbatim_areas(home, "Rollo in der Küche auf") == ("kueche",)
    assert verbatim_areas(home, "Licht aus") == ()
    assert verbatim_areas(home, "Ist die Dachterrassentür offen?") == ()


def test_verbatim_matches_include_device_names():
    home = _german_home()
    hits = verbatim_matches(home.entities, "Ist die Dachterrassentür offen?")
    assert {e.entity_id for e in hits} == {"binary_sensor.sz_tuer", "binary_sensor.ga_tuer"}
    assert verbatim_matches(home.entities, "Licht aus") == ()


def test_name_filter_narrows_inside_scope_before_the_cap():
    home = _german_home()
    cfg = EngineConfig(model="m", scope_cap=3)
    shape = _shape(["query_state"], domains=("binary_sensor", "cover"))
    r = scope_candidates(
        home,
        V.by_name("query_state"),
        shape,
        cfg,
        Trace(),
        prompt="Ist die Dachterrassentür offen?",
    )
    assert isinstance(r, Candidates)
    assert {e.entity_id for e in r.entities} == {"binary_sensor.sz_tuer", "binary_sensor.ga_tuer"}


def test_generic_group_name_does_not_hijack_an_area_scoped_request():
    home = _german_home()
    shape = _shape(["set_position"], areas=("bad_oben", "bad_unten"), domains=("cover",))
    r = scope_candidates(
        home,
        V.by_name("set_position"),
        shape,
        EngineConfig(model="m"),
        Trace(),
        prompt="Badezimmer Rollos auf 15%",
    )
    assert {e.entity_id for e in r.entities} == {
        "cover.bo_links",
        "cover.bo_mitte",
        "cover.bo_rechts",
    }


def test_no_room_no_name_no_match_confirms_the_whole_domain(home, config):
    # "Licht aus": nothing named, nothing scoped, Jev finds no single target -> all lights, ask.
    shape = _shape(["turn_off"], domains=("light",), flags={"collective": 0.37})
    cands = tuple(e for e in home.entities if e.domain == "light")
    t = Trace()
    t.decide("verb:turn_off", 0.9, 0.7)
    plan = plan_round2(home, shape, {"turn_off": cands}, config.thresholds, 60, prompt="Licht aus")
    assert plan.domain_sweep_ok == ("turn_off",)
    r2 = Answers("m", {"target:turn_off": ChoiceA(NO_MATCH, 0.36, {NO_MATCH: 0.36})}, None)
    r = resolve(shape, plan, r2, config, t)
    assert isinstance(r, NeedsConfirmation) and r.reason == "collective_fallback"
    assert len(r.actions[0].targets) == len(cands)


def test_named_but_unknown_device_never_sweeps_the_room(home, config):
    # "Wohnzimmer Stehlampe aufdrehen" where the Stehlampe is not exposed: the room is named,
    # no candidate matches, Jev says a device was named -> ask/escalate, never turn on the room.
    from hunch.resolution import NeedsClarification

    shape = _shape(
        ["turn_on"], areas=("living",), domains=("light",), flags={"names_specific": 0.8}
    )
    t = Trace()
    t.decide("verb:turn_on", 0.97, 0.7)
    cands = tuple(e for e in home.entities if e.area_id == "living" and e.domain == "light")
    plan = plan_round2(
        home,
        shape,
        {"turn_on": cands},
        config.thresholds,
        60,
        prompt="Wohnzimmer Stehlampe aufdrehen",
    )
    assert plan.scoped_sweep_ok == ("turn_on",)  # the planner cannot know; the resolver guards it
    r2 = Answers(
        "m",
        {
            "target:turn_on": ChoiceA(
                "Living room main", 0.34, {"Living room main": 0.34, "Reading lamp": 0.3}
            )
        },
        None,
    )
    r = resolve(shape, plan, r2, config, t)
    assert isinstance(r, NeedsClarification)
    assert (
        "unknown_device:turn_on" not in r.trace.notes
    )  # weak pick path; clarify with the candidates
