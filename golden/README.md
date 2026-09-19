# Golden corpus

24 prompts run against the real Jev API. This is the regression net for future model bumps —
when the pinned `jev-*` model changes, re-run this corpus before rolling it out.

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

Entries below are dated, one per change, in the order applied. Each records what changed, which
corpus rows it affected, and the agreement rate before → after a full run.

<!-- Populated during Step 5 tuning. -->
