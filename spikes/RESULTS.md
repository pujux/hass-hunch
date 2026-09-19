# Spike results: Jev question-count and latency scaling

Run against the real Jev API (`jev-1.13.0`) via `spikes/question_count.py`,
prompt `"turn off all the lights downstairs except the one in the hallway"`,
one `Noul("Should the device named '{name}' be excluded from this
request?")` per candidate.

## Table

| n   | latency (ms) | input tokens | mean p(exclude\|Hallway) | mean p(exclude\|other) |
|-----|-------------:|-------------:|-------------------------:|------------------------:|
| 10  |          775 |          725 |                      0.88 |                     0.60 |
| 25  |          317 |         1434 |                      0.69 |                     0.70 |
| 50  |          289 |         2615 |                      0.79 |                     0.70 |
| 100 |          500 |         4979 |                      0.68 |                     0.73 |
| 200 |          414 |        10005 |                      0.76 |                     0.74 |
| 400 |          703 |        20055 |                      0.75 |                     0.75 |
| 800 |         1131 |        40155 |                      0.74 |                     0.78 |

No request in the ladder errored; every `n` from 10 through 800 returned
`200` with a full set of answers.

## Conclusion

**(a) Max questions per request observed:** all rungs up to **n=800**
succeeded (no 422/413/anything else); the ladder simply ended at the
brief's largest value, so 800 is a floor on the real cap, not the cap
itself — input tokens scaled linearly (~50 tokens/question) and were still
only 40k at n=800, well under the documented 64k budget. **(b) Latency at
n=50 and n=200:** 289 ms and 414 ms respectively — both comfortably inside
the design's 600 ms `latency_budget_ms` and 1500 ms `request_timeout_ms`,
and latency does not scale linearly with n (800 questions took only ~1.1 s,
not ~16x the n=50 latency), so token count is not the dominant cost driver
in this range. **(c) Does separation degrade with n:** yes, sharply. The
mean p(exclude|Hallway) vs p(exclude|other) gap is a clear 0.28 at n=10 but
collapses to near-zero by n=25 and stays in the ±0.05 noise band through
n=800 (and inverts, i.e. "other" scores *higher* than "Hallway", at n=100
and n=800). This is the context-rot effect the design doc warns about
(§3): a single flat `Noul` per candidate is only informative when the
candidate set is very small (≈10). It does **not** hold up as a way to do
large-scale exclusion directly — Hunch's actual design already avoids this
by scoping candidates first (§6 Step 3) before asking any per-candidate
`Noul`, so Round 2's exclusion questions should only ever see the already-
narrowed candidate set, not hundreds of unrelated entities.

## SDK observations

Recorded against `typesafe-sdk==0.7.0` (confirmed via
`uv run python -c "import typesafe_sdk, inspect; ..."` after `uv sync
--all-packages`, since the root workspace's `uv sync` alone does not
install workspace members — see "Deviations from the brief" below).

- **Import names that worked, unchanged from the brief:**
  `from typesafe_sdk import AsyncTypeSafeClient, Noul, TypeSafeAPIError`.
- **`system_one` accepts a per-call `model=` kwarg**, confirmed by
  `inspect.signature`: `system_one(self, state, questions, *, model=None,
  retry=None, timeout=None, extra_headers=None, extra_body=None,
  response_model=None)`. `AsyncTypeSafeClient.__init__` also accepts
  `model=` as a client-wide default; the spike sets both (client default
  and per-call) as the brief's code already did.
- **`SystemOneResponse` fields:** `model: str`, `usage: Usage`,
  `answers: dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]` — a single
  discriminated-union dict keyed by question id, exactly as the brief's
  spike code assumed (`resp.answers[k].noul`). `resp.usage.input_tokens`
  and `resp.usage.output_tokens` are both populated ints, not `None`.
- **Per-answer attributes:**
  - `NoulAnswer`: `type="noul"`, `noul: float`.
  - `ChoiceAnswer`: `type="choice"`, `choice: str`, `confidence: float`,
    `probabilities: dict[str, float]`.
  - `ScoreAnswer`: `type="score"`, `score: float`, `confidence: float`,
    `legend: dict[int, ...]`, `probabilities: dict[int, float]`.
- **Exception class raised on a 4xx:** `TypeSafeAPIError` is the base
  class; the SDK raises specific subclasses per status
  (`TypeSafeBadRequestError`=400, `TypeSafeAuthenticationError`=401,
  `TypeSafePermissionDeniedError`=403, `TypeSafeNotFoundError`=404,
  `TypeSafeUnprocessableEntityError`=422, `TypeSafeRateLimitError`=429,
  `TypeSafeInternalServerError`=5xx). Triggered deliberately and cheaply
  with an invalid `model=` name (one `Noul`, no candidate fan-out):
  `TypeSafeBadRequestError`, `status=400`,
  `body={'detail': {'error_type': 'api_usage_error', 'message': 'Unknown
  model: not-a-real-model'}}`.
  **Important deviation from the brief:** the HTTP status is exposed as
  `exc.status`, **not** `exc.status_code` — the brief's spike code used
  `getattr(exc, 'status_code', '?')`, which would silently print `'?'`
  for every error. The spike in this repo uses `exc.status`.
  (Also tried a `Choice` with a single option as ruling 3 suggested —
  that did **not** error; it resolved with `confidence=1.0`, so the
  invalid-model probe was used instead as the cheap, reliable 4xx.)

### Deviations from the brief (adaptations, per controller ruling 3)

1. `getattr(exc, 'status_code', '?')` → `getattr(exc, 'status', '?')` in
   `spikes/question_count.py`, since the real attribute is `status`.
2. `uv sync` at the workspace root only installs the `dev` dependency
   group (root has `[tool.uv] package = false` per ruling 2, and nothing
   in the root depends on `packages/hunch`), so `typesafe-sdk` was never
   installed and `import typesafe_sdk` failed. Running
   `uv sync --all-packages` installs every workspace member, including
   `hunch` and its `typesafe-sdk` dependency. All later `uv run` commands
   in this task were run against that environment.

Everything else in the brief's spike script (state shape, question
construction, ladder values, print format) matched the real API exactly.
