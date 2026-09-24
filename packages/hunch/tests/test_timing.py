from hunch.phrasing import DE, EN
from hunch.resolution import (
    ActiveTimer,
    NeedsClarification,
    NeedsConfirmation,
    Resolved,
    TimerCommand,
    Timing,
    Trace,
)
from hunch.round2 import NO_MATCH as ROUND2_NO_MATCH
from hunch.timing import (
    ALL_TIMERS,
    NO_LABEL,
    NO_MATCH,
    TIMING_KIND_OPTIONS,
    UNIT_OPTIONS,
    duration_literals,
    label_candidates,
    timer_option,
    timer_options,
)
from hunch.vocabulary import DEFAULT_VOCABULARY, INVERSES


def test_no_match_sentinel_matches_round2():
    assert NO_MATCH == ROUND2_NO_MATCH


def _texts(prompt):
    return [lit.text for lit in duration_literals(prompt)]


def _values(prompt):
    return [lit.value for lit in duration_literals(prompt)]


def test_digits_with_and_without_unit_are_literals():
    assert _texts("Timer 8 Minuten") == ["8 Minuten"]
    assert _values("Timer 8 Minuten") == [8.0]
    assert _texts("Timer 8") == ["8"]
    assert _texts("Wandlampe an für 15min") == ["15min"]
    assert _texts("Rollo auf 20% für 10 Minuten") == ["20", "10 Minuten"]


def test_number_words_and_compounds():
    assert _values("Mach das Licht in zehn Minuten aus") == [10.0]
    assert _texts("Mach das Licht in zehn Minuten aus") == ["zehn Minuten"]
    assert _values("Timer fünfundzwanzig Minuten") == [25.0]
    assert _values("timer for twenty-five minutes") == [25.0]
    assert _values("Timer eine halbe Stunde") == [0.5]
    assert _texts("Timer eine halbe Stunde") == ["halbe Stunde"]
    assert _values("Eine Viertelstunde Timer") == [0.25]
    assert _texts("Eine Viertelstunde Timer") == ["Viertelstunde"]
    assert _values("anderthalb Stunden") == [1.5]


def test_articles_count_only_with_a_unit():
    assert _texts("Stell einen Timer für die Nudeln") == []
    assert _texts("Timer eine Stunde") == ["eine Stunde"] and _values("Timer eine Stunde") == [1.0]
    assert _texts("turn it off in an hour") == ["an hour"]
    assert _texts("set a timer for the pasta") == []


def test_two_literals_for_hour_and_minutes():
    assert _texts("Timer 1 Stunde 20") == ["1 Stunde", "20"]


def test_words_inside_other_words_are_not_literals():
    assert _texts("Licht einschalten") == []
    assert _texts("Spots an") == []


def test_label_candidates_drop_timer_words_numbers_units_articles_and_literal_parts():
    prompt = "Stell einen Timer für die Nudeln auf 8 Minuten"
    assert label_candidates(prompt, duration_literals(prompt)) == ("für", "die", "Nudeln", "auf")
    lits = duration_literals("Timer eine halbe Stunde")
    assert label_candidates("Timer eine halbe Stunde", lits) == ()
    assert label_candidates("Timer Nudeln 8", duration_literals("Timer Nudeln 8")) == ("Nudeln",)


def test_timer_option_shows_name_and_remaining_and_dedupes():
    t = ActiveTimer("a", "Nudeln", 200.0, "timer")
    assert timer_option(t) == "Nudeln (3:20 left)"
    t2 = ActiveTimer("b", None, 59.4, "delayed", "Wandlampe aus")
    assert timer_option(t2) == "Wandlampe aus (0:59 left)"
    t3 = ActiveTimer("c", None, 480.0, "timer")
    assert timer_option(t3) == "timer (8:00 left)"
    twin = ActiveTimer("d", "Nudeln", 200.0, "timer")
    assert timer_options((t, twin, t3)) == (
        "Nudeln (3:20 left)",
        "Nudeln (3:20 left) #2",
        "timer (8:00 left)",
    )


def test_inverses_only_for_reversible_verbs_and_never_unlock_a_door_later():
    for a, b in INVERSES.items():
        assert DEFAULT_VOCABULARY.by_name(a) and DEFAULT_VOCABULARY.by_name(b)
    assert "set_brightness" not in INVERSES and "activate" not in INVERSES
    assert INVERSES["turn_on"] == "turn_off" and INVERSES["close"] == "open"
    assert INVERSES["unlock"] == "lock" and "lock" not in INVERSES


def test_timing_no_match_is_round2_no_match():
    from hunch.round2 import NO_MATCH as R2
    from hunch.timing import NO_MATCH

    assert NO_MATCH == R2


def test_phrasebooks_describe_every_option():
    for pb in (EN, DE):
        assert set(pb.timing_kind_descriptions) == set(TIMING_KIND_OPTIONS)
        assert set(pb.duration_descriptions) == set(UNIT_OPTIONS)
        for key in ("no_label", "all_timers", "no_timer_match"):
            assert pb.special_descriptions[key]
        assert "{literal}" in pb.duration_question
    assert NO_LABEL == "no label" and ALL_TIMERS == "all timers"


def test_result_types_default_the_new_fields():
    r = Resolved((), None, 0.9, Trace())
    assert r.timing is None and r.timer is None
    c = NeedsConfirmation((), None, "confidence", Trace())
    assert c.timing is None
    n = NeedsClarification("which_timer", (), Trace(), timers=(ActiveTimer("a", None, 1, "timer"),))
    assert n.timer_kind is None and n.timing is None and n.verb is None
    assert TimerCommand("cancel").timers == () and Timing("delayed", 60).seconds == 60
