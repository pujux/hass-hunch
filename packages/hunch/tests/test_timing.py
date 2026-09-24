from hunch.phrasing import DE, EN
from hunch.questions import Answers, ChoiceA
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
    HOURS,
    MINUTES,
    NO_LABEL,
    NO_MATCH,
    NOT_DURATION,
    SECONDS,
    TIMING_KIND_OPTIONS,
    UNIT_OPTIONS,
    duration_literals,
    label_candidates,
    sum_duration,
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


def test_fraction_with_an_article_before_its_unit_is_one_literal():
    lits = duration_literals("Set a timer for half an hour")
    assert [(lit.text, lit.value, lit.unit) for lit in lits] == [("half an hour", 0.5, "hours")]
    lits = duration_literals("Set a timer for a quarter of an hour")
    assert [(lit.value, lit.unit) for lit in lits] == [(0.25, "hours")]
    # a bare fraction after an article is no number code can place: 1 h, never a wrong 1.5 h
    assert _texts("turn it off in an hour and a half") == ["an hour"]
    assert _texts("Timer eine halbe Stunde") == ["halbe Stunde"]


def test_tens_to_ninety_and_n_einhalb():
    assert _values("Timer siebzig Minuten") == [70.0]
    assert _values("Timer fünfundachtzig Sekunden") == [85.0]
    assert _values("Timer neunzig Sekunden") == [90.0]
    assert _values("timer for seventy-five minutes") == [75.0]
    assert _values("timer for ninety seconds") == [90.0]
    assert _values("Timer dreieinhalb Minuten") == [3.5]
    assert _values("zweieinhalb Stunden") == [2.5]
    assert _values("eineinhalb Stunden") == [1.5]


def test_articles_count_only_with_a_unit():
    assert _texts("Stell einen Timer für die Nudeln") == []
    assert _texts("Timer eine Stunde") == ["eine Stunde"] and _values("Timer eine Stunde") == [1.0]
    assert _texts("turn it off in an hour") == ["an hour"]
    assert _texts("set a timer for the pasta") == []


def test_spoken_units_are_looked_up_bare_numbers_are_not():
    lits = duration_literals("Timer 1 Stunde 20")
    assert [lit.unit for lit in lits] == [HOURS, None]
    assert duration_literals("Eine Viertelstunde Timer")[0].unit == HOURS
    assert duration_literals("Timer eine halbe Stunde")[0].unit == HOURS
    assert duration_literals("Timer 8 Minuten")[0].unit == MINUTES
    assert duration_literals("Timer 90 Sekunden")[0].unit == SECONDS
    assert (
        duration_literals("in 2 h")[0].unit == HOURS
        and duration_literals("15min")[0].unit == MINUTES
    )
    assert duration_literals("Rollo auf 20%")[0].unit is None


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
    # case-folded dedupe, first spelling wins
    prompt = "Timer Nudeln nudeln 8"
    assert label_candidates(prompt, duration_literals(prompt)) == ("Nudeln",)


def _units(*choices):
    """Round 2 answers for duration:0, duration:1, … (a choice or (choice, conf, probs))."""
    out = {}
    for i, c in enumerate(choices):
        choice, conf, probs = c if isinstance(c, tuple) else (c, 0.9, {})
        out[f"duration:{i}"] = ChoiceA(choice, conf, probs)
    return Answers(model="fake", answers=out, input_tokens=None)


def test_sum_duration_multiplies_spoken_or_chosen_units_and_adds():
    trace = Trace()
    lits = duration_literals("Timer 1 Stunde 20")  # spoken hours, bare 20
    seconds, confs = sum_duration(_units(MINUTES, (MINUTES, 0.8, {})), lits, trace)
    # the spoken "Stunde" wins over Jev's "minutes"; the bare 20 takes Jev's unit
    assert seconds == 4800 and "timing:seconds:4800" in trace.notes
    # no probabilities reported: each literal counts with Jev's confidence
    assert confs == [0.9, 0.8]
    trace = Trace()
    lits = duration_literals("Rollo auf 20% für 10 Minuten")
    seconds, _ = sum_duration(_units(NOT_DURATION, MINUTES), lits, trace)
    assert seconds == 600  # the 20 (a percentage) is not a duration and adds nothing
    lits = duration_literals("Timer 90")
    assert sum_duration(_units(SECONDS), lits, Trace())[0] == 90
    assert sum_duration(_units(HOURS), duration_literals("Timer 2"), Trace())[0] == 7200


def test_sum_duration_spoken_unit_confidence_is_mass_on_the_units():
    lits = duration_literals("Timer eine halbe Stunde")
    probs = {MINUTES: 0.5, HOURS: 0.3, NOT_DURATION: 0.2}
    seconds, confs = sum_duration(_units((MINUTES, 0.3, probs)), lits, Trace())
    assert seconds == 1800 and confs == [0.8]


def test_sum_duration_hands_off_nothing_and_out_of_bounds():
    trace = Trace()
    assert sum_duration(None, duration_literals("Timer 8"), trace) == (None, [])
    trace = Trace()
    seconds, confs = sum_duration(_units(NOT_DURATION), duration_literals("Timer 15"), trace)
    assert seconds is None and confs == [0.9] and "timing:no_duration" in trace.notes
    trace = Trace()
    lits = duration_literals("Timer 3 Sekunden")
    assert sum_duration(_units(SECONDS), lits, trace)[0] is None
    assert "timing:out_of_bounds" in trace.notes
    trace = Trace()
    lits = duration_literals("Timer 25 Stunden")
    assert sum_duration(_units(HOURS), lits, trace)[0] is None
    assert "timing:out_of_bounds" in trace.notes
    # the bounds themselves are allowed: 5 s and 24 h
    assert sum_duration(_units(SECONDS), duration_literals("Timer 5 Sekunden"), Trace())[0] == 5
    lits = duration_literals("Timer 24 Stunden")
    assert sum_duration(_units(HOURS), lits, Trace())[0] == 24 * 3600


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
