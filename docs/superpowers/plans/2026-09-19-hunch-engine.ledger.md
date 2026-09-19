# SDD ledger — plan: docs/superpowers/plans/2026-09-19-hunch-engine.md
Spec: docs/superpowers/specs/2026-09-19-hunch-design.md (read; binding authority)
Branch: engine (in place, user chose feature branch over worktree). Base main = 69c9e19.
Baseline tests: none exist before Task 1 (fresh repo, docs only).

## Pre-flight conflict scan

| Tasks | Produces → consumes | Finding |
|---|---|---|
| T2→T3 | conftest.py `home` fixture; T3 appends `vocab` fixture | additive, consistent |
| T3→T4 | `Verb` used by `resolution.Action` | consistent |
| T4→T5 | `NoulQ/ChoiceQ/ScoreQ`, `NoulA/ChoiceA/ScoreA`, `Answers` ↔ `to_sdk_question/from_sdk_answer` | consistent |
| T4→T6 | `Trace.record/decide`, `Thresholds.flag`, `Answers.noul/choice` ↔ round1 | consistent |
| T6→T7 | `Shape` fields (positional in tests) order `(fired_verbs, scope_areas, scope_domains, area_probs, domain_probs, flags, scene, condition_domain)` | consistent across test_scope/test_round2/test_resolver |
| T7→T8 | `device_label` imported by round2 | consistent |
| T8→T9 | `Round2Plan`, `target_options` ↔ resolver | consistent |
| T9→T10 | `resolve(shape, plan, round2, config, trace)` ↔ engine | consistent |
| T10→T11 | `hunch.__all__` ↔ golden runner imports | all names exported |
| T4→T11 | T11 adds `Trace.input_tokens` + test assertion | additive |
| T1 self | root pyproject uses `[tool.uv] dev-dependencies` (deprecated in uv 0.12) | Ruling: implementer may use `[dependency-groups] dev` instead — same content — cost if wrong: none |
| T1 self | `test_no_ha_imports` greps the literal "homeassistant" in all src files | no task writes that literal into src; docstrings say "the integration" — consistent |
| T5 self | test constructs `TypeSafeRateLimitError.__new__(...)` | plan already names the fallback (`TypeSafeError("rate limited")`) |
| T10 self | device round and Round 2 both `trace.record(2, …)` | cosmetic; Ruling: accept — round index in trace is informational — cost if wrong: slightly ambiguous trace |
| T11 self | runner Step 2 contains a `toks = …` line the plan itself says to remove in Steps 3–4 | Ruling: implementer skips the throwaway line and lands directly on the Step 4 form — cost if wrong: none |

Scan complete; no spec conflicts. Proceeding to Task 1.

## Progress
Plugin: typesafe@typesafe-ai installed mid-session → skill file ~/.claude/plugins/cache/typesafe-ai/typesafe/0.5.7/skills/typesafe-ai/SKILL.md (docs pointers only, no tools). Hand to implementers of T5, T6, T8, T11.
Ruling: every Round 2 Choice (`target:<verb>`, `cond_subject`, `device_round`) gains a final option "none of these"; resolver treats it as no target (→ low_confidence escalate); engine's device round treats it as (). — TypeSafe guidance: "include a no-match outcome when nothing may fit" — cost if wrong: one extra option per Choice, slightly lower confidence on real picks. Carry into T8/T9/T10 dispatches; T8 test `options == ("Living room main", "Reading lamp")` becomes `(..., "none of these")`.
Task 1: dispatched (sonnet) at 7dd7c94
Task 1: implementer DONE_WITH_CONCERNS at 47348b4 (5111a53 scaffold, 47348b4 spike). Spike: n up to 800 OK, 289ms@50 / 414ms@200; exclusion-Noul separation collapsed for n≥25 — but candidates were unscoped and wording conflated "not targeted" with "excepted".
Task 1: reviewer dispatched (sonnet) on 7dd7c94..47348b4; implementer resumed in parallel for an addendum (scoped, sharper-wording variant at n=5..25 + root pyproject depends on hunch so plain `uv sync` works).
Ruling: exclusion Noul instruction in T8 must distinguish "explicitly named as an exception" from "not targeted" (spike wording caused false positives). Final wording depends on addendum result. — cost if wrong: exceptions misfire; golden corpus row covers it.
Task 1: addendum committed 81d318b (implementer DONE). Scoped + sharp-wording exclusion Nouls: 0.99 vs 0.02 separation at n=5/10/15/25, no degradation. Plain `uv sync` now installs hunch.
Ruling (supersedes earlier wording ruling): T8 exclusion Noul instruction = "The request in `request` names an exception — something that must NOT be affected. Is the candidate named '{name}' in area '{area}' that exception?" (verified in spike). Round 2 state candidates must carry `area`. — cost if wrong: none; measured.
Task 1: review on 7dd7c94..47348b4 → spec ✅; 1 Important (README quickstart `uv sync` didn't install hunch — already fixed by addendum 81d318b); ⚠️ trailers → verified by controller via git log, all three commits carry the trailer; ⚠️ README commit attribution → moot, README is in the brief's Files list.
Task 1: minor (deferred): spec §10 item 2 wording dense; guard test is a substring scan (plan-mandated); README placement judgment call.
Task 1: fix round 1/5 → scoped re-review dispatched on 47348b4..81d318b (sonnet).
Task 1: fix round 1/5 (1 addressed, 0 open — uv sync/README; addendum verified CORRECT; commits 47348b4..81d318b)
Task 1: complete (commits 7dd7c94..81d318b, review clean)
Task 2: dispatched (haiku) at 81d318b
Task 2: implementer DONE at f719456; reviewer dispatched (haiku) on 81d318b..f719456
Task 2: review → spec ✅, quality Approved; ⚠️ trailer verified by controller via git log.
Task 2: minor (deferred): test_entity_lookup doesn't exercise scene lookup (plan-authored test).
Task 2: complete (commits 81d318b..f719456, review clean)
Task 3: dispatched (haiku) at f719456
Task 3: implementer DONE at 58e9f78. Trailer reads "Claude Haiku 4.5" (the model that authored it) rather than the plan's literal.
Ruling: commit trailers name the model that actually authored the commit; the plan's Global Constraint literal is relaxed to "Co-Authored-By: <authoring model> <noreply@anthropic.com>". Honest attribution beats cosmetic uniformity; no history rewrite. — cost if wrong: mixed trailer names in git log.
Task 3: reviewer dispatched (haiku) on f719456..58e9f78
Task 3: review → spec ✅, quality Approved, no findings.
Task 3: complete (commits f719456..58e9f78, review clean)
Task 4: dispatched (haiku) at 58e9f78
Task 4: implementer DONE at e8c8f68; reviewer dispatched (haiku) on 58e9f78..e8c8f68
Task 4: review → spec ✅, quality Approved. minor (deferred): Trace.models appends per round (duplicates possible) — intentional, undocumented.
Task 4: complete (commits 58e9f78..e8c8f68, review clean)
Task 5: dispatched (sonnet) at e8c8f68
Task 5: implementer DONE_WITH_CONCERNS at be80eb8 — concern is a plan miscount (9 tests, not 10); no code issue. Rate-limit hazard didn't manifest.
Task 5: reviewer dispatched (sonnet) on e8c8f68..be80eb8
Task 5: review → spec ✅, quality Approved. ⚠️ real AsyncTypeSafeClient/RetryPolicy construction path untested (plan gap; golden runner T11 exercises it). minor (deferred): DecisionBackendError sets __cause__ directly (suppress_context footgun); FakeDecisionClient doesn't copy `state`.
Task 5: complete (commits e8c8f68..be80eb8, review clean)
Task 6: dispatched (sonnet) at be80eb8
Task 6: implementer DONE at 98d5f13; reviewer dispatched (sonnet) on be80eb8..98d5f13
Task 6: review → spec ✅, quality Approved; ⚠️ trailer verified by controller. minor (deferred): condition_domain Choice built even with zero domains.
Task 6: complete (commits be80eb8..98d5f13, review clean)
Task 7: dispatched (haiku) at 98d5f13
Task 7: implementer DONE at 057e628; reviewer dispatched (haiku) on 98d5f13..057e628
Task 7: review → spec ✅, quality Approved, no findings; ⚠️ trailer verified by controller.
Task 7: complete (commits 98d5f13..057e628, review clean)
Task 8: dispatched (sonnet) at 057e628 with rulings: NO_MATCH="none of these" appended to target:/cond_subject Choices; exclusion Noul uses spike-verified wording with area *name* (via home.area_by_id); Round 2 state `area` field carries the area name.
Task 8: implementer DONE at 7b7edef; reviewer dispatched (sonnet) on 057e628..7b7edef
Task 8: review → spec ✅; 2 Important: (1) plan-mandated — param verb with zero candidates still gets a param: question; (2) ruling C (area name in state) has no value assertion. minor (deferred): Round2Plan dicts mutable inside frozen dataclass; single-option singular reuses `collective` bucket (naming).
Ruling: finding (1) is a real plan defect — `params.append` moves after the `if not cands: continue` guard; resolver (T9) already guards on `param:` presence so no downstream change. — cost if wrong: none; a param with no target is unusable anyway.
Task 8: fix round 1/5 dispatched → resume implementer with both findings.
Task 8: fix round 1/5 (2 addressed, 0 open — params-after-guard + area-name assertion; commits 7b7edef..bf73672)
Task 8: complete (commits 057e628..bf73672, review clean)
Task 9: dispatched (sonnet) at bf73672 with ruling: resolver treats NO_MATCH from target:/cond_subject as "no target"/"no condition" (+1 test).
Task 9: implementer DONE at 16750e3; reviewer dispatched (sonnet) on bf73672..16750e3
Task 9: review → 2 Important: (1) score_to_value doesn't clamp scores < 1.0 (extrapolates negative) — real bug, fix; (2) plan-mandated: exclude branch contributes collective+has_exception flags even when one target survives.
Ruling on (2): intended. In the exclusion branch those two flags were relied on to *choose the exclude interpretation* regardless of how many entities survive, unlike the plain collective branch where a lone candidate is deterministic. Global-constraint wording ("collective only when len>1") applies to the plain collective branch. No change. — cost if wrong: single-survivor exclusions get slightly lower confidence.
Task 9: minor (deferred): scene short-circuit keys off verb name "activate"; _verb_prob falls back to 1.0 (invariant undocumented); fired verbs absent from plan silently contribute.
Task 9: fix round 1/5 dispatched → resume implementer with finding (1).
Task 9: fix round 1/5 (1 addressed, 0 open — score_to_value clamp; commits 16750e3..d29578b)
Task 9: complete (commits bf73672..d29578b, review clean)
Task 10: dispatched (sonnet) at d29578b with rulings: device_round Choice gets NO_MATCH last option → (); build_round2_state(prompt, plan, home) signature from T8.
Task 10: implementer DONE at 8c204e0; reviewer dispatched (sonnet) on d29578b..8c204e0
Task 10: review → spec ✅, quality Approved. minor (deferred): untested branches — device round over max_rounds, device round low-confidence non-NO_MATCH, multi-verb DeviceRound+Candidates composition; backslash continuation style; stray blank line in test_engine.py.
Task 10: complete (commits d29578b..8c204e0, review clean)
Task 11: dispatched (sonnet) at 8c204e0 with rulings: skip the throwaway `toks` line (land on Step 4 form); tuning may change Thresholds defaults / round1+round2 instruction wording only — no structural changes; record every tuning change in golden/README.md; NO_MATCH exists on target/cond_subject/device_round Choices.
Task 11: implementer DONE at 7d61579 (0f0c4bd corpus+runner+trace tokens; 7d61579 tuning). Golden: 13/24 → 21/24 (88%), p50 474ms, p95 686ms, $0.0016/run. Failing: 2 Score rows (score_to_value indexing off-by-one — Score appears 0-indexed), "turn everything off" (variance).
Task 11: reviewer dispatched (sonnet) on 8c204e0..7d61579.
Ruling: Jev Score is 0-indexed per docs.typesafe.ai/primitives/score.md (level number = position). `score_to_value` (T9) assumed 1-based — plan defect. Fix in the T11 fix round: `idx = score` (no -1), tests become 0.0→0, 5.0→100, 2.5→37.5, clamps at -0.5→0 and 6.0→100. — cost if wrong: every Score param off by one level; golden rows 5 & 13 are the check.
Ruling: `unlock` co-firing with "lock the front door" (0.86) is a safety-relevant wording defect in Verb.phrasing ("unlock a door or lock" reads as a list). Fix in the T11 fix round: rephrase lock/unlock phrasings to be mutually exclusive, e.g. lock → "lock (secure) a door so it cannot be opened", unlock → "unlock (release) a door so it can be opened"; re-run corpus rows 8/9/23 to confirm. — cost if wrong: unlock is CONFIRM tier so worst case is a spurious confirmation prompt.
Task 11: review → spec ✅, quality Approved; ⚠️ ruling A intra-commit history verified by controller (no throwaway line in 0f0c4bd); trailers verified.
Task 11: minor (deferred): no try/finally around client.aclose(); p95 index one rank low; targets check fragile if a `verbs`+`targets` row is added; ruff-only touches to 3 test files.
Task 11: fix round 1/5 dispatched (controller-originated, from golden data): score_to_value 0-indexing; lock/unlock phrasing mutual exclusivity. Resume implementer.
Task 11: fix round 1/5 (2 addressed, 0 open — Score 0-indexing, lock/unlock phrasing 0.99/0.01; commits 7d61579..b6a11cc). Golden 23/24 (96%), p50 351ms, p95 747ms.
Task 11: complete (commits 8c204e0..b6a11cc, review clean)
All 11 tasks complete. Final whole-branch review dispatched (opus) on 69c9e19..b6a11cc.
Final review (opus) → "With fixes": 1 Critical (KeyError/TypeError from Answers accessors escapes Engine.decide), 10 Important (#2 cross-verb confidence contamination; #3 device round expands entities not devices; #4 device_round+max_rounds=2 silently broken; #5 one ScopeEscalate verb aborts request; #6 scope_cap never applied to strict sets [spec defect]; #7 is_query flag dead [spec/impl mismatch]; #8 latency_budget_ms/request_timeout_ms dead; #9 golden check() blind to extra actions; #10 condition candidates unscoped/uncapped; #11 API exports/docstrings). Minors + deferred triage recorded in final-fix-brief.md.
Ruling #4: device round counts toward max_rounds (a round-trip is a round-trip); EngineConfig.__post_init__ raises ValueError when device_round and max_rounds < 3; blocked Round 2 escalates with reason "round_budget" not "low_confidence". — cost if wrong: config friction for device_round users.
Ruling #6: spec defect. Cap applies to ANY candidate set before it becomes a Choice: strict sets > scope_cap fall through device_round → clarify → escalate like widened ones. Spec §6 step 3 updated. — cost if wrong: more clarifications in big homes.
Ruling #7: drop `is_query` from FLAGS (query_state is already a verb with its own Noul; the flag was never read). Spec §6 step 1/2 updated. — cost if wrong: none observed; fewer questions.
Ruling #8: remove `request_timeout_ms` and `latency_budget_ms` from EngineConfig — timeout belongs to the injected client (TypeSafeDecisionClient.timeout_ms); latency is measured by the golden runner, not enforced in v1. Spec §4.2/§5.3 updated. — cost if wrong: integration config flow loses two knobs it can re-add.
Ruling _verb_prob fallback: 0.0 (fail closed), not 1.0.
Final fix wave: ONE dispatch (opus) with all findings; then one scoped re-review.
Final fix wave dispatched (opus) at b6a11cc; brief: final-fix-brief.md
Final fix wave: DONE_WITH_CONCERNS at 5f29d28 (11 commits, 107 tests, ruff clean). Golden 23/24 → 22/24: I9's stricter check exposed pre-existing `open`/`set_position` co-fire on "open the blinds halfway" (phrasing work, forbidden in this wave). Logged in golden/README Known issues.
Final fix wave: scoped re-review dispatched (sonnet) on b6a11cc..5f29d28.
Final fix wave: scoped re-review → all findings addressed, no new Critical/Important breakage; 107 passed, ruff clean, golden 22/24.
Final: parked — I8 partial: plan doc code sketches still mention removed config fields / is_query — Ruling: the plan is the historical execution record, the spec is the living contract and is updated; leave the plan as written. — cost if wrong: a future reader of the plan sees stale field names.
Final: parked — DeviceRound hit with rounds >= max_rounds still aborts the whole turn (pre-existing; I5-class) — Ruling: defer; guarded by EngineConfig.__post_init__ in practice. — cost if wrong: an over-eager clarification in a rare multi-verb case.
Final: parked — "open the blinds halfway" co-fires open/set_position (golden 22/24) — Ruling: phrasing tuning belongs to a follow-up golden pass, not this branch. — cost if wrong: a spurious `open` action (SAFE tier) alongside set_position.
Branch complete. Workspace deleted after rulings were extracted.
