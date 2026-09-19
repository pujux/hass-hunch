"""Run the golden corpus against the real Jev API. Gated on TYPESAFE_API_KEY; not part of CI.

Run:  uv run python golden/run_golden.py [--model jev-1.13.0] [--only 'kitchen'] [--verbose]
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import pathlib
import statistics
import sys
import time

import yaml
from dotenv import load_dotenv
from hunch import (
    DEFAULT_VOCABULARY,
    Engine,
    EngineConfig,
    Escalate,
    NeedsClarification,
    NeedsConfirmation,
    Resolved,
    TypeSafeDecisionClient,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRICE_PER_M_TOKENS = 0.042


def load_home():
    spec = importlib.util.spec_from_file_location(
        "conftest", ROOT / "packages/hunch/tests/conftest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.home.__wrapped__()  # unwrap the pytest fixture


def kind_of(r) -> str:
    return {
        Resolved: "resolved",
        NeedsConfirmation: "confirm",
        NeedsClarification: "clarify",
        Escalate: "escalate",
    }[type(r)]


def check(row, r) -> list[str]:
    exp, problems = row["expect"], []
    if kind_of(r) != exp["kind"]:
        problems.append(f"kind {kind_of(r)} != {exp['kind']}")
        return problems
    actions = getattr(r, "actions", ())
    names = {a.verb.name for a in actions}
    # `verb` means exactly that verb and no other: a spurious extra action is a failure,
    # not a pass. `verbs` (plural) is the same check over a set.
    if "verb" in exp and names != {exp["verb"]}:
        problems.append(f"verbs {sorted(names)} != {{{exp['verb']!r}}}")
    if "verbs" in exp and names != set(exp["verbs"]):
        problems.append(f"verbs {sorted(names)} != {exp['verbs']}")
    if "targets" in exp:
        wanted = [a for a in actions if "verbs" in exp or a.verb.name == exp.get("verb")]
        got = {e.entity_id for a in wanted for e in a.targets}
        if got != set(exp["targets"]):
            problems.append(f"targets {sorted(got)} != {sorted(exp['targets'])}")
    if "reason" in exp and getattr(r, "reason", None) != exp["reason"]:
        problems.append(f"reason {getattr(r, 'reason', None)} != {exp['reason']}")
    for key, (lo, hi) in exp.get("params", {}).items():
        vals = [a.params.get(key) for a in actions if key in a.params]
        if not vals or not (lo <= vals[0] <= hi):
            problems.append(f"param {key}={vals} not in [{lo}, {hi}]")
    if "condition" in exp:
        c = getattr(r, "condition", None)
        if c is None or c.subject.entity_id != exp["condition"]["subject"] \
                or c.expected_state != exp["condition"]["state"]:
            problems.append(f"condition {c} != {exp['condition']}")
    return problems


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="jev-1.13.0")
    ap.add_argument("--only", default=None)
    ap.add_argument("--verbose", action="store_true", help="dump r.trace.to_dict() for FAIL rows")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY not set — create .env first", file=sys.stderr)
        return 2

    rows = yaml.safe_load((ROOT / "golden/corpus.yaml").read_text())
    if args.only:
        rows = [r for r in rows if args.only in r["prompt"]]
    home = load_home()
    client = TypeSafeDecisionClient(model=args.model, timeout_ms=5000)
    engine = Engine(client, DEFAULT_VOCABULARY, EngineConfig(model=args.model))

    ok, latencies, tokens = 0, [], 0
    try:
        for row in rows:
            t0 = time.perf_counter()
            r = await engine.decide(home, row["prompt"])
            ms = (time.perf_counter() - t0) * 1000
            latencies.append(ms)
            toks = sum(t or 0 for t in r.trace.input_tokens)
            tokens += toks
            problems = check(row, r)
            ok += not problems
            mark = "PASS" if not problems else "FAIL"
            rounds = len(r.trace.models)
            print(f"{mark}  {ms:6.0f} ms  rounds={rounds}  {row['prompt']!r}")
            for pr in problems:
                print(f"        - {pr}")
            if problems and args.verbose:
                print(json.dumps(r.trace.to_dict(), indent=1))
    finally:
        await client.aclose()

    agreement = ok / max(len(rows), 1)
    p50 = statistics.median(latencies)
    p95 = (
        sorted(latencies)[int(len(latencies) * 0.95) - 1]
        if len(latencies) >= 20
        else max(latencies)
    )
    print(
        f"\n{ok}/{len(rows)} agree ({agreement:.0%})  p50={p50:.0f} ms  p95={p95:.0f} ms  "
        f"cost=${tokens / 1e6 * PRICE_PER_M_TOKENS:.4f}  tokens={tokens}"
    )
    return 0 if agreement >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
