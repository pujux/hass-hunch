"""Timers and timing: the part code can look up (numbers, units, inverses, active timers) and
the timer-only Round 2. Whether a number is a duration, in which unit, what a timer is for and
which running timer is meant are Jev's judgments."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hunch.config import EngineConfig
from hunch.phrasing import EN, Phrasebook
from hunch.questions import JSON, Answers, ChoiceQ, Question
from hunch.resolution import (
    ActiveTimer,
    Escalate,
    NeedsClarification,
    Resolution,
    Resolved,
    TimerCommand,
    Trace,
)

__all__ = [
    "NO_MATCH",
    "TIMING_NONE",
    "TIMER_START",
    "TIMER_CANCEL",
    "TIMER_REMAINING",
    "FOR_DURATION",
    "DELAYED",
    "CLOCK_TIME",
    "TIMING_OTHER",
    "TIMING_KIND_OPTIONS",
    "TIMER_KINDS",
    "DEVICE_TIMING_KINDS",
    "TIMING_KIND_TO_TIMING",
    "TIMER_COMMANDS",
    "SECONDS",
    "MINUTES",
    "HOURS",
    "NOT_DURATION",
    "UNIT_OPTIONS",
    "UNIT_FACTORS",
    "NO_LABEL",
    "ALL_TIMERS",
    "MIN_SECONDS",
    "MAX_SECONDS",
    "DurationLiteral",
    "NUMBER_WORDS",
    "TIMER_WORDS",
    "duration_literals",
    "label_candidates",
    "timer_option",
    "timer_options",
    "duration_questions",
    "timer_questions",
    "timer_state",
    "sum_duration",
    "resolve_timer",
]

# Round 1 `timing_kind` options (language-neutral ids; wording in the phrasebooks)
TIMING_NONE = "none"
TIMER_START = "start a timer"
TIMER_CANCEL = "cancel a timer"
TIMER_REMAINING = "ask how long a timer has left"
FOR_DURATION = "device action for a duration"
DELAYED = "device action after a delay"
CLOCK_TIME = "at a clock time or date"
TIMING_OTHER = "other timing"
TIMING_KIND_OPTIONS = (
    TIMING_NONE,
    TIMER_START,
    TIMER_CANCEL,
    TIMER_REMAINING,
    FOR_DURATION,
    DELAYED,
    CLOCK_TIME,
    TIMING_OTHER,
)
TIMER_KINDS = frozenset({TIMER_START, TIMER_CANCEL, TIMER_REMAINING})
DEVICE_TIMING_KINDS = frozenset({FOR_DURATION, DELAYED})
TIMING_KIND_TO_TIMING = {FOR_DURATION: "for_duration", DELAYED: "delayed"}  # -> Timing.kind
TIMER_COMMANDS = {TIMER_START: "start", TIMER_CANCEL: "cancel", TIMER_REMAINING: "remaining"}

# Round 2 sentinels
SECONDS = "seconds"
MINUTES = "minutes"
HOURS = "hours"
NOT_DURATION = "not a duration"
UNIT_OPTIONS = (SECONDS, MINUTES, HOURS, NOT_DURATION)
UNIT_FACTORS = {SECONDS: 1.0, MINUTES: 60.0, HOURS: 3600.0}
NO_LABEL = "no label"
ALL_TIMERS = "all timers"
MIN_SECONDS = 5.0
MAX_SECONDS = 24 * 3600.0

# The same sentinel as hunch.round2.NO_MATCH. round2 imports timing, so timing cannot import
# round2; test_timing asserts the two strings are equal.
NO_MATCH = "none of these"


@dataclass(frozen=True)
class DurationLiteral:
    text: str  # verbatim from the prompt, unit word included when one follows ("8 Minuten")
    value: float  # the number it stands for
    # the unit word that followed the number, looked up ("Minuten" -> minutes); None for a bare
    # number, whose unit is Jev's call. A spoken unit is a fact, not a judgment: asked for the
    # unit of "halbe Stunde", Jev answered "30 minutes" half the time and "half an hour" the
    # other half — both right, and only one of them is the arithmetic we need.
    unit: str | None = None


_DE_ONES = {
    "ein": 1, "zwei": 2, "zwo": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9,
}  # fmt: skip
_EN_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9,
}  # fmt: skip
# Articles are numbers only with a unit behind them ("eine Stunde", "an hour"); alone they are
# just articles ("einen Timer" is not "1").
ARTICLES = {"ein": 1, "eine": 1, "einen": 1, "einer": 1, "a": 1, "an": 1}
_ONES = {**_DE_ONES, **_EN_ONES, **ARTICLES, "eins": 1}
_TEENS = {
    "zehn": 10, "ten": 10, "elf": 11, "eleven": 11, "zwölf": 12, "zwoelf": 12, "twelve": 12,
    "dreizehn": 13, "thirteen": 13, "vierzehn": 14, "fourteen": 14, "fünfzehn": 15,
    "fuenfzehn": 15, "fifteen": 15, "sechzehn": 16, "sixteen": 16, "siebzehn": 17,
    "seventeen": 17, "achtzehn": 18, "eighteen": 18, "neunzehn": 19, "nineteen": 19,
}  # fmt: skip
_DE_TENS = {
    "zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50,
    "fuenfzig": 50, "sechzig": 60,
}  # fmt: skip
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}
_TENS = {**_DE_TENS, **_EN_TENS}
_FRACTIONS = {
    "halbe": 0.5, "halben": 0.5, "halb": 0.5, "half": 0.5, "viertel": 0.25, "quarter": 0.25,
    "dreiviertel": 0.75, "anderthalb": 1.5, "eineinhalb": 1.5,
}  # fmt: skip
NUMBER_WORDS: dict[str, float] = {**_ONES, **_TEENS, **_TENS, **_FRACTIONS}


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_COMPOUND_DE = rf"(?:{_alt(_DE_ONES)})und(?:{_alt(_DE_TENS)})"  # fünfundzwanzig
_COMPOUND_EN = rf"(?:{_alt(_EN_TENS)})[- ](?:{_alt(_EN_ONES)})"  # twenty-five
_UNIT = (
    r"(?:sekunden?|sekunde|sek\.?|seconds?|secs?|minuten?|minute|min\.?|minutes?|mins?|"
    r"stunden?|stunde|std\.?|hours?|hrs?|h|s)"
)
_UNIT_LOOKUP = (
    (re.compile(r"^(?:stunden?|std\.?|hours?|hrs?|h)$", re.I), HOURS),
    (re.compile(r"^(?:minuten?|minute|min\.?|minutes?|mins?)$", re.I), MINUTES),
    (re.compile(r"^(?:sekunden?|sekunde|sek\.?|seconds?|secs?|s)$", re.I), SECONDS),
)


def unit_of(token: str | None) -> str | None:
    if token is None:
        return None
    return next((u for rx, u in _UNIT_LOOKUP if rx.match(token)), None)


_LITERAL = re.compile(
    rf"(?<![\w.,])(\d{{1,4}}(?:[.,]\d+)?|{_COMPOUND_DE}|{_COMPOUND_EN}|{_alt(NUMBER_WORDS)})"
    rf"(?:\s*(?:-\s*)?({_UNIT}))?(?![\w])",
    re.I,
)
_UNIT_WORD = re.compile(rf"^{_UNIT}$", re.I)


def _number_value(token: str) -> float | None:
    low = token.lower()
    if low[0].isdigit():
        return float(low.replace(",", "."))
    if low in NUMBER_WORDS:
        return float(NUMBER_WORDS[low])
    m = re.fullmatch(rf"({_alt(_DE_ONES)})und({_alt(_DE_TENS)})", low)
    if m:
        return float(_DE_ONES[m.group(1)] + _DE_TENS[m.group(2)])
    m = re.fullmatch(rf"({_alt(_EN_TENS)})[- ]({_alt(_EN_ONES)})", low)
    if m:
        return float(_EN_TENS[m.group(1)] + _EN_ONES[m.group(2)])
    return None


def duration_literals(prompt: str) -> tuple[DurationLiteral, ...]:
    """Code looks up: every number in the prompt (digits or a number word), with the unit word
    that follows it, verbatim. Which of them is a duration and in which unit is Jev's call."""
    out: list[DurationLiteral] = []
    for m in _LITERAL.finditer(prompt):
        if m.group(1).lower() in ARTICLES and m.group(2) is None:
            continue  # "einen Timer", "a timer": an article, not a number
        value = _number_value(m.group(1))
        if value is None:
            continue
        text = re.sub(r"\s+", " ", m.group(0).strip())
        out.append(DurationLiteral(text, value, unit_of(m.group(2))))
    return tuple(out)


TIMER_WORDS = frozenset(
    {
        "timer", "timers", "wecker", "alarm", "countdown", "eieruhr", "stell", "stelle",
        "stellen", "set", "start", "starte", "starten",
    }
)  # fmt: skip
_WORD = re.compile(r"[^\W\d_]{3,}", re.U)


def label_candidates(prompt: str, literals: tuple[DurationLiteral, ...]) -> tuple[str, ...]:
    """Code offers: the words of the prompt that could name what a timer is for — everything
    with three or more letters that is not a number, a unit, a timer word or part of a
    duration literal. Original casing, first occurrence wins. Jev picks (or says 'no label')."""
    used = {w.lower() for lit in literals for w in lit.text.split()}
    seen: dict[str, str] = {}
    for w in _WORD.findall(prompt):
        low = w.lower()
        if low in used or low in TIMER_WORDS or low in NUMBER_WORDS or _UNIT_WORD.match(low):
            continue
        seen.setdefault(low, w)
    return tuple(seen.values())


def timer_option(t: ActiveTimer) -> str:
    """The label Jev (and the reply judgment) sees for a running timer: its name or what it
    will do, plus the remaining time as m:ss."""
    name = t.label or t.description or "timer"
    m, s = divmod(int(max(t.remaining_seconds, 0.0)), 60)
    return f"{name} ({m}:{s:02d} left)"


def timer_options(timers: tuple[ActiveTimer, ...]) -> tuple[str, ...]:
    """One distinct label per timer, in order; a repeated label gets ' #2', ' #3'."""
    out: list[str] = []
    for t in timers:
        label = timer_option(t)
        n = 2
        while label in out:
            label = f"{timer_option(t)} #{n}"
            n += 1
        out.append(label)
    return tuple(out)


def duration_questions(literals, pb: Phrasebook = EN) -> dict[str, Question]:
    return {
        f"duration:{i}": ChoiceQ(
            pb.duration_question.format(literal=lit.text),
            UNIT_OPTIONS,
            dict(pb.duration_descriptions),
        )
        for i, lit in enumerate(literals)
    }


def timer_questions(kind, literals, labels, timers, pb: Phrasebook = EN) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    if kind == "start":
        qs.update(duration_questions(literals, pb))
        if labels:
            qs["timer_label"] = ChoiceQ(
                pb.timer_label_question,
                (*labels, NO_LABEL),
                {NO_LABEL: pb.special_descriptions["no_label"]},
            )
    elif timers and (kind == "cancel" or len(timers) > 1):
        # cancelling always asks, even with one timer: a bare "Timer abbrechen" while only a
        # pending "Wandlampe aus" runs must not cancel it blindly. With one timer "all timers"
        # and that timer are the same set, so the option is not offered (it split Jev's mass).
        several = len(timers) > 1
        qs["timer_pick"] = ChoiceQ(
            pb.timer_pick_question,
            (*timer_options(timers), *((ALL_TIMERS,) if several else ()), NO_MATCH),
            {
                **({ALL_TIMERS: pb.special_descriptions["all_timers"]} if several else {}),
                NO_MATCH: pb.special_descriptions["no_timer_match"],
            },
        )
    return qs


def timer_state(prompt, timers, literals, labels) -> JSON:
    state: dict[str, JSON] = {"request": prompt}
    if timers:
        state["timers"] = list(timer_options(timers))
    if literals:
        state["duration_literals"] = [lit.text for lit in literals]
    if labels:
        state["label_candidates"] = list(labels)
    return state


def sum_duration(
    answers: Answers | None, literals, trace: Trace
) -> tuple[float | None, list[float]]:
    """Code multiplies: every literal Jev called a duration, times its unit (the spoken unit
    word when there is one, else the unit Jev chose). None when nothing was a duration or the
    total is implausible (< 5 s, > 24 h)."""
    total = 0.0
    confs: list[float] = []
    if answers is None:
        return None, confs
    for i, lit in enumerate(literals):
        qid = f"duration:{i}"
        if qid not in answers.answers:
            continue
        c = answers.choice(qid)
        if lit.unit is not None and c.probabilities:
            # A spoken unit is looked up, so the only judgment taken from Jev here is
            # "duration or not": its confidence is the mass on the three units together.
            # (Asked for the unit of "halbe Stunde", Jev split minutes/hours 0.51/0.49 — sure it
            # is a duration, unsure how to name it; that split must not sink the turn.)
            confs.append(1.0 - c.probabilities.get(NOT_DURATION, 0.0))
        else:
            confs.append(c.confidence)
        if c.choice in UNIT_FACTORS:
            total += lit.value * UNIT_FACTORS[lit.unit or c.choice]
    if total <= 0:
        trace.note("timing:no_duration")
        return None, confs
    if not MIN_SECONDS <= total <= MAX_SECONDS:
        trace.note("timing:out_of_bounds")
        return None, confs
    trace.note(f"timing:seconds:{total:g}")
    return total, confs


def resolve_timer(
    kind, kind_conf, literals, labels, timers, round2, config: EngineConfig, trace
) -> Resolution:
    th = config.thresholds
    contributions = [kind_conf]
    if kind == "start":
        seconds, confs = sum_duration(round2, literals, trace)
        contributions.extend(confs)
        if seconds is None:
            return Escalate("timing", (), trace)
        label = None
        if round2 is not None and "timer_label" in round2.answers:
            c = round2.choice("timer_label")
            if c.choice != NO_LABEL and c.choice in labels:
                if trace.decide("timer_label", c.confidence, th.target_choice_conf):
                    label = c.choice
                    contributions.append(c.confidence)
                else:
                    trace.note("timer_label:hesitant")
        command = TimerCommand("start", seconds, label)
    else:
        if not timers:
            trace.note("timer:none_active")
            chosen: tuple = ()
        elif kind == "remaining" and len(timers) == 1:
            trace.note("timer:single")
            chosen = tuple(timers)
        else:
            pick = round2.choice("timer_pick") if round2 is not None else None
            by_label = dict(zip(timer_options(timers), timers, strict=True))
            sure = pick is not None and trace.decide(
                "timer_pick", pick.confidence, th.target_choice_conf
            )
            if pick is not None and pick.choice == ALL_TIMERS and sure:
                chosen = tuple(timers)
                contributions.append(pick.confidence)
            elif pick is not None and pick.choice in by_label and sure:
                chosen = (by_label[pick.choice],)
                contributions.append(pick.confidence)
            elif kind == "remaining":
                trace.note("timer_pick:all")
                chosen = tuple(timers)
            elif not config.supports_clarification:
                # a caller that cannot ask back gets a hand-off, never a blind cancel
                trace.note("timer:low_confidence")
                return Escalate("low_confidence", (), trace)
            else:
                return NeedsClarification(
                    "which_timer", (), trace, timers=tuple(timers), timer_kind="cancel"
                )
        command = TimerCommand(kind, timers=chosen)
    confidence = min(contributions)
    trace.decide("confidence", confidence, th.auto_execute)
    if confidence >= th.confirm_band:
        return Resolved((), None, confidence, trace, timer=command)
    trace.note("timer:low_confidence")
    return Escalate("low_confidence", (), trace)
