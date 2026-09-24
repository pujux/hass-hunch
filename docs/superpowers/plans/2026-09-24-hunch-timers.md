# Hunch Timers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hunch starts, cancels and reads any number of voice timers itself and carries out time-limited ("für 15 Minuten") and delayed ("in 15 Minuten") device actions, with timers persisted across HA restarts and announced through a `hunch_timer_finished` event plus an optional script.

**Architecture:** The engine (`packages/hunch`) learns a Round 1 comparison `timing_kind`, a timer-only Round 2 (duration unit per literal, label, which timer) and a `Timing` attached to normal device plans; it stays HA-free and returns `TimerCommand` / `Timing` on the existing result types. The integration (`custom_components/hunch`) gains a `TimerStore` (HA `Store` + `async_track_point_in_utc_time`), a firing routine (execute stored inverse/delayed actions, fire the event, call the script) and the conversation dispatch for timer commands and timed plans.

**Tech Stack:** Python 3.14 (uv workspace), `hunch-engine` (this repo), Home Assistant 2026.9.3 via `pytest-homeassistant-custom-component` (uv group `ha`), ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-09-24-hunch-timers-design.md`. Parents: `docs/superpowers/specs/2026-09-19-hunch-design.md`, `docs/superpowers/specs/2026-09-21-hunch-integration-design.md`.

## Global Constraints

- "Jev thinks, code is stupid": no code decides whether a number is a duration, which unit it is in, what a timer is for, or which running timer is meant. Code lists candidates (literals, words, active timers), looks up tables (number words, unit factors, verb inverses), sums, schedules.
- No threshold defaults change. New gates reuse `Thresholds.flag` (timing kind), `Thresholds.target_choice_conf` (label, timer pick), `auto_execute` / `confirm_band` (bands).
- Result types grow **defaulted** fields only; no new `Resolution` variant. New dataclass fields go at the END of every dataclass (positional construction exists).
- `Escalate` reasons stay a closed set; the only reason added to the table in `engine.py`'s docstring is none — `timing` widens in meaning; document it there.
- Engine package: no HA imports (`test_no_ha_imports.py` guards it). Integration: exact service calls with `hass.services.async_call(..., blocking=True)`; never intents.
- Persisted timer data: labels, descriptions, entity ids, times, device/satellite/conversation ids. Never the API key, never prompts.
- Every user-facing sentence lives in `responder.py` tables (en + de); every Jev-facing sentence in `phrasing.py` (EN + DE).
- Default suite: `uv run pytest -q` (no HA). HA suite: `uv run --group ha --no-group dev pytest tests/integration -q`. Lint: `uv run ruff format . && uv run ruff check .` (docs excluded by config).
- Commit after every green step. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Versions: engine `0.6.0` (`packages/hunch/pyproject.toml`, `hunch.__version__` stays as is — it is not maintained), integration `0.4.0`, manifest `"requirements": ["hunch-engine==0.6.0"]`. Publishing to PyPI is Julian's action, not part of any task.

---

## File structure

```
packages/hunch/src/hunch/resolution.py    ActiveTimer, Timing, TimerCommand; new fields on Resolved/NeedsConfirmation/NeedsClarification
packages/hunch/src/hunch/timing.py        NEW: sentinels, number words, duration_literals, label_candidates, timer Round 2 (state, questions, resolve_timer), sum_duration
packages/hunch/src/hunch/vocabulary.py    INVERSES
packages/hunch/src/hunch/phrasing.py      timing_kind / duration / timer_label / timer_pick wording, EN + DE
packages/hunch/src/hunch/round1.py        timing_kind Choice, timers in state, Shape.timing_kind/timing_conf
packages/hunch/src/hunch/round2.py        Round2Plan.timing_kind/duration_literals, duration questions, state
packages/hunch/src/hunch/resolver.py      Timing assembly + escalations
packages/hunch/src/hunch/engine.py        decide(timers=), gating, _decide_timer, clarify-before-duration guard
packages/hunch/src/hunch/__init__.py      exports
packages/hunch/tests/test_timing.py       NEW
packages/hunch/tests/test_round1.py, test_engine.py, test_resolver.py  additions
golden/run_golden.py, golden/corpus_julian.yaml, golden/corpus.yaml, golden/README.md
custom_components/hunch/timers.py         NEW: StoredAction, HunchTimer, TimerStore, inverse_actions, async_fire_timer
custom_components/hunch/const.py          OPT_TIMER_SCRIPT, EVENT_TIMER_FINISHED, TIMER_STORE_KEY/VERSION
custom_components/hunch/__init__.py       runtime.timers/timer_script, load/stop
custom_components/hunch/config_flow.py    timer_script option
custom_components/hunch/strings.json, translations/en.json, translations/de.json
custom_components/hunch/pending.py        timing on PendingConfirm/PendingClarify; PendingTimerPick
custom_components/hunch/responder.py      format_duration, timer_name, REVERT_WORDS, templates
custom_components/hunch/conversation.py   timers= to decide, _run_timer, timing in _run, which_timer reply
custom_components/hunch/diagnostics.py    timers block
custom_components/hunch/manifest.json     0.4.0 / hunch-engine==0.6.0
tests/integration/test_timers.py          NEW
tests/integration/test_conversation.py, test_options_flow.py, test_diagnostics.py  additions
tests/unit/test_responder.py, tests/unit/test_pending.py  additions
README.md                                 timers section
```

---

### Task 1: Engine types, timing lookups, inverses, phrasing

**Files:**
- Modify: `packages/hunch/src/hunch/resolution.py`
- Create: `packages/hunch/src/hunch/timing.py`
- Modify: `packages/hunch/src/hunch/vocabulary.py`
- Modify: `packages/hunch/src/hunch/phrasing.py`
- Modify: `packages/hunch/src/hunch/__init__.py`
- Test: `packages/hunch/tests/test_timing.py`

**Interfaces:**
- Produces: `ActiveTimer`, `Timing`, `TimerCommand` (spec §4.1); `Resolved.timing/.timer`, `NeedsConfirmation.timing`, `NeedsClarification.timing/.timers/.timer_kind`; `timing.py` constants `TIMING_NONE, TIMER_START, TIMER_CANCEL, TIMER_REMAINING, FOR_DURATION, DELAYED, CLOCK_TIME, TIMING_OTHER, TIMING_KIND_OPTIONS, TIMER_KINDS, DEVICE_TIMING_KINDS, TIMING_KIND_TO_TIMING, TIMER_COMMANDS, SECONDS, MINUTES, HOURS, NOT_DURATION, UNIT_OPTIONS, UNIT_FACTORS, NO_LABEL, ALL_TIMERS, MIN_SECONDS, MAX_SECONDS`; functions `duration_literals(prompt) -> tuple[DurationLiteral, ...]`, `label_candidates(prompt, literals) -> tuple[str, ...]`, `timer_option(t: ActiveTimer) -> str`; `vocabulary.INVERSES: Mapping[str, str]`; phrasebook fields `timing_kind_question, timing_kind_descriptions, duration_question, duration_descriptions, timer_label_question, timer_pick_question` and `special_descriptions` keys `no_label, all_timers, no_timer_match`.

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_timing.py`:

```python
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
from hunch.timing import (
    ALL_TIMERS,
    NO_LABEL,
    TIMING_KIND_OPTIONS,
    UNIT_OPTIONS,
    duration_literals,
    label_candidates,
    timer_option,
)
from hunch.vocabulary import DEFAULT_VOCABULARY, INVERSES


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
    assert _values("Timer eine halbe Stunde") == [1.0, 0.5]
    assert _texts("Timer eine halbe Stunde") == ["eine", "halbe Stunde"]
    assert _values("Eine Viertelstunde Timer") == [1.0, 0.25]
    assert _texts("Eine Viertelstunde Timer") == ["Eine", "Viertelstunde"]
    assert _values("anderthalb Stunden") == [1.5]


def test_two_literals_for_hour_and_minutes():
    assert _texts("Timer 1 Stunde 20") == ["1 Stunde", "20"]


def test_words_inside_other_words_are_not_literals():
    assert _texts("Licht einschalten") == []
    assert _texts("Spots an") == []


def test_label_candidates_drop_timer_words_and_literal_parts():
    lits = duration_literals("Stell einen Timer für die Nudeln auf 8 Minuten")
    assert label_candidates("Stell einen Timer für die Nudeln auf 8 Minuten", lits) == (
        "Stell",
        "einen",
        "für",
        "die",
        "Nudeln",
        "auf",
    )
    lits = duration_literals("Timer eine halbe Stunde")
    assert label_candidates("Timer eine halbe Stunde", lits) == ()


def test_timer_option_shows_name_and_remaining():
    t = ActiveTimer("a", "Nudeln", 200.0, "timer")
    assert timer_option(t) == "Nudeln (3:20 left)"
    t2 = ActiveTimer("b", None, 59.4, "delayed", "Wandlampe aus")
    assert timer_option(t2) == "Wandlampe aus (0:59 left)"
    t3 = ActiveTimer("c", None, 480.0, "timer")
    assert timer_option(t3) == "timer (8:00 left)"


def test_inverses_are_symmetric_and_only_for_reversible_verbs():
    for a, b in INVERSES.items():
        assert INVERSES[b] == a
        assert DEFAULT_VOCABULARY.by_name(a) and DEFAULT_VOCABULARY.by_name(b)
    assert "set_brightness" not in INVERSES and "activate" not in INVERSES
    assert INVERSES["turn_on"] == "turn_off" and INVERSES["close"] == "open"


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest packages/hunch/tests/test_timing.py -q`
Expected: ImportError (`hunch.timing` missing).

- [ ] **Step 3: Result types** (`resolution.py`)

Add after `Condition`:

```python
@dataclass(frozen=True)
class ActiveTimer:
    """A timer the caller currently runs: input to `decide`, and what a cancel or a
    remaining-time question names."""

    timer_id: str
    label: str | None  # what the timer is for ("Nudeln"); None when unnamed
    remaining_seconds: float
    kind: str  # "timer" | "revert" | "delayed"
    description: str | None = None  # scheduled actions: what will happen ("Wandlampe aus")


@dataclass(frozen=True)
class Timing:
    """A device action bound to a duration: do it now and undo it after ("für 15 Minuten"),
    or do it after a delay ("in 15 Minuten")."""

    kind: str  # "for_duration" | "delayed"
    seconds: float


@dataclass(frozen=True)
class TimerCommand:
    kind: str  # "start" | "cancel" | "remaining"
    duration_seconds: float | None = None  # start
    label: str | None = None  # start
    timers: tuple[ActiveTimer, ...] = ()  # cancel / remaining: the timers meant
```

Append to `Resolved`: `timing: Timing | None = None` and `timer: TimerCommand | None = None`. To `NeedsConfirmation`: `timing: Timing | None = None`. To `NeedsClarification` (after `params`): `timing: Timing | None = None`, `timers: tuple[ActiveTimer, ...] = ()`, `timer_kind: str | None = None`; extend its `question_key` comment with `"which_timer"`. Extend the `Escalate` reason comment: `timing` = "timing Hunch cannot carry out itself (clock time, sequence, no duration, non-invertible 'für', timed query/condition, out of bounds)".

- [ ] **Step 4: `INVERSES`** (`vocabulary.py`, after `verbs_for_domain`)

```python
# Verbs a "für 15 Minuten" can undo: code table, symmetric. Everything else (set_*, arm,
# disarm, activate, queries) has no inverse, so a time-limited request on it is handed off.
INVERSES: dict[str, str] = {
    "turn_on": "turn_off",
    "turn_off": "turn_on",
    "open": "close",
    "close": "open",
    "lock": "unlock",
    "unlock": "lock",
    "media_play": "media_pause",
    "media_pause": "media_play",
}
```

- [ ] **Step 5: `timing.py`** (lookups only in this task; the Round 2 half comes in Task 3)

```python
"""Timers and timing: the part code can look up (numbers, units, inverses, active timers) and
the timer-only Round 2. Whether a number is a duration, in which unit, what a timer is for and
which running timer is meant are Jev's judgments."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hunch.resolution import ActiveTimer

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


@dataclass(frozen=True)
class DurationLiteral:
    text: str  # verbatim from the prompt, unit word included when one follows ("8 Minuten")
    value: float  # the number it stands for; the unit is Jev's call


_ONES = {
    "ein": 1, "eine": 1, "einen": 1, "einer": 1, "eins": 1, "one": 1,
    "zwei": 2, "zwo": 2, "two": 2, "drei": 3, "three": 3, "vier": 4, "four": 4,
    "fünf": 5, "fuenf": 5, "five": 5, "sechs": 6, "six": 6, "sieben": 7, "seven": 7,
    "acht": 8, "eight": 8, "neun": 9, "nine": 9,
}  # fmt: skip
_TEENS = {
    "zehn": 10, "ten": 10, "elf": 11, "eleven": 11, "zwölf": 12, "zwoelf": 12, "twelve": 12,
    "dreizehn": 13, "thirteen": 13, "vierzehn": 14, "fourteen": 14, "fünfzehn": 15,
    "fuenfzehn": 15, "fifteen": 15, "sechzehn": 16, "sixteen": 16, "siebzehn": 17,
    "seventeen": 17, "achtzehn": 18, "eighteen": 18, "neunzehn": 19, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "zwanzig": 20, "twenty": 20, "dreißig": 30, "dreissig": 30, "thirty": 30,
    "vierzig": 40, "forty": 40, "fünfzig": 50, "fuenfzig": 50, "fifty": 50,
    "sechzig": 60, "sixty": 60,
}  # fmt: skip
_FRACTIONS = {
    "halbe": 0.5, "halben": 0.5, "halb": 0.5, "half": 0.5, "viertel": 0.25, "quarter": 0.25,
    "dreiviertel": 0.75, "anderthalb": 1.5, "eineinhalb": 1.5,
}  # fmt: skip
NUMBER_WORDS: dict[str, float] = {**_ONES, **_TEENS, **_TENS, **_FRACTIONS}


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_DE_TENS = {k: v for k, v in _TENS.items() if not k.endswith("ty")}
_DE_ONES = {k: v for k, v in _ONES.items() if k not in ("eine", "einen", "einer", "eins", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")}
_EN_TENS = {k: v for k, v in _TENS.items() if k.endswith("ty")}
_EN_ONES = {k: v for k, v in _ONES.items() if k in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")}
_COMPOUND_DE = rf"(?:{_alt(_DE_ONES)})und(?:{_alt(_DE_TENS)})"  # fünfundzwanzig
_COMPOUND_EN = rf"(?:{_alt(_EN_TENS)})[- ](?:{_alt(_EN_ONES)})"  # twenty-five
_UNIT = (
    r"(?:sekunden?|sekunde|sek\.?|seconds?|secs?|minuten?|minute|min\.?|minutes?|mins?|"
    r"stunden?|stunde|std\.?|hours?|hrs?|h|s)"
)
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
        value = _number_value(m.group(1))
        if value is None:
            continue
        text = re.sub(r"\s+", " ", m.group(0).strip())
        out.append(DurationLiteral(text, value))
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
```

Note for the implementer: `label_candidates` must also skip words that are part of a duration literal *including* the number word itself ("Eine", "halbe", "Viertelstunde" are literal parts). The test `("Stell", "einen", "für", "die", "Nudeln", "auf")` deliberately keeps function words — Jev filters, code does not; only timer words and numbers/units are removed. `stell/set/start…` are in `TIMER_WORDS` so "Stell" IS removed — adjust the expected tuple in the test to `("einen", "für", "die", "Nudeln", "auf")`. (Keep the test truthful to the table you ship; the point is: no `Nudeln` lost, no `Minuten` kept.)

- [ ] **Step 6: Phrasebook** (`phrasing.py`)

Add to the `Phrasebook` dataclass (after `room_target_question`, with EN defaults so DE can override):

```python
    timing_kind_question: str = (
        "How is the request bound to time? Compare the options: a standalone timer that "
        "controls no device (start one, cancel one, ask how long one has left), a device action "
        "that lasts for a duration and is then undone, a device action after a delay, something "
        "at a clock time or date, other timing (sequences, 'later', 'then'), or none at all."
    )
    timing_kind_descriptions: Mapping[str, str] = field(
        default_factory=lambda: {
            "none": "no timing at all — the request is about doing something now",
            "start a timer": (
                "set a countdown / kitchen timer / alarm that controls no device: 'Timer 8 "
                "Minuten', 'stell einen Timer für die Nudeln', 'timer for ten minutes'"
            ),
            "cancel a timer": "stop or delete a running timer: 'Timer abbrechen', 'cancel the timer'",
            "ask how long a timer has left": (
                "ask a running timer's remaining time: 'wie lange noch?', 'how long is left on "
                "the pasta timer?'"
            ),
            "device action for a duration": (
                "do something to a device now and undo it after a duration: 'Licht an für 15 "
                "Minuten', 'open the blinds for ten minutes'"
            ),
            "device action after a delay": (
                "do something to a device after a delay: 'in 15 Minuten aus', 'turn it off in "
                "ten minutes', 'nachher'"
            ),
            "at a clock time or date": "at a clock time or on a date: 'um 18 Uhr', 'morgen früh', 'at 7'",
            "other timing": "any other time binding: sequences ('erst …, dann …'), 'später', 'danach'",
        }
    )
    duration_question: str = (
        "In the request, `{literal}` may say how long something lasts or how long to wait. "
        "Which unit is it meant in — or is it not a duration at all?"
    )
    duration_descriptions: Mapping[str, str] = field(
        default_factory=lambda: {
            "seconds": "seconds (Sekunden, sek, s)",
            "minutes": (
                "minutes (Minuten, min) — also the usual unit of a bare number said with a "
                "kitchen timer ('Timer 8')"
            ),
            "hours": "hours (Stunden, std, h)",
            "not a duration": (
                "the number is not a duration: a percentage, a temperature, a count, a channel, "
                "part of a name"
            ),
        }
    )
    timer_label_question: str = (
        "Which single word says WHAT the timer is for — the dish, the task, the thing being "
        "timed ('Nudeln', 'Tee', 'Wäsche', 'pasta')? Pick 'no label' when the request only says "
        "timer and a duration."
    )
    timer_pick_question: str = (
        "Which running timer does the request mean? `timers` lists them with their remaining "
        "time. Pick 'all timers' when every one of them is meant."
    )
```

Add to BOTH `special_descriptions` dicts:

EN: `"no_label": "the request names nothing the timer is for"`, `"all_timers": "every running timer is meant"`, `"no_timer_match": "none of the listed timers is the one meant"`.
DE: `"no_label": "die Anfrage nennt nichts, wofür der Timer ist"`, `"all_timers": "alle laufenden Timer sind gemeint"`, `"no_timer_match": "keiner der aufgezählten Timer ist gemeint"`.

Give DE overrides of `timing_kind_question`, `timing_kind_descriptions` (same 8 keys, German wording, same examples), `duration_question` (with `{literal}`), `duration_descriptions`, `timer_label_question`, `timer_pick_question` in the `DE = Phrasebook(...)` construction.

- [ ] **Step 7: Exports** (`__init__.py`): add `ActiveTimer`, `Timing`, `TimerCommand` to the import from `hunch.resolution` and to `__all__`; add `INVERSES` from `hunch.vocabulary` and to `__all__`.

- [ ] **Step 8: Run tests, lint**

Run: `uv run pytest -q && uv run ruff format . && uv run ruff check .`
Expected: all pass (existing 236 + new).

- [ ] **Step 9: Commit**

```bash
git add packages/hunch
git commit -m "Engine: timer and timing result types, duration/label lookups, verb inverses, timing phrasing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Round 1 `timing_kind`

**Files:**
- Modify: `packages/hunch/src/hunch/round1.py`
- Test: `packages/hunch/tests/test_round1.py`

**Interfaces:**
- Consumes: `timing.TIMING_KIND_OPTIONS`, `TIMING_NONE`, phrasebook fields (Task 1).
- Produces: `build_round1_state(home, prompt, previous=None, timers=())` adds `"timers"`; `build_round1_questions` adds `"timing_kind"` ChoiceQ; `Shape.timing_kind: str | None = None`, `Shape.timing_conf: float = 0.0`; `interpret_round1` fills them and notes `timing_kind:<id>` for a non-none pick.

- [ ] **Step 1: Failing tests** (append to `test_round1.py`; reuse that file's fixtures/helpers — read its head first)

```python
def test_timing_kind_is_a_described_choice_and_timers_enter_the_state(home, vocab):
    from hunch.questions import ChoiceQ
    from hunch.resolution import ActiveTimer
    from hunch.round1 import build_round1_questions, build_round1_state
    from hunch.timing import TIMING_KIND_OPTIONS

    qs = build_round1_questions(home, vocab)
    q = qs["timing_kind"]
    assert isinstance(q, ChoiceQ) and q.options == TIMING_KIND_OPTIONS
    assert set(q.descriptions) == set(TIMING_KIND_OPTIONS)
    assert "timers" not in build_round1_state(home, "Timer 8 Minuten")
    state = build_round1_state(
        home, "wie lange noch?", timers=(ActiveTimer("a", "Nudeln", 200.4, "timer"),)
    )
    assert state["timers"] == [{"label": "Nudeln", "description": None, "remaining_seconds": 200}]


def test_shape_carries_the_timing_kind(home, vocab, thresholds):
    from hunch.questions import Answers, ChoiceA, NoulA
    from hunch.resolution import Trace
    from hunch.round1 import build_round1_questions, interpret_round1
    from hunch.timing import TIMER_START

    qs = build_round1_questions(home, vocab)
    answers = {qid: NoulA(0.05) for qid in qs}
    for qid, q in qs.items():
        if isinstance(q, ChoiceQ):
            answers[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
    answers["timing_kind"] = ChoiceA(TIMER_START, 0.88, {})
    trace = Trace()
    shape = interpret_round1(home, vocab, Answers(answers, "m", None), thresholds, trace)
    assert shape.timing_kind == TIMER_START and shape.timing_conf == 0.88
    assert "timing_kind:start a timer" in trace.notes
```

Adapt the `Answers(...)` constructor and fixture names to what `test_round1.py` already uses (check `hunch/questions.py` for `Answers` fields and `conftest.py` for `thresholds`/`config` fixtures; if there is no `thresholds` fixture use `Thresholds()`).

- [ ] **Step 2: Run to verify failure**: `uv run pytest packages/hunch/tests/test_round1.py -q` → KeyError `timing_kind`.

- [ ] **Step 3: Implement**

`build_round1_state(home, prompt, previous=None, timers: tuple[ActiveTimer, ...] = ())`: after `previous`, add

```python
    if timers:
        state["timers"] = [
            {
                "label": t.label,
                "description": t.description,
                "remaining_seconds": int(round(t.remaining_seconds)),
            }
            for t in timers
        ]
```

`build_round1_questions`: after the flags loop:

```python
    qs["timing_kind"] = ChoiceQ(
        pb.timing_kind_question, TIMING_KIND_OPTIONS, dict(pb.timing_kind_descriptions)
    )
```

`Shape`: append `timing_kind: str | None = None` and `timing_conf: float = 0.0` (END of the dataclass). `interpret_round1`: before `return Shape(...)`:

```python
    timing_kind: str | None = None
    timing_conf = 0.0
    if "timing_kind" in answers.answers:
        tk = answers.choice("timing_kind")
        if tk.choice in TIMING_KIND_OPTIONS and tk.choice != TIMING_NONE:
            timing_kind, timing_conf = tk.choice, tk.confidence
            trace.note(f"timing_kind:{tk.choice}")
```

and pass `timing_kind=timing_kind, timing_conf=timing_conf` to `Shape(...)`. Import `from hunch.timing import TIMING_KIND_OPTIONS, TIMING_NONE` and `ActiveTimer` from `hunch.resolution`.

- [ ] **Step 4: Run** `uv run pytest -q` (the fake clients in `test_engine.py` default unknown ChoiceQs to `"none"`, so nothing else changes) and lint.

- [ ] **Step 5: Commit** `git commit -am "Round 1 compares how the request is bound to time (timing_kind); active timers in the state"` (+ trailer).

---

### Task 3: The timer path in the engine

**Files:**
- Modify: `packages/hunch/src/hunch/timing.py` (Round 2 half)
- Modify: `packages/hunch/src/hunch/engine.py`
- Test: `packages/hunch/tests/test_engine.py`

**Interfaces:**
- Consumes: Task 1 + 2.
- Produces: `Engine.decide(home, prompt, previous=None, timers=())`; `timing.timer_state(prompt, timers, literals, labels) -> JSON`; `timing.timer_questions(kind, literals, labels, timers, pb) -> dict[str, Question]`; `timing.duration_questions(literals, pb) -> dict[str, Question]` (ids `duration:{i}`); `timing.sum_duration(answers, literals, trace) -> tuple[float | None, list[float]]`; `timing.resolve_timer(kind, kind_conf, literals, labels, timers, round2, config, trace) -> Resolution`; `Engine._decide_timer`.

- [ ] **Step 1: Failing tests** (append to `test_engine.py`; `_scripted` is there)

```python
from hunch.resolution import ActiveTimer, NeedsClarification, Resolved, Escalate
from hunch.timing import ALL_TIMERS, MINUTES, NOT_DURATION, TIMER_CANCEL, TIMER_REMAINING, TIMER_START, HOURS


async def test_timer_start_with_label_and_minutes(home, vocab, config):
    client, calls = _scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.9, {}), "flag:is_fragment": NoulA(0.95)},
        {"duration:0": ChoiceA(MINUTES, 0.9, {}), "timer_label": ChoiceA("Nudeln", 0.85, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "Timer für die Nudeln 8 Minuten")
    assert isinstance(r, Resolved) and r.actions == () and r.timing is None
    assert r.timer.kind == "start" and r.timer.duration_seconds == 480 and r.timer.label == "Nudeln"
    assert calls["n"] == 2
    assert "timing:seconds:480" in r.trace.notes


async def test_timer_start_sums_hours_and_minutes_and_drops_hesitant_label(home, vocab, config):
    client, _ = _scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.9, {})},
        {
            "duration:0": ChoiceA(HOURS, 0.9, {}),
            "duration:1": ChoiceA(MINUTES, 0.8, {}),
            "timer_label": ChoiceA("Braten", 0.4, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(home, "Timer Braten 1 Stunde 20")
    assert isinstance(r, Resolved) and r.timer.duration_seconds == 4800 and r.timer.label is None
    assert "timer_label:hesitant" in r.trace.notes and r.confidence == 0.8


async def test_timer_start_without_a_number_hands_off(home, vocab, config):
    client, calls = _scripted({"timing_kind": ChoiceA(TIMER_START, 0.9, {})})
    r = await Engine(client, vocab, config).decide(home, "Stell einen Timer für die Nudeln")
    assert isinstance(r, Escalate) and r.reason == "timing" and calls["n"] == 1
    assert "timing:no_duration" in r.trace.notes


async def test_timer_start_where_every_number_is_rejected_hands_off(home, vocab, config):
    client, _ = _scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.9, {})},
        {"duration:0": ChoiceA(NOT_DURATION, 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "Timer 15")
    assert isinstance(r, Escalate) and r.reason == "timing"


async def test_remaining_with_no_timer_needs_no_second_round(home, vocab, config):
    client, calls = _scripted({"timing_kind": ChoiceA(TIMER_REMAINING, 0.9, {})})
    r = await Engine(client, vocab, config).decide(home, "wie lange noch?")
    assert isinstance(r, Resolved) and r.timer.kind == "remaining" and r.timer.timers == ()
    assert calls["n"] == 1 and "timer:none_active" in r.trace.notes


async def test_single_timer_is_the_one(home, vocab, config):
    t = ActiveTimer("a", "Nudeln", 200, "timer")
    client, calls = _scripted({"timing_kind": ChoiceA(TIMER_CANCEL, 0.9, {})})
    r = await Engine(client, vocab, config).decide(home, "Timer abbrechen", timers=(t,))
    assert isinstance(r, Resolved) and r.timer.kind == "cancel" and r.timer.timers == (t,)
    assert calls["n"] == 1


async def test_two_timers_ask_jev_and_a_hesitant_cancel_clarifies(home, vocab, config):
    a, b = ActiveTimer("a", "Nudeln", 200, "timer"), ActiveTimer("b", "Reis", 600, "timer")
    client, calls = _scripted(
        {"timing_kind": ChoiceA(TIMER_CANCEL, 0.9, {})},
        {"timer_pick": ChoiceA("Nudeln (3:20 left)", 0.5, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "Timer abbrechen", timers=(a, b))
    assert isinstance(r, NeedsClarification) and r.question_key == "which_timer"
    assert r.timers == (a, b) and r.timer_kind == "cancel" and r.candidates == ()
    assert calls["n"] == 2


async def test_two_timers_sure_pick_and_all_timers(home, vocab, config):
    a, b = ActiveTimer("a", "Nudeln", 200, "timer"), ActiveTimer("b", None, 600, "delayed", "Wandlampe aus")
    client, _ = _scripted(
        {"timing_kind": ChoiceA(TIMER_REMAINING, 0.9, {})},
        {"timer_pick": ChoiceA("Wandlampe aus (10:00 left)", 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "wie lange noch bis die Lampe aus geht", timers=(a, b))
    assert isinstance(r, Resolved) and r.timer.timers == (b,)
    client, _ = _scripted(
        {"timing_kind": ChoiceA(TIMER_CANCEL, 0.9, {})},
        {"timer_pick": ChoiceA(ALL_TIMERS, 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "alle Timer abbrechen", timers=(a, b))
    assert isinstance(r, Resolved) and r.timer.timers == (a, b)


async def test_hesitant_remaining_reads_all(home, vocab, config):
    a, b = ActiveTimer("a", "Nudeln", 200, "timer"), ActiveTimer("b", "Reis", 600, "timer")
    client, _ = _scripted(
        {"timing_kind": ChoiceA(TIMER_REMAINING, 0.9, {})},
        {"timer_pick": ChoiceA(NO_MATCH, 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "wie lange noch?", timers=(a, b))
    assert isinstance(r, Resolved) and r.timer.timers == (a, b) and "timer_pick:all" in r.trace.notes


async def test_hesitant_timer_kind_with_has_timing_still_hands_off(home, vocab, config):
    client, calls = _scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.5, {}), "flag:has_timing": NoulA(0.8), "verb:turn_off": NoulA(0.9)}
    )
    r = await Engine(client, vocab, config).decide(home, "Licht aus, Timer 8 Minuten")
    assert isinstance(r, Escalate) and r.reason == "timing" and calls["n"] == 1
```

- [ ] **Step 2: Run to verify failure**: `uv run pytest packages/hunch/tests/test_engine.py -q -k timer` → TypeError (`timers` kwarg) / AttributeError.

- [ ] **Step 3: `timing.py` Round 2 half** (append)

```python
from hunch.config import EngineConfig
from hunch.phrasing import EN, Phrasebook
from hunch.questions import JSON, Answers, ChoiceQ, Question
from hunch.resolution import (
    Escalate,
    NeedsClarification,
    Resolution,
    Resolved,
    TimerCommand,
    Trace,
)

NO_MATCH = "none of these"  # keep identical to hunch.round2.NO_MATCH (import it there instead
                            # if round2 does not import timing — avoid a cycle: round2 will
                            # import timing, so timing must NOT import round2; define the same
                            # string here and add an assertion test that they are equal)


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
    elif len(timers) > 1:
        qs["timer_pick"] = ChoiceQ(
            pb.timer_pick_question,
            (*(timer_option(t) for t in timers), ALL_TIMERS, NO_MATCH),
            {
                ALL_TIMERS: pb.special_descriptions["all_timers"],
                NO_MATCH: pb.special_descriptions["no_timer_match"],
            },
        )
    return qs


def timer_state(prompt, timers, literals, labels) -> JSON:
    state: dict[str, JSON] = {"request": prompt}
    if timers:
        state["timers"] = [timer_option(t) for t in timers]
    if literals:
        state["duration_literals"] = [lit.text for lit in literals]
    if labels:
        state["label_candidates"] = list(labels)
    return state


def sum_duration(answers: Answers | None, literals, trace: Trace) -> tuple[float | None, list[float]]:
    """Code multiplies: every literal Jev called a duration, times its unit. None when nothing
    was a duration or the total is implausible (< 5 s, > 24 h)."""
    total = 0.0
    confs: list[float] = []
    if answers is None:
        return None, confs
    for i, lit in enumerate(literals):
        qid = f"duration:{i}"
        if qid not in answers.answers:
            continue
        c = answers.choice(qid)
        confs.append(c.confidence)
        if c.choice in UNIT_FACTORS:
            total += lit.value * UNIT_FACTORS[c.choice]
    if total <= 0:
        trace.note("timing:no_duration")
        return None, confs
    if not MIN_SECONDS <= total <= MAX_SECONDS:
        trace.note("timing:out_of_bounds")
        return None, confs
    trace.note(f"timing:seconds:{total:g}")
    return total, confs


def resolve_timer(kind, kind_conf, literals, labels, timers, round2, config: EngineConfig, trace) -> Resolution:
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
        elif len(timers) == 1:
            trace.note("timer:single")
            chosen = tuple(timers)
        else:
            pick = round2.choice("timer_pick") if round2 is not None else None
            by_label = {timer_option(t): t for t in timers}
            sure = pick is not None and trace.decide("timer_pick", pick.confidence, th.target_choice_conf)
            if pick is not None and pick.choice == ALL_TIMERS and sure:
                chosen = tuple(timers)
                contributions.append(pick.confidence)
            elif pick is not None and pick.choice in by_label and sure:
                chosen = (by_label[pick.choice],)
                contributions.append(pick.confidence)
            elif kind == "remaining":
                trace.note("timer_pick:all")
                chosen = tuple(timers)
            else:
                return NeedsClarification("which_timer", (), trace, timers=tuple(timers), timer_kind="cancel")
        command = TimerCommand(kind, timers=chosen)
    confidence = min(contributions)
    trace.decide("confidence", confidence, th.auto_execute)
    if confidence >= th.confirm_band:
        return Resolved((), None, confidence, trace, timer=command)
    trace.note("timer:low_confidence")
    return Escalate("low_confidence", (), trace)
```

Add a test asserting `hunch.timing.NO_MATCH == hunch.round2.NO_MATCH` (in `test_timing.py`).

- [ ] **Step 4: `engine.py`**

- `decide(self, home, prompt, previous=None, timers: tuple[ActiveTimer, ...] = ())` → `_decide(home, prompt, trace, previous, timers)`; pass `timers` to `build_round1_state(home, prompt, previous, timers)`.
- Replace the `has_timing` block with:

```python
        if shape.timing_kind in TIMER_KINDS and trace.decide(
            "timing_kind", shape.timing_conf, th.flag
        ):
            # "Timer 8 Minuten", "Timer abbrechen", "wie lange noch?": no device, no room, no
            # verb — the timer path, before the fragment rule can call it incomplete.
            return await self._decide_timer(prompt, shape, timers, trace, rounds)
        timing_kind: str | None = None
        if shape.timing_kind in DEVICE_TIMING_KINDS and trace.decide(
            "timing_kind", shape.timing_conf, th.flag
        ):
            timing_kind = TIMING_KIND_TO_TIMING[shape.timing_kind]
        elif trace.decide("flag:has_timing", shape.flag("has_timing"), th.flag):
            # a clock time, a sequence, "other", or a kind Jev was not sure about
            return Escalate("timing", (), trace)
```

`timing_kind` is used by Task 4; for this task leave it assigned (ruff may flag unused — add a `del timing_kind` placeholder? No: Task 4 lands right after; to keep this commit lint-clean, add `trace.note(f"timing:device:{timing_kind}")` when set, which Task 4 keeps).

- Add:

```python
    async def _decide_timer(self, prompt, shape, timers, trace, rounds) -> Resolution:
        kind = TIMER_COMMANDS[shape.timing_kind]
        literals = duration_literals(prompt) if kind == "start" else ()
        labels = label_candidates(prompt, literals) if kind == "start" else ()
        if kind == "start" and not literals:
            trace.note("timing:no_duration")
            return Escalate("timing", (), trace)
        questions = timer_questions(kind, literals, labels, timers, self._pb)
        round2 = None
        if questions:
            if rounds >= self._config.max_rounds:
                trace.note("max_rounds_reached_before_round2")
                return Escalate("round_budget", (), trace)
            round2 = await self._client.ask(timer_state(prompt, timers, literals, labels), questions)
            trace.record(2, round2)
        return resolve_timer(kind, shape.timing_conf, literals, labels, timers, round2, self._config, trace)
```

- Update the `decide` docstring table: `timing` row → "timing Hunch cannot carry out itself: a clock time or date, a sequence, no duration found, 'für' on a verb without inverse, a timed query or conditional action". Add a paragraph: "`timers` are the caller's running timers; a `Resolved` may carry `timer` (a `TimerCommand`, `actions == ()`) or `timing` (a `Timing` on the actions)."

- [ ] **Step 5: Run** `uv run pytest -q` and lint. The existing `test_timing_escalates_immediately` still passes (timing_kind defaults to `none`).

- [ ] **Step 6: Commit** `"Timers: start, cancel and remaining-time as a Jev-judged path with duration units, label and timer pick"` (+ trailer).

---

### Task 4: Timed device actions (`für` / `in`)

**Files:**
- Modify: `packages/hunch/src/hunch/round2.py`, `resolver.py`, `engine.py`
- Test: `packages/hunch/tests/test_engine.py`, `packages/hunch/tests/test_round2.py`

**Interfaces:**
- Consumes: `timing.duration_literals`, `duration_questions`, `sum_duration`, `TIMING_KIND_TO_TIMING`, `vocabulary.INVERSES`.
- Produces: `Round2Plan.timing_kind: str | None = None`, `Round2Plan.duration_literals: tuple[DurationLiteral, ...] = ()` (END of dataclass); `plan_round2(..., timing_kind: str | None = None)`; `build_round2_questions` adds `duration:{i}`; `build_round2_state` adds `"duration_literals"`; `resolve(...)` returns `Resolved/NeedsConfirmation/NeedsClarification` with `timing`.

- [ ] **Step 1: Failing tests** (append to `test_engine.py`)

```python
from hunch.timing import DELAYED, FOR_DURATION

R1_KITCHEN_ON = {
    "verb:turn_on": NoulA(0.95),
    "area:kitchen": NoulA(0.95),
    "domain:light": NoulA(0.9),
    "verb_primary": ChoiceA("turn_on", 0.95, {}),
    "area_primary": ChoiceA("Kitchen", 0.95, {}),
}


async def test_for_duration_attaches_timing_to_the_plan(home, vocab, config):
    client, calls = _scripted(
        {**R1_KITCHEN_ON, "timing_kind": ChoiceA(FOR_DURATION, 0.9, {}), "flag:has_timing": NoulA(0.9)},
        {"target:turn_on": ChoiceA("Kitchen ceiling (Kitchen)", 0.9, {}), "duration:0": ChoiceA(MINUTES, 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "Kitchen ceiling on for 15 minutes")
    assert isinstance(r, Resolved | NeedsConfirmation)
    assert r.timing == Timing("for_duration", 900) and r.actions[0].verb.name == "turn_on"
    assert calls["n"] == 2


async def test_delayed_turn_off_of_a_set(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:turn_off": NoulA(0.95), "area:kitchen": NoulA(0.95), "domain:light": NoulA(0.9),
            "flag:collective": NoulA(0.9), "verb_primary": ChoiceA("turn_off", 0.95, {}),
            "area_primary": ChoiceA("Kitchen", 0.95, {}), "timing_kind": ChoiceA(DELAYED, 0.9, {}),
        },
        {"duration:0": ChoiceA(MINUTES, 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "turn the kitchen lights off in ten minutes")
    assert isinstance(r, Resolved) and r.timing == Timing("delayed", 600)
    assert {e.entity_id for e in r.actions[0].targets} == {"light.kitchen_ceiling", "light.kitchen_counter"}


async def test_for_duration_on_a_verb_without_inverse_hands_off(home, vocab, config):
    client, _ = _scripted(
        {
            "verb:set_brightness": NoulA(0.95), "area:kitchen": NoulA(0.95), "domain:light": NoulA(0.9),
            "verb_primary": ChoiceA("set_brightness", 0.95, {}), "area_primary": ChoiceA("Kitchen", 0.95, {}),
            "timing_kind": ChoiceA(FOR_DURATION, 0.9, {}),
        },
        {
            "target:set_brightness": ChoiceA("Kitchen ceiling (Kitchen)", 0.9, {}),
            "param_value:set_brightness": ChoiceA("50%", 0.9, {}),
            "duration:0": ChoiceA(NOT_DURATION, 0.9, {}),
            "duration:1": ChoiceA(MINUTES, 0.9, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(home, "Kitchen ceiling to 50% for 10 minutes")
    assert isinstance(r, Escalate) and r.reason == "timing"
    assert "timing:not_invertible:set_brightness" in r.trace.notes


async def test_timed_query_and_missing_number_hand_off_before_round_two(home, vocab, config):
    client, calls = _scripted(
        {"verb:query_state": NoulA(0.95), "area:kitchen": NoulA(0.95), "timing_kind": ChoiceA(DELAYED, 0.9, {})}
    )
    r = await Engine(client, vocab, config).decide(home, "how warm is the kitchen in ten minutes")
    assert isinstance(r, Escalate) and r.reason == "timing" and calls["n"] == 1
    client, calls = _scripted({**R1_KITCHEN_ON, "timing_kind": ChoiceA(DELAYED, 0.9, {})})
    r = await Engine(client, vocab, config).decide(home, "Kitchen ceiling on later")
    assert isinstance(r, Escalate) and r.reason == "timing" and calls["n"] == 1
    assert "timing:no_duration" in r.trace.notes


async def test_timing_with_a_condition_hands_off(home, vocab, config):
    client, _ = _scripted(
        {
            **R1_KITCHEN_ON, "timing_kind": ChoiceA(DELAYED, 0.9, {}),
            "flag:has_condition": NoulA(0.9), "condition_domain": ChoiceA("switch", 0.9, {}),
        },
        {
            "target:turn_on": ChoiceA("Kitchen ceiling (Kitchen)", 0.9, {}),
            "duration:0": ChoiceA(MINUTES, 0.9, {}),
            "cond_subject": ChoiceA("Fridge (Kitchen)", 0.9, {}),
            "cond_state": ChoiceA("off", 0.9, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(home, "Kitchen ceiling on in 5 minutes if the fridge is off")
    assert isinstance(r, Escalate) and r.reason == "timing" and "timing:with_condition" in r.trace.notes
```

Check the fixture's option labels (`target_options` produce labels like `"Kitchen ceiling (Kitchen)"` — verify against an existing test in `test_engine.py` and adjust). Check `DOMAIN_STATES["switch"]` includes `"off"` for the condition test; if the fixture has no switch condition path, use `binary_sensor`/an existing condition test's ids.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: `round2.py`**

- `Round2Plan`: append `timing_kind: str | None = None` and `duration_literals: tuple[DurationLiteral, ...] = ()` at the END.
- `plan_round2(..., timing_kind: str | None = None)`: compute `dur = duration_literals(prompt) if timing_kind else ()` and pass `timing_kind, dur` as the last two positional args of `Round2Plan(...)`.
- `build_round2_questions`: at the end `qs.update(duration_questions(plan.duration_literals, pb))`.
- `build_round2_state`: `if plan.duration_literals: state["duration_literals"] = [l.text for l in plan.duration_literals]` before `candidates`.
- Imports: `from hunch.timing import DurationLiteral, duration_literals, duration_questions`.

- [ ] **Step 4: `resolver.py`**

After the condition block and before `if exception_unresolved:`:

```python
    timing: Timing | None = None
    if plan.timing_kind is not None:
        seconds, confs = sum_duration(round2, plan.duration_literals, trace)
        contributions.extend(confs)
        if seconds is None:
            return Escalate("timing", tuple(actions), trace)
        if condition is not None:
            trace.note("timing:with_condition")
            return Escalate("timing", tuple(actions), trace)
        if plan.timing_kind == "for_duration":
            for a in actions:
                if a.verb.name not in INVERSES:
                    trace.note(f"timing:not_invertible:{a.verb.name}")
                    return Escalate("timing", tuple(actions), trace)
        timing = Timing(plan.timing_kind, seconds)
```

Pass `timing=timing` into every `Resolved(...)`, `NeedsConfirmation(...)` and the post-Round-2 `NeedsClarification(...)` return at the end of `resolve`. Imports: `Timing` from `hunch.resolution`, `INVERSES` from `hunch.vocabulary`, `sum_duration` from `hunch.timing`.

- [ ] **Step 5: `engine.py`**

- After `if not shape.fired_verbs: return Escalate("no_intent", ...)`:

```python
        if timing_kind is not None:
            if any(v.is_query for v in shape.fired_verbs):
                trace.note("timing:query")
                return Escalate("timing", (), trace)
            if not duration_literals(prompt):
                trace.note("timing:no_duration")
                return Escalate("timing", (), trace)
```

- Both places that return `NeedsClarification` before Round 2 (the `DeviceRound` budget branch and the `if not per_verb: if pending_clarify is not None:` branch): when `timing_kind is not None`, `trace.note("timing:clarify_before_duration")` and return `Escalate("timing", (), trace)` instead.
- `plan_round2(..., frozenset(prefer_pick), timing_kind)` (add the argument).
- Remove the placeholder note from Task 3 if it was added.

- [ ] **Step 6: Run** `uv run pytest -q` + lint. Update `test_round2.py` if a test constructs `Round2Plan` positionally past the old last field (it should not).

- [ ] **Step 7: Commit** `"Timed device actions: 'für' (do now, undo after) and 'in' (do later) carry a Timing; non-invertible, timed queries and timed conditions hand off"` (+ trailer).

---

### Task 5: Engine version, golden runner and corpus rows

**Files:**
- Modify: `packages/hunch/pyproject.toml` (version `0.6.0`)
- Modify: `golden/run_golden.py`, `golden/corpus_julian.yaml`, `golden/corpus.yaml`

**Interfaces:**
- Consumes: `Resolved.timer/.timing`, `ActiveTimer`.
- Produces: corpus rows with `timers:` inputs and `timer:` / `timing:` expectations.

- [ ] **Step 1: Runner** (`run_golden.py`)

In `check(row, r)` add:

```python
    if "timing" in exp:
        t = getattr(r, "timing", None)
        lo, hi = exp["timing"].get("seconds", (0, float("inf")))
        if t is None or t.kind != exp["timing"]["kind"] or not lo <= t.seconds <= hi:
            problems.append(f"timing {t} != {exp['timing']}")
    if "timer" in exp:
        t = getattr(r, "timer", None)
        want = exp["timer"]
        if t is None or t.kind != want["kind"]:
            problems.append(f"timer {t} != {want}")
        else:
            if "seconds" in want and not want["seconds"][0] <= (t.duration_seconds or -1) <= want["seconds"][1]:
                problems.append(f"timer seconds {t.duration_seconds} not in {want['seconds']}")
            if "label" in want and t.label != want["label"]:
                problems.append(f"timer label {t.label!r} != {want['label']!r}")
            if "count" in want and len(t.timers) != want["count"]:
                problems.append(f"timer count {len(t.timers)} != {want['count']}")
```

In `main`, build `timers = tuple(ActiveTimer(f"t{i}", t.get("label"), float(t["remaining_seconds"]), t.get("kind", "timer"), t.get("description")) for i, t in enumerate(row.get("timers", [])))` and pass `timers=timers` to the final `engine.decide(...)`. Import `ActiveTimer` from `hunch`.

- [ ] **Step 2: Corpus rows**

In `golden/corpus_julian.yaml` change the "Mach das Licht in zehn Minuten aus" row to:

```yaml
- prompt: Mach das Licht in zehn Minuten aus
  # "Licht" over the whole home: a sweep confirm, a pick or a clarification are all fine — the delay must be understood
  expect: {kinds: [resolved, confirm, clarify], timing: {kind: delayed, seconds: [600, 600]}}
```

Do the same for the `timing` row in `golden/corpus.yaml` (English fixture home; keep `kinds` wide).

Append to `golden/corpus_julian.yaml`:

```yaml
# ---- timers and timed actions (2026-09-24) ----
- prompt: Timer 8 Minuten
  expect: {kind: resolved, timer: {kind: start, seconds: [480, 480], label: null}}
- prompt: Stell einen Timer für die Nudeln auf 8 Minuten
  expect: {kind: resolved, timer: {kind: start, seconds: [480, 480], label: Nudeln}}
- prompt: Timer für die Nudeln, 8 Minuten
  expect: {kind: resolved, timer: {kind: start, seconds: [480, 480], label: Nudeln}}
- prompt: Eine Viertelstunde Timer
  expect: {kind: resolved, timer: {kind: start, seconds: [900, 900]}}
- prompt: Timer eine halbe Stunde
  expect: {kind: resolved, timer: {kind: start, seconds: [1800, 1800]}}
- prompt: Timer 1 Stunde 20
  # "20" alone may be read as minutes (4800) or rejected (3600); both are honest
  expect: {kind: resolved, timer: {kind: start, seconds: [3600, 4800]}}
- prompt: Wie lange läuft der Timer für die Nudeln noch?
  timers: [{label: Nudeln, remaining_seconds: 200}, {label: Reis, remaining_seconds: 600}]
  expect: {kind: resolved, timer: {kind: remaining, count: 1}}
- prompt: Wie lange noch?
  timers: [{label: Nudeln, remaining_seconds: 200}]
  expect: {kind: resolved, timer: {kind: remaining, count: 1}}
- prompt: Timer abbrechen
  timers: [{label: Nudeln, remaining_seconds: 200}]
  expect: {kind: resolved, timer: {kind: cancel, count: 1}}
- prompt: Timer abbrechen
  timers: [{label: Nudeln, remaining_seconds: 200}, {label: Reis, remaining_seconds: 600}]
  # two timers, none named: ask which — or, if Jev reads "abbrechen" as all, cancel both
  expect: {kinds: [clarify, resolved]}
- prompt: Alle Timer abbrechen
  timers: [{label: Nudeln, remaining_seconds: 200}, {label: Reis, remaining_seconds: 600}]
  expect: {kind: resolved, timer: {kind: cancel, count: 2}}
- prompt: Wandlampe im Vorzimmer für 15 Minuten an
  expect: {kind: resolved, verb: turn_on, targets: [light.vorzimmer_wandlampe], timing: {kind: for_duration, seconds: [900, 900]}}
- prompt: Wandlampe im Vorzimmer in 15 Minuten aus
  expect: {kind: resolved, verb: turn_off, targets: [light.vorzimmer_wandlampe], timing: {kind: delayed, seconds: [900, 900]}}
- prompt: Rollo in der Küche für 10 Minuten runter
  expect: {kind: resolved, verb: close, targets: [cover.kuche_fenster_rollo], timing: {kind: for_duration, seconds: [600, 600]}}
- prompt: Rollo in der Küche auf 20% für 10 Minuten
  expect: {kind: escalate, reason: timing}
- prompt: Mach das Licht um 18 Uhr aus
  expect: {kind: escalate, reason: timing}
- prompt: Wie warm ist es in 10 Minuten?
  expect: {kind: escalate}
```

- [ ] **Step 3: Version** `packages/hunch/pyproject.toml` → `version = "0.6.0"`.

- [ ] **Step 4: Run** `uv run pytest -q`, lint, then the golden corpus against the real home (the API key is in `.env`, the export in `golden/homes/julian.json`; both exist on this machine):

```bash
uv run python golden/run_golden.py --home golden/homes/julian.json --corpus golden/corpus_julian.yaml --only Timer --verbose
uv run python golden/run_golden.py --home golden/homes/julian.json --corpus golden/corpus_julian.yaml
```

Expected: the new rows pass; if a row fails, report the Jev answers verbatim in the task report (do NOT change thresholds; a wording change to a description is allowed only when the report shows the answer was wrong for a describable reason — "measure before rewording").

- [ ] **Step 5: Commit** `"Golden runner knows timers and timing; 17 timer rows for Julian's home; engine 0.6.0"` (+ trailer).

---

### Task 6: `TimerStore` and firing (integration)

**Files:**
- Create: `custom_components/hunch/timers.py`
- Modify: `custom_components/hunch/const.py`
- Test: `tests/integration/test_timers.py`

**Interfaces:**
- Produces: `StoredAction`, `HunchTimer`, `TimerStore(hass, on_fire)`, `inverse_actions(actions, ok_ids) -> tuple[Action, ...]`, `stored_actions(actions) -> tuple[StoredAction, ...]`, `async_fire_timer(hass, entry, timer, overdue)`; constants `OPT_TIMER_SCRIPT = "timer_script"`, `EVENT_TIMER_FINISHED = "hunch_timer_finished"`, `TIMER_STORE_KEY = "hunch.timers"`, `TIMER_STORE_VERSION = 1`.

- [ ] **Step 1: Failing tests** (`tests/integration/test_timers.py`)

```python
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hunch.const import TIMER_STORE_KEY
from custom_components.hunch.timers import HunchTimer, StoredAction, TimerStore


def _timer(tid="t1", seconds=480.0, kind="timer", label="Nudeln", actions=()):
    return HunchTimer(
        timer_id=tid,
        kind=kind,
        label=label,
        description=None,
        duration_seconds=seconds,
        due_at=dt_util.utcnow() + timedelta(seconds=seconds),
        actions=tuple(actions),
        language="de",
        conversation_id="c1",
        device_id=None,
        satellite_id=None,
        area_id=None,
    )


async def test_add_fires_once_due_and_saves(hass: HomeAssistant, hass_storage, freezer):
    fired = AsyncMock()
    store = TimerStore(hass, fired)
    await store.async_load()
    await store.async_add(_timer())
    assert len(store.active()) == 1 and 479 <= store.remaining(store.active()[0]) <= 480
    assert hass_storage[TIMER_STORE_KEY]["data"]["timers"][0]["label"] == "Nudeln"
    freezer.tick(timedelta(seconds=481))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    fired.assert_awaited_once()
    timer, overdue = fired.await_args.args
    assert timer.timer_id == "t1" and overdue is False
    assert store.active() == () and hass_storage[TIMER_STORE_KEY]["data"]["timers"] == []


async def test_cancel_disarms(hass: HomeAssistant, hass_storage, freezer):
    fired = AsyncMock()
    store = TimerStore(hass, fired)
    await store.async_load()
    await store.async_add(_timer())
    assert (await store.async_cancel("t1")).label == "Nudeln"
    assert await store.async_cancel("t1") is None
    freezer.tick(timedelta(seconds=500))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    fired.assert_not_awaited()


async def test_load_rearms_and_fires_overdue_timers(hass: HomeAssistant, hass_storage, freezer):
    due = dt_util.utcnow() - timedelta(seconds=30)
    later = dt_util.utcnow() + timedelta(seconds=300)
    hass_storage[TIMER_STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": TIMER_STORE_KEY,
        "data": {
            "timers": [
                {
                    "timer_id": "old", "kind": "timer", "label": "Tee", "description": None,
                    "duration_seconds": 60, "due_at": due.isoformat(), "actions": [],
                    "language": "de", "conversation_id": None, "device_id": None,
                    "satellite_id": None, "area_id": None,
                },
                {
                    "timer_id": "new", "kind": "delayed", "label": None, "description": "Wandlampe aus",
                    "duration_seconds": 300, "due_at": later.isoformat(),
                    "actions": [{"verb": "turn_off", "entity_ids": ["light.a"], "params": {}}],
                    "language": "de", "conversation_id": None, "device_id": None,
                    "satellite_id": None, "area_id": None,
                },
            ]
        },
    }
    fired = AsyncMock()
    store = TimerStore(hass, fired)
    await store.async_load()
    await hass.async_block_till_done()
    fired.assert_awaited_once()
    timer, overdue = fired.await_args.args
    assert timer.timer_id == "old" and overdue is True
    active = store.active()
    assert [t.timer_id for t in active] == ["new"]
    assert active[0].actions == (StoredAction("turn_off", ("light.a",), {}),)
    a = store.as_active_timers()
    assert a[0].description == "Wandlampe aus" and a[0].kind == "delayed" and 299 <= a[0].remaining_seconds <= 300
```

- [ ] **Step 2: Run** `uv run --group ha --no-group dev pytest tests/integration/test_timers.py -q` → ImportError.

- [ ] **Step 3: Constants** (`const.py`)

```python
OPT_TIMER_SCRIPT = "timer_script"
EVENT_TIMER_FINISHED = "hunch_timer_finished"
TIMER_STORE_KEY = f"{DOMAIN}.timers"
TIMER_STORE_VERSION = 1
```

- [ ] **Step 4: `timers.py`**

```python
"""Hunch-managed timers: kitchen timers, and the scheduled half of "für 15 Minuten" / "in 15
Minuten". Persisted in a HA Store, armed with the HA clock, announced with one event."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from hunch import DEFAULT_VOCABULARY, Action, ActiveTimer, INVERSES

from .const import EVENT_TIMER_FINISHED, TIMER_STORE_KEY, TIMER_STORE_VERSION

_LOGGER = logging.getLogger(__name__)

OnFire = Callable[["HunchTimer", bool], Awaitable[None]]


@dataclass(frozen=True)
class StoredAction:
    verb: str
    entity_ids: tuple[str, ...]
    params: Mapping[str, float | str]


@dataclass(frozen=True)
class HunchTimer:
    timer_id: str
    kind: str  # "timer" | "revert" | "delayed"
    label: str | None
    description: str | None
    duration_seconds: float
    due_at: datetime  # aware UTC
    actions: tuple[StoredAction, ...]
    language: str
    conversation_id: str | None
    device_id: str | None
    satellite_id: str | None
    area_id: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["due_at"] = self.due_at.isoformat()
        d["actions"] = [
            {"verb": a.verb, "entity_ids": list(a.entity_ids), "params": dict(a.params)}
            for a in self.actions
        ]
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> HunchTimer:
        due = dt_util.parse_datetime(d["due_at"])
        if due is None:
            raise ValueError(f"bad due_at {d['due_at']!r}")
        return cls(
            timer_id=d["timer_id"],
            kind=d["kind"],
            label=d.get("label"),
            description=d.get("description"),
            duration_seconds=float(d["duration_seconds"]),
            due_at=dt_util.as_utc(due),
            actions=tuple(
                StoredAction(a["verb"], tuple(a["entity_ids"]), dict(a.get("params", {})))
                for a in d.get("actions", [])
            ),
            language=d.get("language", "en"),
            conversation_id=d.get("conversation_id"),
            device_id=d.get("device_id"),
            satellite_id=d.get("satellite_id"),
            area_id=d.get("area_id"),
        )


def new_timer_id() -> str:
    return uuid4().hex


def stored_actions(actions: Sequence[Action]) -> tuple[StoredAction, ...]:
    return tuple(
        StoredAction(a.verb.name, tuple(e.entity_id for e in a.targets), dict(a.params))
        for a in actions
    )


def inverse_actions(actions: Sequence[Action], ok_ids: set[str]) -> tuple[Action, ...]:
    """The undo of "für": each action's inverse verb on the targets that were actually
    changed, without values (an inverse has none)."""
    out = []
    for a in actions:
        inverse = INVERSES.get(a.verb.name)
        targets = tuple(e for e in a.targets if e.entity_id in ok_ids)
        if inverse and targets:
            out.append(Action(DEFAULT_VOCABULARY.by_name(inverse), targets, {}))
    return tuple(out)


class TimerStore:
    def __init__(self, hass: HomeAssistant, on_fire: OnFire) -> None:
        self._hass = hass
        self._on_fire = on_fire
        self._store: Store[dict[str, Any]] = Store(hass, TIMER_STORE_VERSION, TIMER_STORE_KEY)
        self._timers: dict[str, HunchTimer] = {}
        self._unsub: dict[str, CALLBACK_TYPE] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        overdue: list[HunchTimer] = []
        for raw in data.get("timers", []):
            try:
                timer = HunchTimer.from_dict(raw)
            except (KeyError, ValueError, TypeError):
                _LOGGER.warning("Dropping unreadable timer %s", raw.get("timer_id"))
                continue
            self._timers[timer.timer_id] = timer
            if timer.due_at <= dt_util.utcnow():
                overdue.append(timer)
            else:
                self._arm(timer)
        for timer in overdue:
            self._hass.async_create_task(self._fire(timer.timer_id, True))

    def active(self) -> tuple[HunchTimer, ...]:
        return tuple(sorted(self._timers.values(), key=lambda t: t.due_at))

    def remaining(self, timer: HunchTimer) -> float:
        return max(0.0, (timer.due_at - dt_util.utcnow()).total_seconds())

    def as_active_timers(self) -> tuple[ActiveTimer, ...]:
        return tuple(
            ActiveTimer(t.timer_id, t.label, self.remaining(t), t.kind, t.description)
            for t in self.active()
        )

    async def async_add(self, timer: HunchTimer) -> None:
        self._timers[timer.timer_id] = timer
        self._arm(timer)
        await self._save()

    async def async_cancel(self, timer_id: str) -> HunchTimer | None:
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return None
        if unsub := self._unsub.pop(timer_id, None):
            unsub()
        await self._save()
        return timer

    async def async_stop(self) -> None:
        for unsub in self._unsub.values():
            unsub()
        self._unsub.clear()

    def _arm(self, timer: HunchTimer) -> None:
        def _due(_now: datetime) -> None:
            self._unsub.pop(timer.timer_id, None)
            self._hass.async_create_task(self._fire(timer.timer_id, False))

        self._unsub[timer.timer_id] = async_track_point_in_utc_time(self._hass, _due, timer.due_at)

    async def _fire(self, timer_id: str, overdue: bool) -> None:
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return
        await self._save()
        try:
            await self._on_fire(timer, overdue)
        except Exception:  # noqa: BLE001 - a broken announcement must not kill the store
            _LOGGER.exception("Timer %s failed to fire", timer_id)

    async def _save(self) -> None:
        await self._store.async_save({"timers": [t.to_dict() for t in self.active()]})


async def async_fire_timer(hass: HomeAssistant, entry: ConfigEntry, timer: HunchTimer, overdue: bool) -> None:
    """What a due timer does: run its stored actions (revert / delayed), fire the event, call the
    configured script. Import here is late to avoid a cycle with `__init__`."""
    from .executor import Executor

    rt = entry.runtime_data
    executed: list[str] = []
    failed: list[str] = []
    if timer.actions:
        home = rt.builder.build()
        actions = []
        for sa in timer.actions:
            targets = tuple(e for i in sa.entity_ids if (e := home.entity_by_id(i)) is not None)
            missing = set(sa.entity_ids) - {e.entity_id for e in targets}
            if missing:
                _LOGGER.warning("Timer %s: entities gone: %s", timer.timer_id, sorted(missing))
            if targets:
                actions.append(Action(DEFAULT_VOCABULARY.by_name(sa.verb), targets, dict(sa.params)))
        results = await Executor(hass).execute(actions, Context())
        executed = [r.entity_id for r in results if r.ok]
        failed = [r.entity_id for r in results if not r.ok]
    data = {
        **{k: v for k, v in timer.to_dict().items() if k != "actions"},
        "overdue": overdue,
        "executed": executed,
        "failed": failed,
    }
    hass.bus.async_fire(EVENT_TIMER_FINISHED, data)
    if rt.timer_script:
        try:
            await hass.services.async_call(
                "script", "turn_on", {"entity_id": rt.timer_script, "variables": data}, blocking=False
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Timer script %s failed: %s", rt.timer_script, err)
```

Check `HomeModel` has `entity_by_id` (it is used in `test_home_model_snapshot.py`); check `Store` generic typing works on 2026.9 (else drop the subscript). `replace` import unused → remove.

- [ ] **Step 5: Run** the three tests + lint. Fix `freezer` interplay: `async_track_point_in_utc_time` fires when `async_fire_time_changed(hass)` runs with the frozen clock past `due_at`.

- [ ] **Step 6: Commit** `"TimerStore: persisted, clock-armed timers with an on-fire callback; firing runs stored actions, fires hunch_timer_finished, calls the timer script"` (+ trailer).

---

### Task 7: Runtime, options flow, translations, diagnostics

**Files:**
- Modify: `custom_components/hunch/__init__.py`, `config_flow.py`, `strings.json`, `translations/en.json`, `translations/de.json`, `diagnostics.py`
- Test: `tests/integration/test_options_flow.py`, `tests/integration/test_diagnostics.py`, `tests/integration/test_init.py`

**Interfaces:**
- Consumes: Task 6.
- Produces: `HunchRuntime.timers: TimerStore`, `HunchRuntime.timer_script: str | None`; option `timer_script`; diagnostics `"timers"`.

- [ ] **Step 1: Failing tests**

`test_options_flow.py`: in the successful `async_configure` payload add `"timer_script": "script.timer_ansage"` and assert `entry.options["timer_script"] == "script.timer_ansage"` and, after reload, `entry.runtime_data.timer_script == "script.timer_ansage"`.

`test_diagnostics.py`: add

```python
async def test_diagnostics_list_active_timers(hass: HomeAssistant, setup_hunch):
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    from custom_components.hunch.timers import HunchTimer

    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    await entry.runtime_data.timers.async_add(
        HunchTimer("t", "timer", "Nudeln", None, 480, dt_util.utcnow() + timedelta(seconds=480), (), "de", None, None, None, None)
    )
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["timers"][0]["label"] == "Nudeln" and 479 <= diag["timers"][0]["remaining_seconds"] <= 480
```

`test_init.py`: add a test that unloading the entry leaves the store file intact: add a timer, unload, assert `hass_storage[TIMER_STORE_KEY]["data"]["timers"]` still has one entry.

- [ ] **Step 2: Implement**

`__init__.py`: `HunchRuntime` gains `timers: TimerStore` and `timer_script: str | None` (append). In `async_setup_entry`, before `entry.runtime_data = ...`:

```python
    async def _on_fire(timer, overdue):
        await async_fire_timer(hass, entry, timer, overdue)

    timers = TimerStore(hass, _on_fire)
```

then include `timers=timers, timer_script=entry.options.get(OPT_TIMER_SCRIPT) or None` in the runtime; after assigning `runtime_data`: `await timers.async_load()`; `entry.async_on_unload(timers.async_stop)` — note `async_stop` is a coroutine function; `async_on_unload` accepts callables returning a coroutine in 2026.9 (verify; if not, wrap in a callback that schedules it).

`config_flow.py`: `vol.Optional(OPT_TIMER_SCRIPT): EntitySelector(EntitySelectorConfig(domain="script"))` after the fallback agent field; import from `homeassistant.helpers.selector`.

`strings.json` / `en.json`: `"timer_script": "Script to run when a timer finishes (gets name, duration, device as variables)"`; `de.json`: `"timer_script": "Skript beim Ablauf eines Timers (bekommt Name, Dauer, Gerät als Variablen)"`.

`diagnostics.py`: add

```python
        "timers": [
            {
                "kind": t.kind,
                "label": t.label,
                "description": t.description,
                "remaining_seconds": round(rt.timers.remaining(t)),
            }
            for t in rt.timers.active()
        ],
```

- [ ] **Step 3: Run** the HA suite + lint. **Step 4: Commit** `"Runtime owns the TimerStore; timer_script option; timers in diagnostics"` (+ trailer).

---

### Task 8: Responder and pending types

**Files:**
- Modify: `custom_components/hunch/responder.py`, `custom_components/hunch/pending.py`
- Test: `tests/unit/test_responder.py`, `tests/unit/test_pending.py`

**Interfaces:**
- Produces: `format_duration(seconds, lang) -> str`, `compact_duration(seconds, lang) -> str`, `timer_name(label, description, duration_seconds, lang) -> str`, `revert_words(verb_names, lang) -> str`, `REVERT_WORDS`, new `OUTCOMES`/`TEMPLATES` keys `timer_started, timer_none, timer_remaining, timer_cancelled, which_timer, delayed_scheduled, for_duration_done`; `render` handles them; `PendingConfirm.timing`, `PendingClarify.timing` (defaulted, END), `PendingTimerPick(timers, labels, question, created)`; `PendingStore` accepts it.

- [ ] **Step 1: Failing tests** (`test_responder.py` append)

```python
from custom_components.hunch.responder import compact_duration, format_duration, revert_words, timer_name


def test_format_duration_in_both_languages():
    assert format_duration(480, "de") == "8 Minuten"
    assert format_duration(4800, "de") == "1 Stunde 20 Minuten"
    assert format_duration(200, "de") == "3 Minuten 20 Sekunden"
    assert format_duration(1, "de") == "1 Sekunde"
    assert format_duration(60, "en") == "1 minute"
    assert format_duration(3661, "en") == "1 hour 1 minute 1 second"
    assert compact_duration(480, "de") == "8-Minuten" and compact_duration(480, "en") == "8-minute"


def test_timer_name():
    assert timer_name("Nudeln", None, 480, "de") == "Timer für Nudeln"
    assert timer_name(None, "Wandlampe aus", 900, "de") == "Wandlampe aus"
    assert timer_name(None, None, 480, "de") == "8-Minuten-Timer"
    assert timer_name(None, None, 480, "en") == "8-minute timer"
    assert timer_name("pasta", None, 480, "en") == "timer for pasta"


def test_timer_templates_render():
    assert render("timer_started", "de", name="Timer für Nudeln", duration="8 Minuten") == "Timer für Nudeln gestellt, 8 Minuten."
    assert render("timer_none", "de") == "Es läuft kein Timer."
    assert render("timer_remaining", "de", lines=["Timer für Nudeln: noch 3 Minuten 20 Sekunden."]) == "Timer für Nudeln: noch 3 Minuten 20 Sekunden."
    assert render("timer_cancelled", "de", names=["Timer für Nudeln", "Timer für Reis"]) == "Abgebrochen: Timer für Nudeln, Timer für Reis."
    assert render("which_timer", "de", options=["Nudeln (3:20 left)", "Reis (10:00 left)"]).startswith("Welchen Timer meinst du: ")
    assert render("delayed_scheduled", "de", duration="15 Minuten", body="Wandlampe (Vorzimmer) ausschalten") == "In 15 Minuten: Wandlampe (Vorzimmer) ausschalten."
    assert render("for_duration_done", "de", body="Wandlampe (Vorzimmer) eingeschaltet", duration="15 Minuten", revert="aus") == "Erledigt: Wandlampe (Vorzimmer) eingeschaltet, in 15 Minuten wieder aus."
    assert render("for_duration_done", "en", body="turned on Wall lamp", duration="15 minutes", revert="off") == "Done: turned on Wall lamp, off again in 15 minutes."
    assert revert_words(["turn_on", "close"], "de") == "aus, auf" and revert_words(["turn_on"], "en") == "off"
```

`test_pending.py`: `PendingConfirm((), None, "q", 0.0).timing is None`; `PendingTimerPick` stored and taken back from `PendingStore`.

- [ ] **Step 2: Implement** (`responder.py`)

Add to `OUTCOMES` and both `TEMPLATES` dicts (spec §5.5 table). Add:

```python
DURATION_UNITS = {
    "en": (("hour", "hours"), ("minute", "minutes"), ("second", "seconds")),
    "de": (("Stunde", "Stunden"), ("Minute", "Minuten"), ("Sekunde", "Sekunden")),
}


def _duration_parts(seconds: float) -> list[tuple[int, int]]:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return [(i, n) for i, n in enumerate((h, m, s)) if n]


def format_duration(seconds: float, language: str) -> str:
    units = DURATION_UNITS.get(language, DURATION_UNITS["en"])
    parts = _duration_parts(seconds) or [(2, 0)]
    return " ".join(f"{n} {units[i][0] if n == 1 else units[i][1]}" for i, n in parts)


def compact_duration(seconds: float, language: str) -> str:
    """For names: '8-Minuten' / '8-minute'; several parts joined with hyphens."""
    units = DURATION_UNITS.get(language, DURATION_UNITS["en"])
    parts = _duration_parts(seconds) or [(2, 0)]
    if language == "de":
        return "-".join(f"{n}-{units[i][0] if n == 1 else units[i][1]}" for i, n in parts)
    return "-".join(f"{n}-{units[i][0]}" for i, n in parts)


TIMER_NAME = {"en": ("timer for {label}", "{compact} timer"), "de": ("Timer für {label}", "{compact}-Timer")}


def timer_name(label, description, duration_seconds, language) -> str:
    tpl = TIMER_NAME.get(language, TIMER_NAME["en"])
    if label:
        return tpl[0].format(label=label)
    if description:
        return description
    return tpl[1].format(compact=compact_duration(duration_seconds, language))


REVERT_WORDS = {
    "en": {"turn_off": "off", "turn_on": "on", "close": "closed", "open": "open", "unlock": "unlocked", "lock": "locked", "media_pause": "paused", "media_play": "playing"},
    "de": {"turn_off": "aus", "turn_on": "an", "close": "zu", "open": "auf", "unlock": "aufgesperrt", "lock": "abgesperrt", "media_pause": "pausiert", "media_play": "weiter"},
}


def revert_words(verb_names, language) -> str:
    """What the devices will be again after 'für': the word for each action's INVERSE."""
    from hunch import INVERSES

    words = REVERT_WORDS.get(language, REVERT_WORDS["en"])
    seen = list(dict.fromkeys(words[INVERSES[v]] for v in verb_names if v in INVERSES))
    return ", ".join(seen)
```

In `render`: `timer_remaining` joins `lines` with "\n" (like `query_answer`); `timer_cancelled` joins `names` with ", "; `which_timer` joins `options` with ", "; the others format slots directly.

`pending.py`: append `timing: Timing | None = None` to `PendingConfirm` and `PendingClarify`; add

```python
@dataclass(frozen=True)
class PendingTimerPick:
    timers: tuple[ActiveTimer, ...]
    labels: tuple[str, ...]  # timer_option(t) per timer, plus ALL_TIMERS last
    question: str
    created: float
```

and widen the `PendingStore` type hints to the three-way union.

- [ ] **Step 3: Run** `uv run pytest -q` + lint. **Step 4: Commit** `"Responder speaks durations, timer names and timed-action sentences; pending turns carry timing; PendingTimerPick"` (+ trailer).

---

### Task 9: Conversation entity: timer commands and timed plans

**Files:**
- Modify: `custom_components/hunch/conversation.py`, `custom_components/hunch/manifest.json`
- Test: `tests/integration/test_conversation.py`

**Interfaces:**
- Consumes: Tasks 3–8.
- Produces: the user-visible behaviour of spec §5.4; manifest `0.4.0`, `hunch-engine==0.6.0`.

- [ ] **Step 1: Failing tests** (append to `test_conversation.py`; reuse `_home`, `_say`, `_speech`, `scripted`, `R1_TURN_OFF_KITCHEN`; `_home` has `light.kuche_spots` on and `light.kuche_kucheninsel` off)

```python
from datetime import timedelta
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed
from hunch.timing import ALL_TIMERS, DELAYED, FOR_DURATION, MINUTES, TIMER_CANCEL, TIMER_REMAINING, TIMER_START
from custom_components.hunch.const import EVENT_TIMER_FINISHED


async def test_timer_start_is_stored_and_spoken(hass, setup_hunch):
    await _home(hass)
    client, calls = scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.9, {})},
        {"duration:0": ChoiceA(MINUTES, 0.9, {}), "timer_label": ChoiceA("Nudeln", 0.9, {})},
    )
    entry, _ = await setup_hunch(client, calls)
    result = await _say(hass, "Timer für die Nudeln 8 Minuten", conversation_id="t1")
    assert _speech(result) == "Timer für Nudeln gestellt, 8 Minuten."
    active = entry.runtime_data.timers.active()
    assert len(active) == 1 and active[0].label == "Nudeln" and active[0].kind == "timer"
    assert active[0].conversation_id == "t1" and active[0].language == "de"
    assert result.response.speech["plain"]["extra_data"]["hunch"]["outcome"] == "Resolved"


async def test_timer_fires_event_and_script(hass, setup_hunch, freezer):
    await _home(hass)
    client, calls = scripted(
        {"timing_kind": ChoiceA(TIMER_START, 0.9, {})}, {"duration:0": ChoiceA(MINUTES, 0.9, {})}
    )
    entry, _ = await setup_hunch(client, calls, options={"timer_script": "script.ansage"})
    events = async_capture_events(hass, EVENT_TIMER_FINISHED)
    script_calls = async_mock_service(hass, "script", "turn_on")
    await _say(hass, "Timer 2 Minuten")
    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(events) == 1 and events[0].data["duration_seconds"] == 120 and events[0].data["overdue"] is False
    assert events[0].data["label"] is None and events[0].data["kind"] == "timer"
    assert len(script_calls) == 1 and script_calls[0].data["entity_id"] == "script.ansage"
    assert script_calls[0].data["variables"]["duration_seconds"] == 120
    assert entry.runtime_data.timers.active() == ()


async def test_remaining_and_cancel_and_none(hass, setup_hunch):
    await _home(hass)
    client, calls = scripted({"timing_kind": ChoiceA(TIMER_REMAINING, 0.9, {})})
    entry, _ = await setup_hunch(client, calls)
    assert _speech(await _say(hass, "wie lange noch?")) == "Es läuft kein Timer."
    await entry.runtime_data.timers.async_add(HunchTimer("a", "timer", "Nudeln", None, 480, dt_util.utcnow() + timedelta(seconds=200), (), "de", None, None, None, None))
    speech = _speech(await _say(hass, "wie lange noch?"))
    assert speech.startswith("Timer für Nudeln: noch 3 Minuten")
    client2, calls2 = scripted({"timing_kind": ChoiceA(TIMER_CANCEL, 0.9, {})})
    entry.runtime_data.client = client2  # same engine object holds the client: patch via runtime
    ...
```

Write the cancel half as its own test with a fresh `setup_hunch(client_cancel, ...)` (a second entry is not possible — single instance — so instead script the ONE fake client to answer `timing_kind` from a mutable dict the test flips between turns: build `answers = {"timing_kind": ChoiceA(TIMER_REMAINING, 0.9, {})}`, pass `answers` to `scripted(answers)` and mutate `answers["timing_kind"]` between `_say` calls — `scripted` reads the dict by reference).

Further tests to write, same style:

- `test_two_timers_which_timer_clarification_cancels_the_pick`: two stored timers, Round 2 `timer_pick` hesitant (0.5) → speech starts with "Welchen Timer meinst du: "; `continue_conversation is True`; reply "den für die Nudeln" judged with `reply={"reply_pick": ChoiceA("Nudeln (3:20 left)", 0.9, {})}` → speech "Abgebrochen: Timer für Nudeln." and one timer left.
- `test_for_duration_turns_on_now_and_off_later`: Round 1 `{"verb:turn_on", "area:kuche", "domain:light", "verb_primary": turn_on, "area_primary": Küche, "timing_kind": FOR_DURATION}`, Round 2 `{"target:turn_on": <Kücheninsel label>, "duration:0": MINUTES}`; `async_mock_service(hass, "homeassistant", "turn_on")` and `"turn_off"`; say "Kücheninsel für 15 Minuten an" → one `turn_on` call now, speech "Erledigt: Kücheninsel (Küche) eingeschaltet, in 15 Minuten wieder aus."; stored timer kind `revert` with `StoredAction("turn_off", ("light.kuche_kucheninsel",), {})`; `freezer.tick(901)` + fire → one `turn_off` call on the same id, event with `executed == ["light.kuche_kucheninsel"]`.
- `test_delayed_turn_off_runs_nothing_now`: Round 1 `R1_TURN_OFF_KITCHEN` + `timing_kind: DELAYED`, Round 2 `{"duration:0": MINUTES}`; say "Licht in der Küche in 10 Minuten aus" → no `turn_off` call now, speech "In 10 Minuten: Spots (Küche), Kücheninsel (Küche) ausschalten."; after `freezer.tick(601)` one `turn_off` call with both ids.
- `test_confirmed_plan_keeps_its_timing`: force a confirmation (e.g. `flag:collective` with `max_silent_targets` option 1) with `timing_kind: DELAYED`; speech asks "Soll ich …?"; reply "ja" (`reply_confirm: affirmative`) → no service call now, a `delayed` timer stored.
- `test_timer_turn_is_not_remembered_for_follow_ups`: after "Timer 8 Minuten", `entry.runtime_data.last_turns.get(cid) is None`.

Use `async_mock_service` from `pytest_homeassistant_custom_component.common`; check how existing tests capture service calls in this file (`svc` list) and follow that.

- [ ] **Step 2: Run** to verify failure.

- [ ] **Step 3: Implement** (`conversation.py`)

- Imports: `ActiveTimer`, `Timing`, `TimerCommand` from `hunch`; `ALL_TIMERS`, `timer_option` from `hunch.timing`; `HunchTimer`, `inverse_actions`, `new_timer_id`, `stored_actions` from `.timers`; `PendingTimerPick` from `.pending`; `format_duration`, `revert_words`, `timer_name` from `.responder`; `dt_util`, `timedelta`, `device_registry as dr`.
- `_async_handle_message`: `result = await rt.engine.decide(home, user_input.text, previous, rt.timers.as_active_timers())`. `Resolved` branch: `if result.timer is not None: return await self._run_timer(turn, result.timer, result.trace)` else `_run(..., timing=result.timing)`. `NeedsConfirmation`: `PendingConfirm(result.actions, result.condition, question, now, result.timing)`; the confirm question gets a timing clause: append `" " + render_timing_hint` — keep simple: when `timing` is set, prefix the body with `format_duration` via two new `REASONS`-like phrases? Simplest honest form: build the confirm question from the normal clauses and append `condition`-slot text `", in {duration}"` (de) / `" in {duration}"` (en) for `delayed`, and `", für {duration}"` / `" for {duration}"` for `for_duration` — add `TIMING_CLAUSE = {"en": {"delayed": " in {d}", "for_duration": " for {d}"}, "de": {"delayed": " in {d}", "for_duration": " für {d}"}}` to `responder.py` and a helper `timing_clause(timing, lang)`; pass it through the `condition` slot (concatenated after the condition clause).
- `NeedsClarification`: `question_key == "which_timer"` → labels `tuple(timer_option(t) for t in result.timers) + (ALL_TIMERS,)`; question `render("which_timer", lang, options=labels[:-1])`; `rt.pending.put(cid, PendingTimerPick(result.timers, labels, question, now))`; `_result(..., "NeedsClarification", cont=True)`. Otherwise as before, with `PendingClarify(..., timing=result.timing)`.
- `_handle_reply`: `PendingTimerPick` → `ChoiceQ(CLARIFY_INSTRUCTIONS, (*pending.labels, NO_MATCH))`; on a sure pick: `ALL_TIMERS` → all timers, a label → that timer; cancel them via `_cancel_timers`; else escalate with `pending_context(question, "cancel one of the timers: …")`.
- `_handle_confirm_reply` → `_run(..., timing=pending.timing)`; `_handle_clarify_reply` → `_run(..., timing=pending.timing)` (also when it re-asks a `risk:confirm`, carry the timing into the new `PendingConfirm`).
- `_run(self, turn, home, actions, condition, trace, outcome, timing: Timing | None = None)`:
  - after the condition check and the queries/commands split, `if timing is not None and timing.kind == "delayed" and commands:` → `await rt.timers.async_add(self._make_timer(turn, "delayed", stored_actions(commands), description=self._action_clauses(commands, areas, lang, done=False), duration=timing.seconds))`; `self._remember(turn, actions)`; return `_result(turn, render("delayed_scheduled", lang, duration=format_duration(timing.seconds, lang), body=<same clauses>), trace, outcome)`.
  - after executing commands, when `timing is not None and timing.kind == "for_duration"`: `ok_ids = {r.entity_id for r in results if r.ok}`; `revert = inverse_actions(commands, ok_ids)`; if `revert`: add a `revert` timer with `stored_actions(revert)` and description `self._action_clauses(revert, areas, lang, done=False)`; text = `render("for_duration_done", lang, body=self._action_clauses(commands, areas, lang, done=True), duration=format_duration(timing.seconds, lang), revert=revert_words([a.verb.name for a in commands], lang))` (if some failed, keep the `execution_failed` text and append the for-duration sentence on a new line).
- `_make_timer(self, turn, kind, actions, *, label=None, description=None, duration) -> HunchTimer`: `device_id = turn.user_input.device_id`; `area_id = (dr.async_get(self.hass).async_get(device_id).area_id if device_id and dr.async_get(self.hass).async_get(device_id) else None)`; `due_at = dt_util.utcnow() + timedelta(seconds=duration)`; `satellite_id = getattr(turn.user_input, "satellite_id", None)`.
- `_run_timer(self, turn, command, trace)`:
  - `start`: `_make_timer(turn, "timer", (), label=command.label, duration=command.duration_seconds)` → add → `render("timer_started", lang, name=timer_name(label, None, dur, lang), duration=format_duration(dur, lang))`.
  - `remaining`: none → `timer_none`; else lines `f"{timer_name(...)}: noch {format_duration(remaining)}."` — build via a new template `timer_remaining_line` in `responder.py` (`"{name}: noch {remaining}."` / `"{name}: {remaining} left."`) and `render("timer_remaining", lang, lines=...)`. `remaining` per timer from `rt.timers.active()` matched by `timer_id` (the engine's `ActiveTimer.remaining_seconds` is a snapshot; re-read the store).
  - `cancel`: none → `timer_none`; else `await self._cancel_timers(turn, command.timers, trace)` → `render("timer_cancelled", lang, names=[...])`. A timer that vanished meanwhile (`async_cancel` returned None) is simply skipped.
  - Timer turns do not call `_remember`.
- `manifest.json`: `"version": "0.4.0"`, `"requirements": ["hunch-engine==0.6.0"]`.

- [ ] **Step 4: Run** the HA suite, the default suite, lint. **Step 5: Commit** `"Hunch sets, reads and cancels timers and schedules 'für'/'in' device actions itself (integration 0.4.0, engine 0.6.0)"` (+ trailer).

---

### Task 10: Docs

**Files:**
- Modify: `README.md`, `golden/README.md`, `docs/superpowers/specs/2026-09-19-hunch-design.md` (one addendum line pointing at the timers spec), `docs/superpowers/specs/2026-09-21-hunch-integration-design.md` (same)

- [ ] **Step 1:** README: under "What Hunch answers itself vs. hands off" add timers and "für/in" to the "itself" list, remove them from the hand-off list, and add a short "Timers" subsection: no helpers needed; event `hunch_timer_finished` with its fields; the `timer_script` option with an example script that speaks `{{ label or description or (duration_seconds // 60) ~ ' Minuten' }}` via `tts.speak`; what still hands off (clock times, sequences).
- [ ] **Step 2:** `golden/README.md` tuning log: a dated 2026-09-24 entry: what was added, the corpus result from Task 5 (numbers from the report), and any wording changes made with the measured reason.
- [ ] **Step 3:** One line each in both parent specs: "2026-09-24: timers and timed actions — see `2026-09-24-hunch-timers-design.md`."
- [ ] **Step 4:** Commit `"Docs: timers"` (+ trailer).

---

## Self-review notes

- Spec coverage: §4.1 → T1; §4.2 → T2 + T3 gating; §4.3 → T3; §4.4 → T4; §4.5 → T1; §4.6 notes spread over T3/T4; §5.1–5.2 → T6; §5.3 → T7; §5.4 → T9; §5.5 → T8 (+ `timing_clause`, `timer_remaining_line` added in T9's step 3 — implementers add them to `responder.py` with tests); §5.6 → T7; §6 → T5; §7 → each task's tests.
- Type consistency: `TimerCommand.kind` values `"start" | "cancel" | "remaining"` everywhere; `Timing.kind` `"for_duration" | "delayed"`; `HunchTimer.kind` `"timer" | "revert" | "delayed"`; `ActiveTimer.kind` mirrors `HunchTimer.kind`.
- Known judgment calls for the executor: `label_candidates` keeps function words (Jev filters); `timing.NO_MATCH` duplicates `round2.NO_MATCH` to avoid an import cycle (tested equal).
