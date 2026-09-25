# Hunch — timers and timed device actions (sub-project 2b) design

Date: 2026-09-24. Status: approved in chat (2026-09-24), written for the plan.
Parent specs: `2026-09-19-hunch-design.md` (engine), `2026-09-21-hunch-integration-design.md`
(integration). Terms and thresholds are theirs unless redefined here.

## 1. Summary

Today every request that mentions a delay, duration or timer escalates as `timing`, and the
fallback LLM cannot do timers either (HA's own Assist timers need a voice satellite with a timer
handler; the app, the browser and the Assist dialog have none). Julian's two asks:

1. **Timers** — as many as wanted, with any spoken duration, cancellable, readable: "Timer 8
   Minuten", "Stell einen Timer für die Nudeln auf 8 Minuten", "Wie lange läuft der Timer für
   die Nudeln noch?", "Timer abbrechen".
2. **Timed device actions** — "Wandlampe an für 15 Minuten" (do now, undo after) and
   "Wandlampe in 15 Minuten aus" (do later).

Hunch owns its timers: an in-integration `TimerStore` persisted in a HA `Store`, one scheduler,
one event on expiry (`hunch_timer_finished`) plus an optional script call. What happens when a
timer rings (which speaker says what) stays a HA automation the user writes. The engine stays
HA-free: it learns to *recognise and structure* timers and timing; the integration schedules.

Governing principle unchanged: **Jev thinks, code is stupid.** Every semantic decision (is this
a timer or a device action? which number is the duration, in which unit? what is the timer for?
which running timer is meant?) is a Jev question. Code lists candidates, looks up tables,
multiplies, schedules.

## 2. Goals and non-goals

Goals

- Start, cancel and read timers by voice without any HA helper entity or satellite.
- Time-limited device actions ("für 15 Minuten") and delayed device actions ("in 15 Minuten")
  for every verb that has an inverse (see §5.5).
- Timers survive a HA restart; those that expired during the downtime fire on startup.
- Every timer and every scheduled action is visible to the engine as an *active timer*, so
  "Timer abbrechen" can cancel a scheduled "Wandlampe aus" too.
- Same trace, `extra_data.hunch` mark and `agent_detail` visibility as every other Hunch turn.

Non-goals (v1)

- Clock times and dates ("um 18 Uhr", "morgen früh") → still `Escalate("timing")`.
- Sequences ("erst …, dann …"), "wenn es dunkel wird", "sobald …" → unchanged hand-off.
- Durations with no number Hunch can look up ("ein paar Minuten") → `Escalate("timing")`.
- Snooze, pause/resume of timers, timer entities in the dashboard, changing a running timer.
- Timed queries ("wie warm ist es in 10 Minuten") and timed conditional actions ("in 10 Minuten
  aus, wenn …") → `Escalate("timing")`.
- Restoring arbitrary previous state after "für": only invertible verbs; "Rollo auf 20% für
  10 Minuten" → `Escalate("timing")`. The revert covers only the targets whose state before
  the request differed from the state the command sets (code table `COMMANDED_STATE`): "Licht
  für 15 Minuten an" on a lamp that is already on leaves it on afterwards and stores no revert
  (addendum 2026-09-25, item 4). Nothing else (brightness, position) is restored.
- A clock time hidden inside a duration request ("um 18 Uhr für 10 Minuten") is not detected
  separately; Jev's `timing_kind` decides which reading wins.
- A second "für" on the same device stacks a second revert timer; nothing merges them.

## 3. Decisions taken in brainstorming (do not re-ask)

- No `timer.*` helper entities; no HA intent timers. Hunch-managed timers in a HA `Store`.
- Expiry = event `hunch_timer_finished` always, plus `script.turn_on` of a script chosen in the
  options flow when one is set. Without a script only the event fires.
- Timers that expired while HA was down fire immediately on startup (`overdue: true`).
- Unnamed timers are named after their duration in responses ("8-Minuten-Timer").
- "für" only for verbs with an inverse; the inverse table is code. `lock` has no inverse:
  "Tür für 10 Minuten absperren" would unlock a door unattended later, so it hands off;
  `unlock → lock` ("für 10 Minuten aufsperren", locks again) is kept.
- Engine result types grow fields; no new `Resolution` variant.
- Timing turns (timer commands, "für", "in") are not remembered for follow-ups: "und im
  Esszimmer" after "Licht in der Küche in 10 Minuten aus" must not run immediately.
- Scheduled actions run with the requesting user's context (`user_id` stored on the timer), so
  HA's permission checks apply as they did to the immediate half.
- Cancel always asks Jev which timer is meant when any timer runs (a single pending revert must
  not be cancelled by a bare "Timer abbrechen" meant for a kitchen timer that already rang);
  reading the remaining time never asks when only one timer runs.

## 4. Engine changes (`packages/hunch`, version 0.6.0)

### 4.1 New result types (`resolution.py`)

```python
@dataclass(frozen=True)
class ActiveTimer:
    """A timer the caller currently runs; input to `decide`, and what cancel/remaining name."""
    timer_id: str
    label: str | None            # what the timer is for ("Nudeln"); None when unnamed
    remaining_seconds: float
    kind: str                    # "timer" | "revert" | "delayed"
    description: str | None = None  # scheduled actions: what will happen ("Wandlampe aus")

@dataclass(frozen=True)
class Timing:
    """A device action bound to a duration."""
    kind: str      # "for_duration" (do now, invert after) | "delayed" (do after)
    seconds: float

@dataclass(frozen=True)
class TimerCommand:
    kind: str                                  # "start" | "cancel" | "remaining"
    duration_seconds: float | None = None      # start
    label: str | None = None                   # start
    timers: tuple[ActiveTimer, ...] = ()       # cancel / remaining: the timers meant
```

Existing variants gain fields (all defaulted, so existing construction sites keep working):

- `Resolved(actions, condition, confidence, trace, timing: Timing | None = None,
  timer: TimerCommand | None = None)`. A timer command comes with `actions == ()` and
  `condition is None`. `timing` and `timer` are never both set.
- `NeedsConfirmation(..., timing: Timing | None = None)`.
- `NeedsClarification(..., timing: Timing | None = None, timers: tuple[ActiveTimer, ...] = (),
  timer_kind: str | None = None)`; new `question_key` value `"which_timer"` (then `candidates
  == ()`, `verb is None`, `timers` non-empty, `timer_kind == "cancel"`).
- `Escalate` reason `timing` keeps its name; its meaning widens to "timing Hunch cannot carry
  out itself": clock time or date, sequence, no duration found, a `for_duration` on a verb
  without inverse, timing on a query or on a conditional action, duration out of bounds.

`Engine.decide(home, prompt, previous=None, timers: tuple[ActiveTimer, ...] = ())`.

### 4.2 Round 1: the timing comparison

`has_timing` (Noul) stays. New Choice `timing_kind`, always asked, with described options
(language-neutral ids in `round1.py`, wording in the phrasebooks EN + DE):

| id | meaning |
|---|---|
| `none` | no timing at all |
| `start a timer` | set a countdown / kitchen timer / alarm that controls no device |
| `cancel a timer` | stop or delete a running timer |
| `ask how long a timer has left` | ask a timer's remaining time |
| `device action for a duration` | do something now and undo it after a duration ("für 15 Minuten", "for ten minutes") |
| `device action after a delay` | do something after a delay ("in 15 Minuten", "in ten minutes", "nachher") |
| `at a clock time or date` | "um 18 Uhr", "morgen", "at 7" |
| `other timing` | sequences ("dann", "danach"), anything else time-bound |

Round 1 state gains `"timers": [{"label", "description", "remaining_seconds"}]` when the caller
passed active timers (so "wie lange noch?" can be judged with the knowledge that a timer runs).

`Shape` gains `timing_kind: str | None` (the Choice's pick) and `timing_conf: float`.

Engine gating, in this order, right after `interpret_round1`:

1. `timing_kind ∈ {start, cancel, remaining}` and `trace.decide("timing_kind", conf, th.flag)`
   → the **timer path** (§4.3). This runs *before* the `is_fragment` check: "Timer 8 Minuten"
   names no device and no room and is not a fragment.
2. `timing_kind ∈ {for_duration, delayed}` and conf ≥ `th.flag` → remember the kind; the
   request continues down the normal path with timing attached (§4.4).
3. Otherwise, if `flag:has_timing` fires (≥ `th.flag`) **or** `timing_kind` is any non-`none`
   pick (a clock time, a sequence, "other", or a hesitant timer/device kind) →
   `Escalate("timing")`. A request Jev bound to time in any way it cannot carry out never
   executes now ("Licht um 18 Uhr aus" must not turn the light off immediately).
4. A `MORE_SAME` follow-up replay with a device timing kind pending → `Escalate("timing")`
   (the replay path returns before Round 2 and would run the previous actions now).

### 4.3 The timer path (`timing.py`, new module)

Pure lookups and the timer-specific Round 2.

**Duration literals.** `duration_literals(prompt) -> tuple[DurationLiteral, ...]`, each
`DurationLiteral(text: str, value: float)`. A literal is a number token optionally followed by a
unit token, verbatim from the prompt. Number tokens: digits (`\d{1,4}(?:[.,]\d+)?`) or a word
from `NUMBER_WORDS` (de + en: eins/ein/eine/einen/einer → 1, zwei/zwo 2, drei 3, … zwölf 12,
dreizehn … neunzehn, zwanzig, dreißig/dreissig, vierzig, fünfzig, sechzig; compounds
`einundzwanzig` … `neunundfünfzig` via a stupid pattern `(\w+)und(zwanzig|dreißig|…)`;
`halbe/halben/half` 0.5, `viertel/quarter` 0.25, `dreiviertel` 0.75,
`anderthalb/eineinhalb` 1.5; en one … sixty). Unit tokens (optional, attached to the
literal text, *not* interpreted by code): `sekunde(n)|sek|s|minute(n)|min|stunde(n)|std|h|
second(s)|sec|minute(s)|hour(s)|hr(s)`, also glued ("Viertelstunde" = `viertel` + `stunde`).
Code never decides the unit: for every literal Jev is asked

- `duration:{i}` (Choice, options `seconds` / `minutes` / `hours` / `not a duration`, described):
  "Which unit is `<literal text>` meant in, as part of how long the timer / the delay lasts —
  or is it not a duration at all (a percentage, a room number, a count)?"

`seconds = Σ value_i × unit_factor_i` over literals not judged `not a duration`. Bounds:
5 s ≤ seconds ≤ 24 h, else `Escalate("timing")` with note `timing:out_of_bounds`. No literal, or
every literal `not a duration` → `Escalate("timing")`, note `timing:no_duration`.

Articles (`ein, eine, einen, einer, a, an`) are number words only when a unit word follows
("eine Stunde", "an hour"); alone they are not literals ("einen Timer" is not "1").

**Label.** `label_candidates(prompt, literals) -> tuple[str, ...]`: every word of the prompt with
≥ 3 letters, minus words that are part of a duration literal, minus number words, unit words,
articles and the timer words (`TIMER_WORDS` in `timing.py`: timer, timers, wecker, alarm,
countdown, eieruhr, stell, stelle, stellen, set, start, starte, starten), deduplicated,
original casing. Function words stay — Jev filters, code does not. Jev is asked

- `timer_label` (Choice over candidates + `no label`, described): "Which word names WHAT the
  timer is for — the dish, the task, the thing being timed? `no label` when the sentence only
  says timer and a duration."

The label is the chosen word when conf ≥ `th.target_choice_conf`, else `None` (note
`timer_label:hesitant`). Asked only for `start`, only when candidates exist.

**Which timer.** For `cancel` / `remaining`:

- no active timers → `Resolved(timer=TimerCommand(kind, timers=()))`, no Round 2 (the responder
  says none is running);
- `remaining` with exactly one → that one, no Round 2 (note `timer:single`);
- otherwise (`remaining` with several, `cancel` with one or more) → Round 2 `timer_pick`
  (Choice): options are one label per timer built by code (`timer_option(t)`: `label` or
  `description` or `timer`, plus `remaining` as "m:ss"; a duplicate label gets ` #2`, ` #3`),
  plus `all timers` and `NO_MATCH`, described. Rules: `remaining` with NO_MATCH or conf <
  `th.target_choice_conf` → all timers (reading is harmless; note `timer_pick:all`). `cancel`:
  a pick or `all timers` with conf ≥ `th.target_choice_conf` → those; otherwise
  `NeedsClarification("which_timer", (), trace, timers=<all active>, timer_kind="cancel")`.

**Round 2 state for the timer path:** `{"request", "timers": [...], "duration_literals":
[text…], "label_candidates": [...]}` — only the keys that apply.

**Confidence:** `min(timing_conf, all duration confs, label conf if a label was taken, pick
conf if a pick was taken)`, recorded as the `confidence` decision against `auto_execute` like
every other turn. A timer is harmless, so the timer path has no confirm turn: confidence ≥
`th.confirm_band` → `Resolved`; below → `Escalate("low_confidence")`, note
`timer:low_confidence`.

Round budget: the timer path spends Round 1 plus at most one Round 2; `max_rounds` applies as
elsewhere (`round_budget` escalation).

### 4.4 Timed device actions

When step 2 of §4.2 remembered `for_duration` or `delayed`:

- any fired verb `is_query` → `Escalate("timing")`, note `timing:query`;
- `duration_literals(prompt)` empty → `Escalate("timing")`, note `timing:no_duration`, before
  Round 2;
- `plan_round2(..., timing_kind=<kind>)` puts the literals in `Round2Plan.duration_literals`;
  `build_round2_questions` adds `duration:{i}` per literal (same question as §4.3). Timing
  therefore always needs a Round 2, even when the targets were already settled in Round 1
  (the `round_budget` rule applies unchanged);
- `resolve(...)` computes `seconds` as in §4.3 (bounds, no-duration → `Escalate("timing")`),
  adds every duration conf **and `shape.timing_conf`** to `contributions`, and:
  - `condition is not None` → `Escalate("timing")`, note `timing:with_condition`;
  - `for_duration` and an action's verb — or the verb of a pending post-Round-2 clarification —
    is not in `INVERSES` → `Escalate("timing")`, note `timing:not_invertible:<verb>`;
  - attaches `Timing(kind, seconds)` to the `Resolved` / `NeedsConfirmation` /
    `NeedsClarification` it returns.
- A `NeedsClarification` produced *after* Round 2 (the target Choice spread its mass) carries
  `timing` too, so the reply turn can execute with it. A clarification produced *before* Round 2
  (scope-time `which_device`) has no duration yet; the integration's reply turn then executes
  **without** timing — unacceptable. Rule: when a timing kind is pending and the scope step
  would return `NeedsClarification` before Round 2, the engine returns `Escalate("timing")`
  instead, note `timing:clarify_before_duration`.
- Follow-ups: `MORE_SAME` replay returns the previous actions without timing (unchanged).
  Timing is never carried from a previous turn.

**`INVERSES`** (`vocabulary.py`, code table):
`turn_on↔turn_off`, `open↔close`, `unlock→lock`, `media_play↔media_pause`. Everything else
(`set_*`, `lock`, `arm`, `disarm`, `activate`, queries) has no inverse (see §3 for `lock`).

### 4.5 Phrasebook additions (EN + DE)

`timing_kind_question` + `timing_kind_descriptions` (8 options), `duration_question`
(`{literal}`) + `duration_descriptions` (4 options), `timer_label_question` +
`no_label` description, `timer_pick_question` + `all_timers` / `no_timer_match` descriptions.
Sentinels: `SECONDS = "seconds"`, `MINUTES = "minutes"`, `HOURS = "hours"`,
`NOT_DURATION = "not a duration"`, `NO_LABEL = "no label"`, `ALL_TIMERS = "all timers"` in
`timing.py`; `NO_MATCH` reused from `round2.py`.

### 4.6 Trace notes (closed set added)

`timing_kind:<id>`, `timing:no_duration`, `timing:out_of_bounds`, `timing:query`,
`timing:with_condition`, `timing:not_invertible:<verb>`, `timing:clarify_before_duration`,
`timing:replay`, `timing:seconds:<n>`, `timer:none_active`, `timer:single`, `timer_pick:all`,
`timer_label:hesitant`, `timer:low_confidence`.

## 5. Integration changes (`custom_components/hunch`, version 0.4.0)

### 5.1 `TimerStore` (`timers.py`, new)

```python
@dataclass(frozen=True)
class StoredAction:
    verb: str
    entity_ids: tuple[str, ...]
    params: Mapping[str, float | str]

@dataclass(frozen=True)
class HunchTimer:
    timer_id: str                 # uuid4 hex
    kind: str                     # "timer" | "revert" | "delayed"
    label: str | None
    description: str | None       # "Wandlampe aus" — rendered at creation, in the turn's language
    duration_seconds: float
    due_at: datetime              # aware UTC
    actions: tuple[StoredAction, ...]   # () for kind "timer"
    language: str
    conversation_id: str | None
    device_id: str | None
    satellite_id: str | None
    area_id: str | None           # the requesting device's area, looked up at creation
    user_id: str | None           # the requesting user; stored actions run as them
```

`TimerStore(hass, on_fire: Callable[[HunchTimer, bool], Awaitable[None]])`:

- `async_load()` — reads `Store(hass, 1, "hunch.timers")`; re-arms every timer with
  `async_track_point_in_utc_time` (the due callback is a HA `@callback`); an overdue one (due ≤
  now) fires with `overdue=True` once HA has started (`async_at_started`), so the services its
  actions need exist.
- `active() -> tuple[HunchTimer, ...]` sorted by `due_at`; `remaining(timer) -> float` (≥ 0).
- `async_add(timer)` arms and saves; `async_cancel(timer_id) -> HunchTimer | None` disarms,
  removes, saves.
- `as_active_timers() -> tuple[ActiveTimer, ...]` for the engine (`label`, `description`,
  `remaining_seconds`, `kind`).
- `async_stop()` — cancels the listeners (unload), keeps the file.
- Firing: the listener removes the timer from memory, awaits `on_fire`, then saves. Neither a
  failing save nor a failing callback raises out of the listener (logged). Fire tasks are
  created with `entry.async_create_background_task`.

Serialization is a plain dict per timer (`due_at` as ISO string); the store file is not user
data beyond labels and entity ids.

### 5.2 Firing (`timers.py` `async_fire_timer(hass, entry, timer, overdue)`)

1. kinds `revert` / `delayed`: rebuild `Action`s from the current home model
   (`builder.build().entity_by_id`, `DEFAULT_VOCABULARY.by_name`), drop entity ids that no
   longer exist (logged), run `Executor.execute(actions, Context(user_id=timer.user_id))`.
   An action more than `MAX_OVERDUE_SECONDS` (3600) overdue is **skipped** (a blind must not
   open hours late at night); the event then carries `skipped: true` and `executed: []`.
2. fire `hunch_timer_finished` with
   `{timer_id, kind, label, description, duration_seconds, duration_text, name, due_at, overdue,
   skipped, language, conversation_id, device_id, satellite_id, area_id, user_id,
   executed: [entity_id…], failed: [entity_id…]}`. `duration_text` and `name` are the spoken
   forms from the responder in the request's language (added 2026-09-25: a 10-second timer
   announced as "0 Minuten" by a Jinja `// 60` template).
3. if option `timer_script` is set and `kind == "timer"`: `script.turn_on` on that entity with
   `variables` = the same dict, `blocking=False`. A missing script is logged, never raised.
   Reverts and delayed actions are not announced (2026-09-25, Julian: "nur das echte Timer");
   automations on the event see every kind.

### 5.3 Runtime and options

`HunchRuntime` gains `timers: TimerStore` and `timer_script: str | None`. Setup order:
runtime built → `await timers.async_load()` → platforms. Unload: `timers.async_stop()`.

Options flow: `OPT_TIMER_SCRIPT = "timer_script"`, `EntitySelector(domain="script")`, optional.
`strings.json` + `translations/{en,de}.json`: "Script to run when a timer finishes" /
"Skript beim Ablauf eines Timers".

### 5.4 Conversation entity

- `decide(home, text, previous, timers=rt.timers.as_active_timers())`.
- `Resolved` with `timer` → `_run_timer`:
  - `start`: build `HunchTimer(kind="timer", label, duration, due=now+duration, actions=(),
    language, conversation_id, device_id, satellite_id, area_id)` → `async_add` →
    `timer_started`.
  - `remaining`: `timers == ()` → `timer_none`; else one `timer_remaining` line per timer.
  - `cancel`: `timers == ()` → `timer_none`; else `async_cancel` each → `timer_cancelled`.
- `Resolved` / confirmed `NeedsConfirmation` / clarified pick with `timing` → `_run(...,
  timing=)`:
  - `delayed`: no execution now; `HunchTimer(kind="delayed", actions=<the plan>,
    description=describe_action clauses)` → `async_add` → `delayed_scheduled`.
  - `for_duration`: execute now as today; then `HunchTimer(kind="revert",
    actions=inverse_actions(commands), description=<revert clause>)` → `async_add` →
    `for_duration_done`. `inverse_actions` maps each action's verb through `INVERSES`, keeps
    targets, drops params. If execution partly failed, the revert is still scheduled for the
    targets that succeeded (only those).
  - the condition check runs before (engine guarantees no condition with timing, so nothing
    changes here).
- `NeedsConfirmation.timing` travels into `PendingConfirm.timing`; `NeedsClarification.timing`
  into `PendingClarify.timing`; both reply handlers pass it to `_run`.
- `NeedsClarification("which_timer")` → `PendingTimerPick(timers, labels, question, created)`.
  `labels` are the spoken labels (`timer_name` + remaining, e.g. "Timer für Nudeln (3 Minuten
  20 Sekunden)") plus `all timers`; the reply is judged with the existing `CLARIFY_QID` Choice
  over exactly these labels; the pick cancels those timers. A reply that picks nothing answers
  `cancelled` ("Okay, ich habe nichts geändert.") — the fallback agent cannot cancel Hunch
  timers, so it is never handed this turn. Timers that vanished meanwhile are skipped; if none
  is left, `timer_none`.
- Confirm questions with timing carry a `timing_clause` (", in 15 Minuten" / ", für 15
  Minuten"); a `risk:confirm` re-ask after a clarification keeps the timing.
- `_remember`: timing turns and timer commands are **not** remembered (§3).
- `extra_data.hunch` / `agent_detail`: unchanged; the outcome string is `Resolved`.

### 5.5 Responder

`format_duration(seconds, lang)`: whole hours / minutes / seconds, zero parts dropped, singular
forms ("1 Minute", "1 Stunde", "1 Sekunde" / "1 minute" …), e.g. "8 Minuten", "1 Stunde 20
Minuten", "3 Minuten 20 Sekunden". `compact_duration` for names: "8-Minuten" / "8-minute",
"1-Stunde-20-Minuten" is allowed to be ugly.

`timer_name(label, description, duration_seconds, lang)`: `label` → "Timer für {label}" /
"timer for {label}"; `description` (scheduled actions: the action clauses rendered at creation,
"Wandlampe (Vorzimmer) ausschalten") → the description; else "{compact}-Timer" /
"{compact} timer". English sentences are capitalised at the first letter by `render`.

Templates (en / de):

| key | en | de |
|---|---|---|
| `timer_started` | "{name} set, {duration}." | "{name} gestellt, {duration}." |
| `timer_none` | "No timer is running." | "Es läuft kein Timer." |
| `timer_remaining_line` | "{name}: {remaining} left." | "{name}: noch {remaining}." |
| `timer_remaining` | lines joined with newlines | lines joined with newlines |
| `timer_cancelled` | "Cancelled: {names}." | "Abgebrochen: {names}." |
| `which_timer` | "Which timer do you mean: {options}?" | "Welchen Timer meinst du: {options}?" |
| `delayed_scheduled` | "In {duration}: {body}." | "In {duration}: {body}." |
| `for_duration_done` | "Done: {body}, {revert} again in {duration}." | "Erledigt: {body}, in {duration} wieder {revert}." |

`body` = the existing `describe_action` clauses (infinitive for `delayed`, done-form for
`for_duration`). `REVERT_WORDS` (per inverse verb): en on/off/open/closed/locked/unlocked/
playing/paused; de an/aus/auf/zu/abgesperrt/aufgesperrt/weiter/pausiert. `timer_name` for an
unnamed timer capitalises in German ("8-Minuten-Timer").

### 5.6 Diagnostics

`"timers": [{"kind", "label", "description", "remaining_seconds"}]`.

## 6. Golden corpus and runner

`run_golden.py` learns:

- row input `timers: [{label, remaining_seconds, kind, description}]` → passed to `decide`;
- expectations `timing: {kind, seconds: [lo, hi]}`, `timer: {kind, seconds: [lo, hi], label,
  count}` (`count` = number of timers a cancel/remaining named).

New rows in `corpus_julian.yaml` (entity ids from `golden/homes/julian.json`):

| prompt | expect |
|---|---|
| Timer 8 Minuten | resolved, timer start, 480, label none |
| Stell einen Timer für die Nudeln auf 8 Minuten | resolved, timer start, 480, label Nudeln |
| Timer für die Nudeln, 8 Minuten | resolved, timer start, 480, label Nudeln |
| Eine Viertelstunde Timer | resolved, timer start, 900 |
| Timer eine halbe Stunde | resolved, timer start, 1800 |
| Timer 1 Stunde 20 | resolved, timer start, 4800 (or clarify-free 3600: kinds resolved, seconds [3600, 4800]) |
| Wie lange läuft der Timer für die Nudeln noch? (timers: Nudeln 200 s, Reis 600 s) | resolved, timer remaining, count 1 |
| Wie lange noch? (timers: Nudeln 200 s) | resolved, timer remaining, count 1 |
| Timer abbrechen (timers: Nudeln 200 s) | resolved, timer cancel, count 1 (Jev confirms the single timer) |
| Timer abbrechen (timers: Nudeln, Reis) | clarify which_timer, or resolved cancel with count 2 |
| Alle Timer abbrechen (timers: Nudeln, Reis) | resolved, timer cancel, count 2 |
| Wandlampe im Vorzimmer für 15 Minuten an | resolved, turn_on, light.vorzimmer_wandlampe, timing for_duration 900 |
| Wandlampe im Vorzimmer in 15 Minuten aus | resolved, turn_off, light.vorzimmer_wandlampe, timing delayed 900 |
| Rollo in der Küche für 10 Minuten runter | resolved, close, cover.kuche_fenster_rollo, timing for_duration 600 |
| Rollo in der Küche auf 20% für 10 Minuten | escalate timing |
| Mach das Licht um 18 Uhr aus | escalate timing |
| Wie warm ist es in 10 Minuten? | escalate, reason timing |

The existing rows "Mach das Licht in zehn Minuten aus" (both corpora) change from `escalate
timing` to `kinds: [resolved, confirm, clarify]` with `timing: {kind: delayed, seconds: [600,
600]}` — "Licht" over the whole home may land anywhere between a sweep confirm and a pick, but
the timing must be recognised.

## 7. Testing

Engine (`packages/hunch/tests`, default suite, no HA):

- `test_timing.py`: literals (digits, words, compounds, glued units, "1 Stunde 20" → two
  literals, "15%" is a literal Jev may reject, bare articles are not literals),
  `label_candidates` (timer words, numbers, units, articles and literal parts removed, casing
  kept), `INVERSES` (every inverse is a verb, `lock` absent), seconds arithmetic and bounds.
- `test_round1.py`: `timing_kind` question present with 8 described options; `timers` in state
  only when passed; Shape carries kind + conf.
- `test_engine.py` (scripted `FakeDecisionClient`): timer start with label; start without
  literal → `Escalate("timing")`; remaining with 0/1/2 timers; cancel with two timers hesitant
  → `NeedsClarification("which_timer")`; cancel `all timers`; `for_duration` on `turn_on`
  attaches `Timing`; `for_duration` on `set_position` → `Escalate("timing")`;
  `delayed` on a query → `Escalate("timing")`; timing + condition → `Escalate("timing")`;
  hesitant kind with `has_timing` fired → `Escalate("timing")`; a timer sentence with
  `is_fragment` high still reaches the timer path; the fake client's Round 2 sees
  `duration:0` questions.

Integration (`tests/integration`, `uv run --group ha --no-group dev pytest tests/integration`):

- `test_timers.py`: add + fire via `async_fire_time_changed` → `on_fire` called, timer gone,
  store saved (`hass_storage`); cancel disarms; load re-arms; load with an overdue timer fires
  with `overdue=True`; `as_active_timers` remaining shrinks with time.
- `test_conversation.py` additions: start → speech "Timer für Nudeln gestellt, 8 Minuten." and a
  stored timer; remaining / cancel / none running; `for_duration`: service call now, then after
  the duration `homeassistant.turn_off` on the same ids and the event fired; `delayed`: no
  service call now, one after the delay; confirm turn with timing executes with timing;
  which-timer clarification reply cancels the picked one; script option → `script.turn_on`
  called with variables; event payload fields.
- `test_options_flow.py`: `timer_script` stored; `test_diagnostics.py`: `timers` block.

Golden: the rows of §6 pass against `julian.json` with `--phrasebook en` (the tuned book).

## 8. Sequence

1. Engine: `resolution.py` types, `timing.py` lookups, `vocabulary.INVERSES`, phrasebook
   strings, Round 1 `timing_kind`, timer path in `engine.py`, duration questions in
   `round2.py`/`resolver.py`, tests, version 0.6.0.
2. Integration: `TimerStore` + runner, runtime/options, conversation dispatch, responder,
   diagnostics, tests, manifest 0.4.0 → `hunch-engine==0.6.0`.
3. Golden runner + corpus rows; run against the real home; tuning-log entry in
   `golden/README.md`; engine spec addendum pointer.
4. Julian publishes engine 0.6.0 (`tools/check_dist.py` first); release v0.4.0.

## Addendum (2026-09-25) — rulings from the final review

1. **Spoken units are looked up, not judged.** Supersedes §4.3 "*not* interpreted by code" and
   "Code never decides the unit". `DurationLiteral` gains `unit: str | None = None`: the unit
   word that follows a number ("Minuten", "Std", glued "Viertelstunde") is looked up by code
   (`unit_of`); only a bare number ("Timer 8") takes Jev's `duration:{i}` unit. For a literal
   with a spoken unit Jev's answer decides only "duration or not", and its confidence is
   `1 − P(not a duration)` — asked for the unit of "halbe Stunde", Jev split minutes/hours
   0.51/0.49, sure it is a duration and unsure how to name it. Lookups added in the same spirit:
   a fraction with an article before its unit is one literal ("half an hour" 0.5 h, "a quarter
   of an hour" 0.25 h); a bare fraction after an article is none ("an hour and a half" reads as
   1 h — a known gap, never a wrong 1.5 h; "one and a half hours" is a known gap too: it reads
   as a bare "one" plus "half hours", 30 min plus whatever unit Jev gives the "one"); number
   words run to ninety (siebzig … neunzig, seventy … ninety, compounds) and "zweieinhalb" …
   "neuneinhalb".
2. **`all timers` only for two or more timers.** Supersedes §4.3 "plus `all timers`": with one
   timer, "that timer" and "all timers" name the same set and split Jev's mass, so neither the
   `timer_pick` options nor its question text (`{all_hint}`) mention it. The integration's
   which-timer question follows the same rule. Cancel still always asks (§3).
3. **`NO_MATCH` is duplicated in `timing.py`.** Supersedes §4.5 "`NO_MATCH` reused from
   `round2.py`": `round2` imports `timing`, so `timing` cannot import `round2`; the string is
   defined in both and `test_timing` asserts they are equal.
4. **"für" reverts only what changed.** Supersedes §2's "the revert ignores the device's state"
   and extends §5.4. Before a `for_duration` plan runs, the integration reads each target's
   state (`hass.states`, a lookup). A target is reverted only if its service call succeeded and
   its prior state differed from `COMMANDED_STATE[verb]` (`turn_on` on, `turn_off` off, `open`
   open, `close` closed, `unlock` unlocked, `media_play` playing, `media_pause` paused; a verb
   missing from the table, or a target with no state, counts as a change). Two lookups refine
   the table (`is_already_done`, round 2 of the review): `turn_on` is already done in any
   state but `off`, `unavailable` and `unknown`, because thermostats (`heat`, `cool`, `auto`)
   and media players (`playing`, `idle`, …) are never `on`; and a cover that reports
   `current_position` is already open only at 100 and already closed only at 0 (a blind at 50%
   reports `open`), otherwise its state word decides. Targets already in the commanded state are
   neither reverted nor named in the "wieder aus" sentence. If no target changed, no revert
   timer is stored and the reply is the plain `action_done`. After a partial failure the revert
   sentence is `for_duration_rest` ("{body}, in {duration} wieder {revert}.") under the
   `execution_failed` line, not a second "Erledigt:".
5. **A timed turn or a timer command clears the last turn.** Extends §3 and §5.4 `_remember`:
   not remembering a timed turn is not enough — "Licht Küche an", "Licht Küche in 10 Minuten
   aus", "und im Esszimmer" would lean on the first turn and switch the Esszimmer light on at
   once. `LastTurnStore.forget(conversation_id)` runs right after `decide` whenever the result
   carries timing (a `Resolved`, `NeedsConfirmation` or `NeedsClarification` with `timing`) or
   is `Escalate("timing")` — so a timed request that is later declined, answered with "other"
   or handed off clears the turn before it too — and whenever a timer command resolves or a
   which-timer question is asked.
6. Also ruled in, each keeping §3's safety rules:
   - a hand-off after a which-device question (reply NO_MATCH, hesitant or failed) carries the
     timing clause (" in 15 minutes") in its context, like the confirm hand-off;
   - a failed timer-pick judgment answers `cancelled` locally (outcome
     `TimerPickJudgmentFailed`); a timer pick never reaches the fallback;
   - a delay and a duration in one sentence ("in 5 Minuten für 10 Minuten") is `other timing`
     by its phrasebook description, so it escalates as `timing` (golden row); no code heuristic;
   - `resolve_timer` honours `supports_clarification=False`: a hesitant cancel returns
     `Escalate("low_confidence")` instead of `NeedsClarification("which_timer")`.
