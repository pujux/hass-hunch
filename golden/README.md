# Golden corpus

24 prompts run against the real Jev API. This is the regression net for future model bumps —
when the pinned `jev-*` model changes, re-run this corpus before rolling it out.

## Latest result

- **Model:** `jev-1.13.0`
- **Date:** 2026-09-19 (after the final-review fix wave)
- **Agreement:** fixture 24/24; real German home (`corpus_julian.yaml`, 32 rows) 32/32 (one row, "Mach alles aus außer …", is variance-prone)
- **Latency:** p50 ≈ 370–400 ms, p95 ≈ 800 ms (real home needs two rounds more often)
- **Cost:** ≈ $0.0016 (fixture) / $0.0034 (real home) per full run

2 rows fail. "turn everything off" is the long-standing model-variance row. "open the blinds
halfway" is **newly reported, not newly broken**: the fix wave tightened `check()` so a
spurious extra action fails the row, and this prompt fires `open` (0.98) *and* `set_position`
(0.99), producing two actions where the corpus expects one. Both are described under "Known
issues" below.

## How to run

```bash
uv run python golden/run_golden.py [--model jev-1.13.0] [--only 'kitchen'] [--verbose]
```

- `--model` overrides the pinned Jev model id (default `jev-1.13.0`).
- `--only <substring>` filters corpus rows whose prompt contains the substring — use it for
  focused iteration instead of running the full 24-row corpus every time.
- `--verbose` dumps `r.trace.to_dict()` (every question asked, every answer, every threshold
  decision) for each FAIL row, to help diagnose what went wrong.

Requires `TYPESAFE_API_KEY` in `.env` at the repo root (loaded via `python-dotenv`; never
printed or read by this script). Not part of `pytest` — `golden/` sits outside `testpaths`.

Exit code is `1` if agreement < 80%, `0` otherwise (`2` if the API key is missing).

## What the columns mean

Each row prints:

```
PASS/FAIL  <latency> ms  rounds=<n>  '<prompt>'
        - <problem>   (only for FAIL rows, one line per mismatch)
```

- **PASS/FAIL**: whether the engine's resolution matched `expect` in `corpus.yaml`.
- **latency**: wall-clock time for `engine.decide(...)`, including all API round trips.
- **rounds**: number of `client.ask(...)` calls the engine made (`len(trace.models)`) — 1 if
  Round 1 alone resolved it, 2 if Round 2 (or a device round) was needed.
- **problem lines**: what mismatched — kind, verb, targets, reason, a param out of its expected
  range, or a condition.

Summary line:

```
<ok>/<total> agree (<pct>)  p50=<ms> ms  p95=<ms> ms  cost=$<usd>  tokens=<n>
```

- **agree**: fraction of rows that PASSed.
- **p50 / p95**: median / 95th-percentile latency across all rows (p95 falls back to `max()`
  below 20 samples, which is always the case here at 24 rows unless `--only` is used).
- **cost / tokens**: summed `input_tokens` across every trace entry, priced at
  `PRICE_PER_M_TOKENS = 0.042` ($ per million input tokens).

## Tuning log

### 2026-09-21 — comparators: Nouls say which apply, Choices make Jev compare

Experiment first (`scratch exp_choices.py`, 60 prompts): a verb Choice (verbs + several + none)
scored 38/38 vs the Nouls' 37/38 and resolved every co-fire ("auf" → open not turn_on, "zu" →
close not turn_off, "Wie spät ist es?" → none); an area Choice (areas + floors + several + whole
home + none) was worse than the Nouls as a scope source (27/33 vs 31/33 — it had no floors as
options and hedges on stems) but is the only thing that can say "none" or "whole home" outright;
a domain Choice added nothing. Adopted: `verb_primary` and `area_primary` in Round 1 alongside
the Nouls. Deleted in exchange: exclusive verb groups, the lone-leader rule, the `names_place`
and `whole_home` flags, the 0.9 hard bar. Also: the Round 1 state now shows the home as a
hierarchy (floors → areas, with aliases) instead of two flat lists, and lists the exposed devices
the prompt names by name (type + room) — that alone took "Kücheninsel auf 35%" from a hand-off
(set_position 0.67 vs set_brightness 0.51, Jev could not know what a Kücheninsel is) to
Resolved at 0.95. Rules that use the comparison: a single winner drops co-firing verbs; a verb
the Nouls left under the bar is promoted when the comparison agrees (contribution stays low →
confirmation); "several" skips the out-of-room re-check; a sure area pick (≥ 0.85) narrows the
scope, a hesitant one leaves the Noul set. Result: **fixture 24/24, real home 35/36** ("Wie
warm ist es im Vorzimmer?" open — see the trace notes in git history).

### 2026-09-20 (night) — Jev thinks, code fetches and computes

Julian's principle, after reviewing the day's heuristics: Jev is the judgment engine; code only
helps it get state and data, then lets it decide. More questions cost nothing measurable
(they run in parallel; a full 36-row run is still ≈ $0.005). Every rule where code had been
*judging* was swapped for a Jev question, one at a time, measured against the corpus:

| Code was judging… | Now Jev is asked… |
|---|---|
| a regex picked "15%"/"22 Grad" as the target | which listed number is the value (or none), whether it is a change (`param_relative`) and, for blinds, whether it means closed (`param_inverted`); code parses and bounds it |
| a verbatim device name narrowed the candidates | nothing — names only rescue candidates from the cap; the Choice decides, with "none of these" |
| "no device named + room named ⇒ all of them" sweeps | `all_of` Noul with the candidates in front of it ("die Rollos", "Licht im Untergeschoss") |
| a widened verb was dropped/kept by rule | `outside_scope` Noul: is this a separate action on devices outside the named room? ("… and close the blinds") |
| an exception named verbatim was excluded by code | the exclusion Nouls — and they hold at n=52 (Mini Kühlschrank 0.98, everything else 0.02) |
| a hard 0.9 bar and a room-count rule decided the scope | `names_place` and `whole_home` Nouls; a room said out loud (or a shared stem) is the scope; a room Jev is ≥ 0.9 sure of counts on its own |
| domain synonyms overrode Jev's domain | the synonyms ride inside Jev's domain question ("light (lights, lamps, Licht, Lampen…)") |
| a 4-way "mode" Choice | two Nouls with contrasting examples — the Choice had hedged at 0.4 |

Also: Choice options now carry descriptions (Jev criteria); multi-entity devices are offered
both as the device ("Bedside lamps") and per entity. Result: **real home 36/36, fixture 24/24**.
Two rows hover at a threshold and may confirm instead of resolve ("15% zu" number pick,
"alles aus außer" lone-leader verb) — asking is the designed outcome there.

### 2026-09-20 (evening) — thresholds set on Julian's corpus

Decided with Julian once his prompts were in: (1) a collective flag backed by a named area or
floor counts as strong — `max(collective, scope strength)` — because two independent signals
agree on "all of them"; (2) a lone-leader verb fires below `verb_fire` when it is ≥ 0.55 and
leads the runner-up by ≥ 0.3 ("Mach alles aus": turn_off 0.68, close 0.33); (3) `auto_execute`
0.75 → 0.70. Also: `has_condition` reworded so "außer"/"except" (an exception) no longer reads
as a condition (0.07 vs 0.98 for real conditions). Then two more code-side rules from the last failing traces: most rooms firing at once means
*the whole home*, not a dozen hallucinations ("Mach alles aus"); and an exception named verbatim
("außer dem Mini Kühlschrank", "except the fridge") is excluded by code with no Nouns asked.
Result: **real home 32/32, fixture 24/24** ("turn everything off" accepts confirm or escalate).

### 2026-09-20 (later) — Julian's own prompts: code matches names, Jev judges the rest

13 prompts added verbatim to `corpus_julian.yaml` (32 rows). Baseline on the new rows was poor
(25/32 overall) and every failure traced to a place where Jev was asked something code could
have looked up:

- **Explicit numbers** ("auf 15%", "22 Grad") are now read by code (`ScoreSpec.kind`); the
  Score rubric is only asked for words like "halb".
- **Names before judgement.** Areas and floors named in the prompt (names or aliases, and a
  stem like "Badezimmer" for two bathrooms) set the scope; a Jev-only area needs ≥ 0.9
  ("Licht aus" had picked Ankleide 0.72 + Wohnzimmer 0.77 out of thin air). Device and
  entity names in the prompt narrow the candidates inside the scope ("Dachterrassentür" → the
  two door sensors, not 116 candidates). Domain words in either language ("Licht", "lights",
  "Rollos", "Fernseher") override Jev's domain guess ("downstairs lights" had swept a switch).
- **Contradictory verbs** (open+close+set_position, on+off, lock+unlock) — only the strongest
  fires. Fixed the long-standing "open the blinds halfway" co-fire.
- **Sweeps.** Whole area/floor named + no device named + no single target → all of them;
  no area + no name → all, with confirmation ("Licht aus"); never on widened candidates;
  never when Jev says a device was named (`names_specific` reworded: 0.84 for an unexposed
  "Stehlampe", 0.04 for "Licht aus").
- **Ask, don't guess:** weak pick among real alternatives → clarify; same name in several
  rooms with no room said → clarify even on a confident pick ("Dachterrasse Rollo zu").
- **Safety:** a condition or exception we could not honour blocks execution and escalates
  ("… wenn es wärmer als 23 Grad ist" would have closed the blinds unconditionally).
- Result: **29/32 (91%)**, p50 ≈ 410 ms; fixture corpus **23/24**.
- The three remaining rows are threshold/contribution questions, not logic: two correct
  sweeps land at 0.55–0.72 because the `collective` flag is a min-contribution
  ("Rollos im Schlafzimmer runter", "Badezimmer Rollos auf 15%"); "Mach alles aus" has
  `turn_off` at 0.63–0.75 around `verb_fire` 0.7.

### 2026-09-20 — first real home (German, 187 exposed entities) and the rules it forced

Export of a real HA 2026.9.2 installation via `tools/export_home.py` (gitignored), corpus
`golden/corpus_julian.yaml` (19 German prompts), run with `--home golden/homes/julian.json`.

- Baseline, English questions, no changes: **13/19 (68%)**.
- Same prompts with German questions (`--phrasebook de`), same session: **12/19** vs EN 15/19 —
  the failures were identical, so language was ruled out; wording stays English by default.
- Rules added (spec §6 "No-match handling"): singular no-match + `collective` ≥ 0.4 →
  collective with confirmation ("Rollos … halb runter"); singular no-match with confidence < 0.6 →
  `NeedsClarification` with ranked candidates ("Ist das Fenster im Esszimmer offen?"); a
  `names_specific` Round 1 flag (weak on its own: 0.49 for "die Spots"); and a deterministic
  verbatim-name override — if exactly one scoped candidate's name appears in the prompt, a
  collective sweep narrows to it ("Mach die Spots in der Küche an" → only `light.kuche_spots`).
- Result: **17/19 (89%)**, p50 ≈ 650 ms (two rounds are the norm on this home), $0.002/run.
  Fixture corpus unchanged at 22/24.
- Still failing: "Fahr die Rollos im Schlafzimmer runter" resolves correctly but at 0.71
  confidence (`auto_execute` 0.75) → asks; "Mach alles aus außer dem Mini Kühlschrank" has
  `turn_off` hovering 0.67–0.75 around `verb_fire` 0.7 → sometimes `no_intent`. Both are
  threshold questions, deliberately left until more real prompts exist.

Entries below are dated, one per change, in the order applied. Each records what changed, which
corpus rows it affected, and the agreement rate before → after a full run.

### 2026-09-19 — baseline

First run against `jev-1.13.0`, no tuning: **13/24 agree (54%)**, p50=558 ms, p95=761 ms,
cost=$0.0015 (35,489 tokens). 11 rows failed:

```
FAIL     774 ms  rounds=1  'turn off the downstairs lights'
        - targets [...,'switch.fridge'] != [...] (fridge should not be included)
FAIL     772 ms  rounds=2  'turn off everything in the kitchen except the fridge'
        - targets [all downstairs lights] != [kitchen lights only]
FAIL     703 ms  rounds=2  'dim the office light to about half'
        - param brightness_pct=[21.1] not in [40, 60]
FAIL     389 ms  rounds=1  'unlock the front door'
        - kind escalate != confirm
FAIL     342 ms  rounds=1  'run the goodnight script'
        - kind confirm != resolved
FAIL     536 ms  rounds=1  'turn everything off'
        - kind resolved != confirm
FAIL     580 ms  rounds=2  'set the bedroom to 22 degrees'
        - param temperature=[20.0] not in [21, 23]
FAIL     761 ms  rounds=2  'turn on the christmas tree'
        - kind escalate != resolved
FAIL     358 ms  rounds=1  'turn on both bedside lamps'
        - targets [..., 'light.office_desk'] != [bedroom lamps only]
FAIL     315 ms  rounds=1  "what's the weather like tomorrow"
        - reason timing != no_intent
FAIL     699 ms  rounds=2  'turn off the kitchen lights and close the blinds'
        - kind confirm != resolved
```

Diagnosis, from `--verbose` traces on each FAIL row:

1. **Scope over-widening in collective mode.** `Thresholds.scope_fire` (0.6) let borderline
   floor/area/domain Nouls (0.61–0.69) fire — e.g. `floor:downstairs`=0.61 for a
   kitchen-only request, `domain:switch`=0.65–0.67 for lights-only requests,
   `floor:upstairs`=0.67–0.69 for a bedroom-only request, `area:living`=0.62 for the
   christmas tree (which has no area at all and got excluded by the spurious area filter).
   When `flag:collective` is high, these widened candidates go straight into the action with
   no Round-2 disambiguation, so the extra scope leaks into the final targets. Affects: downstairs
   lights, kitchen-except-fridge, bedside lamps, christmas tree.
2. **`is_destructive`'s instruction named "unlocking" and "disarming" as examples**, directly
   contradicting `unlock`/`disarm`'s own `Risk.CONFIRM` classification — so "unlock the front
   door" always escalated as `destructive` before its own verb risk was even considered.
3. **`has_timing`'s instruction fired on any future-time mention**, not just device-action
   scheduling, so "what's the weather like tomorrow" escalated as `timing` instead of falling
   through to `no_intent`.
4. **`turn_off`'s phrasing ("turn something off") is broad enough that a goodnight *script*
   invocation also scores high on it** (0.79–0.80) — goodnight routines conceptually "turn
   things off". The spurious `turn_off` action's `flag:collective` contribution (0.67) then
   drags the overall confidence into the confirm band, so `NeedsConfirmation` fires instead of
   `Resolved` even though the *real* action (`activate` the script) was fully confident.
5. **`set_position`'s phrasing overlaps with `close`/`open`** ("set how far open blinds... are"
   reads as true for a plain close), so it co-fired at 0.70–0.81 alongside `close` on
   "close the living room blinds" and "turn off the kitchen lights and close the blinds",
   breaking the exact-verb-set check on the latter.
6. **`flag:collective`'s instruction under-weighted a plain plural** ("the kitchen lights") in
   a multi-verb request, landing at 0.55–0.57 — just under the 0.65 gate — so the multi-target
   `turn_off` action was mis-routed to the singular-target `Choice` path instead of the
   collective path, which asked "which single device" and got `none of these`.
7. **`Thresholds` alone cannot make `max_silent_targets` (an `EngineConfig` field, out of
   ruling-C's tuning bounds) reachable on this fixture** — the fixture home has only 10
   turn_off-capable entities, well under the default `max_silent_targets=20`, so "turn
   everything off" can never trigger `blast_radius` regardless of thresholds. The corpus
   expectation was wrong for this fixture's scale; see "Corpus expectation corrections" below.
8. **`score_to_value` in `resolver.py` assumes 1-based Jev scores**
   (`idx = score - 1.0`), but per `docs.typesafe.ai/primitives/score.md` the Score primitive
   is explicitly **0-indexed** ("a level's number is its position in the `criteria` array,
   starting at 0"). This is an off-by-one bug in `resolver.py`, which ruling C puts out of
   tuning bounds ("the resolver's rules"). See "Known issues" below — left unfixed, both
   affected rows (`dim the office light`, `set the bedroom to 22 degrees`) fail in every run.

### 2026-09-19 — tuning pass 1: thresholds + wording (items 1–7 above)

Changes, all within ruling C's bounds:

- `Thresholds.scope_fire`: `0.6` → `0.7` (`config.py`). Verified against every corpus row's
  floor/area/domain probabilities before applying: this only excludes the 0.61–0.69 spurious
  fires identified above; every legitimately-needed scope signal in the corpus is ≥ 0.72, and
  rows that rely on broad/empty scope (query rows, single-entity domains) are unaffected because
  Round-2's per-target `Choice` question self-corrects when `flag:collective` is low.
- `Thresholds.collective`: `0.65` → `0.5` (`config.py`). Checked every row's `flag:collective`
  value: nothing meaningful sits in [0.5, 0.65) except the "kitchen lights and close the
  blinds" row (0.55–0.57), and every row below 0.5 either escalates before reaching the
  collective decision or has only one candidate entity (so collective vs. singular routing is
  moot).
- `round1.py` `_FLAG_INSTRUCTIONS["is_destructive"]`: reworded to explicitly exclude ordinary
  lock/unlock/arm/disarm actions ("beyond an ordinary lock, unlock, arm or disarm action (which
  are handled separately)"), since those already have their own `Risk.CONFIRM` handling.
- `round1.py` `_FLAG_INSTRUCTIONS["has_timing"]`: reworded to ask about delaying/scheduling a
  *device action* specifically, contrasting it with "merely mentioning a future time in a
  request that is not about controlling a device (such as asking about tomorrow's weather)".
- `vocabulary.py` `Verb.phrasing` for `turn_on`/`turn_off`: reworded to "directly turn a
  specific device or devices on/off (not by naming a scene or script)", to stop it firing on
  scene/script invocations that conceptually involve turning things off.
- `vocabulary.py` `Verb.phrasing` for `set_position`: reworded to "set blinds, shades or
  curtains to a specific partial position such as halfway or a percentage, rather than simply
  fully open or closed", to separate it from plain `close`/`open` requests.

Result: **19/24 agree (79%)**, p50=402 ms, p95=819 ms, cost=$0.0015 (36,843 tokens). Fixed:
downstairs lights, kitchen-except-fridge, unlock front door, goodnight script, christmas tree,
weather query. Still failing: dim office light (param bug, unfixable in-bounds), set bedroom
temperature (same param bug), turn everything off (`kind escalate != resolved` — see below),
bedside lamps (`targets` leak recurred — model variance, see below), kitchen-lights-and-blinds
(`kind confirm != resolved`, `flag:collective`=0.54 too low to lift overall confidence above
`auto_execute`).

### 2026-09-19 — tuning pass 2: collective wording (item 6 above)

- `round1.py` `_FLAG_INSTRUCTIONS["collective"]`: reworded to explicitly call out a plain
  plural as evidence of collective intent ("signaled by a plain plural (e.g. 'the kitchen
  lights'), or a word like 'all', 'both' or 'every'"), rather than relying on the model to
  infer it from "plural or 'all'" alone.

Verified in isolation with `--only 'kitchen lights and close'` (run 3 times: PASS, PASS, PASS)
before spending a full run.

Result: **21/24 agree (88%)**, p50=474 ms, p95=686 ms, cost=$0.0016 (37,635 tokens). Fixed:
kitchen-lights-and-blinds, and "turn on both bedside lamps" also passed this time (its earlier
failure was model variance — floor/area probabilities for this exact prompt have been observed
both above and below `scope_fire` across repeated runs; see "Known issues").

Stopped tuning here: 88% comfortably clears the 80% gate, and the 3 remaining failures are not
addressable within ruling C's bounds (see below) rather than under-tuned wording/thresholds.

### 2026-09-19 — follow-up fixes: `score_to_value` off-by-one, and lock/unlock phrasing

The controller ruled two items from "Known issues" (below) back in scope:

- **`resolver.py` `score_to_value`**: changed `idx = score - 1.0` to `idx = score`, per
  `docs.typesafe.ai/primitives/score.md` confirming the Score primitive is 0-indexed over its
  `criteria` list, not 1-indexed. Updated `test_score_to_value_interpolates` in
  `test_resolver.py` to the 0-based contract (`0.0→0`, `5.0→100`, `2.5→37.5`, clamps
  `-0.5→0`, `6.0→100`), and `test_param_is_interpolated_into_action`'s input score from `3.5`
  to `2.5` so its expected `37.5` output stays correct under the new formula (that test is
  about interpolation into `Action.params`, not about which score value maps to what).
  Re-ran the two affected rows in isolation:
  ```
  $ uv run python golden/run_golden.py --only "office light"
  PASS    1270 ms  rounds=2  'dim the office light to about half'
  1/1 agree (100%)  ...
  $ uv run python golden/run_golden.py --only "22 degrees"
  PASS    1179 ms  rounds=2  'set the bedroom to 22 degrees'
  1/1 agree (100%)  ...
  ```
- **`vocabulary.py` `Verb.phrasing` for `lock`/`unlock`**: reworded from "lock a door or lock"
  / "unlock a door or lock" (both contained the literal word "lock", read as a list rather
  than a contrast) to "lock or secure a door so it cannot be opened (not unlock)" / "unlock or
  release a door so it can be opened (not lock)". Confirmed via a scratch diagnostic dumping
  `verb:lock`/`verb:unlock` probabilities for the 4 front-door rows, run twice for stability:
  ```
  'lock the front door'                                   {'verb:lock': 0.99, 'verb:unlock': 0.01}
  'unlock the front door'                                 {'verb:lock': 0.01, 'verb:unlock': 0.98}
  'is the front door locked'                              {'verb:lock': 0.15, 'verb:unlock': 0.03}
  'if the blinds are closed lock the front door'          {'verb:lock': 0.98, 'verb:unlock': 0.01}

  'lock the front door'                                   {'verb:lock': 0.98, 'verb:unlock': 0.01}
  'unlock the front door'                                 {'verb:lock': 0.01, 'verb:unlock': 0.98}
  'is the front door locked'                              {'verb:lock': 0.15, 'verb:unlock': 0.03}
  'if the blinds are closed lock the front door'          {'verb:lock': 0.98, 'verb:unlock': 0.02}
  ```
  `verb:unlock` dropped from ≈0.86 (co-firing, above `verb_fire`=0.7) to ≈0.01 on both lock
  prompts, and vice versa — the first wording variant tried worked cleanly, no second attempt
  needed.

`uv run pytest -q` → 83 passed; `uv run ruff check packages/ golden/` → clean.

Full-corpus result: **23/24 agree (96%)**, p50=351 ms, p95=747 ms, cost=$0.0016 (38,019
tokens) — up from 21/24 (88%). Only "turn everything off" still fails (unchanged, pre-existing
model-variance issue, see "Known issues"). A first full run after these fixes landed at
22/24 (92%) because "turn off the downstairs lights" flaked on a borderline `domain:switch`
probability (0.65–0.67, close to the 0.7 `scope_fire` threshold) — re-running that row alone
passed, and a second full run confirmed 23/24; this is the same class of scope-threshold
variance already noted for "turn everything off", not a new defect.

### 2026-09-19 — final-review fix wave (no tuning)

Eleven review findings fixed across the engine (see
`.superpowers/sdd/2026-09-19-hunch-engine/final-fix-report.md`). Nothing in this wave touched
question wording or thresholds, by instruction. The one change that affects this corpus is to
the runner itself:

- `check()` now compares the **exact verb set** of the produced actions against `verb` /
  `verbs`, instead of asking only whether the expected verb appears somewhere. An engine that
  emits the right action *plus* a spurious one used to score a PASS.
- The row loop closes the HTTP client in a `finally` block.

Result: **22/24 agree (92%)**, p50=377 ms, p95=720 ms, cost=$0.0016 (37,419 tokens), against
23/24 before the wave. The one row that changed verdict, "open the blinds halfway", was
already producing the extra `open` action before this wave; only the check changed. Verified
with `--verbose`: `verb:open`=0.98 alongside `verb:set_position`=0.99, so both fire and both
resolve to `cover.living_blinds`. Separating them is a `phrasing` rewording, which this wave
was explicitly told not to attempt — see "Known issues".

## Corpus expectation corrections

- **`turn everything off`**: originally `{kind: confirm, verb: turn_off, reason:
  blast_radius}`. Changed to `{kind: resolved, verb: turn_off}`. The fixture home has only 10
  `turn_off`-capable entities (9 lights + 1 switch); `blast_radius` requires more targets than
  `EngineConfig.max_silent_targets` (20), which is an `EngineConfig` field, not a `Thresholds`
  field, and thus out of ruling C's tuning bounds. With `flag:collective` and `verb:turn_off`
  both typically ≥ 0.94, the engine's designed behavior on this fixture is to auto-resolve, not
  confirm — the original expectation assumed a larger real home than this fixture models.

## Known issues (not fixed — out of ruling C's bounds or inherent model variance)

1. **`open` co-fires with `set_position` on "open the blinds halfway".** `verb:open`=0.98 and
   `verb:set_position`=0.99, so the engine emits two actions: open the blinds, and set them to
   half. Tuning pass 1 already narrowed `set_position`'s phrasing to separate it from a *plain*
   open/close ("rather than simply fully open or closed"), which fixed "close the living room
   blinds"; a partial-position request such as "halfway" still reads as true for both verbs,
   because it genuinely is an opening action. Fixing it means rewording `open`/`close` to
   exclude a named partial position (or making partial-position requests suppress the plain
   verb in the resolver) — wording/threshold work that the final-review fix wave was told not
   to do. Reported, not papered over.
2. **"turn everything off" has high verb-probability variance.** Across repeated runs,
   `verb:turn_off` for this prompt was observed at both ~0.99 (fires cleanly, resolves as
   expected) and ~0.40–0.41 (spread thin across `turn_off`/`close`/`lock`/`disarm`, none
   clearing `verb_fire`=0.7, so the request escalates as `no_intent`). This looks like genuine
   sampling variance from the model on a deliberately vague, all-encompassing prompt rather
   than a wording defect — the verb's phrasing is already about as unambiguous as it can be
   ("directly turn a specific device or devices off"). Lowering `verb_fire` globally to
   accommodate this would reintroduce the cross-verb contamination fixed in tuning pass 1 (e.g.
   `set_position`/`close`, `lock`/`unlock`), so it was left alone. Re-running the corpus may
   show this row passing or failing depending on the draw. The same class of borderline-Noul
   variance was also observed once on "turn off the downstairs lights" (`domain:switch`≈0.65,
   close to the 0.7 `scope_fire` threshold) — see the 2026-09-19 follow-up tuning-log entry.

### Resolved

- ~~`score_to_value` off-by-one bug in `resolver.py`~~ — **fixed** 2026-09-19 (see tuning log).
  The TypeSafe Score primitive is 0-indexed over its `criteria` list (confirmed against
  `docs.typesafe.ai/primitives/score.md`), but `score_to_value` computed `idx = score - 1.0`,
  i.e. treated it as 1-indexed. Changed to `idx = score`.
- ~~`unlock` fires alongside `lock`~~ — **fixed** 2026-09-19 (see tuning log). Reworded both
  verbs' `phrasing` in `vocabulary.py` to explicitly contrast ("lock ... (not unlock)" /
  "unlock ... (not lock)") instead of both containing the bare word "lock". Confirmed
  `verb:unlock` dropped from ≈0.86 to ≈0.01 on lock prompts (and vice versa) across two runs.
