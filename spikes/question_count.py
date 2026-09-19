"""Spike: how many questions fit in one Jev request, and how latency scales.

Run:  uv run python spikes/question_count.py
Needs TYPESAFE_API_KEY in .env (never printed).
"""

import asyncio
import os
import time

from dotenv import load_dotenv
from typesafe_sdk import AsyncTypeSafeClient, Noul, TypeSafeAPIError

MODEL = "jev-1.13.0"
PROMPT = "turn off all the lights downstairs except the one in the hallway"
AREAS = ["Kitchen", "Living room", "Hallway", "Bedroom", "Office", "Bathroom", "Garage", "Garden"]
DOMAINS = ["light", "switch", "cover", "climate", "lock", "media_player", "fan", "scene"]


def entity_name(i: int) -> str:
    return f"{AREAS[i % len(AREAS)]} {DOMAINS[i % len(DOMAINS)]} {i}"


def build_questions(n: int) -> dict[str, Noul]:
    return {
        f"exclude:{i}": Noul(
            instructions=f"Should the device named '{entity_name(i)}' be excluded from this request?"
        )
        for i in range(n)
    }


async def probe(client: AsyncTypeSafeClient, n: int) -> None:
    state = {
        "request": PROMPT,
        "candidates": [{"id": i, "name": entity_name(i)} for i in range(n)],
    }
    t0 = time.perf_counter()
    try:
        resp = await client.system_one(state=state, questions=build_questions(n), model=MODEL)
    except TypeSafeAPIError as exc:
        # NOTE: the SDK exposes the HTTP status as `exc.status`, not `exc.status_code`
        # (confirmed by inspecting typesafe_sdk._core.errors.TypeSafeAPIError on 0.7.0).
        print(f"n={n:4d}  ERROR status={getattr(exc, 'status', '?')} {type(exc).__name__}: {exc}")
        return
    dt = (time.perf_counter() - t0) * 1000
    tokens = resp.usage.input_tokens
    hallway = [k for k in resp.answers if "Hallway" in entity_name(int(k.split(":")[1]))]
    hallway_p = sum(resp.answers[k].noul for k in hallway) / max(len(hallway), 1)
    others_p = sum(resp.answers[k].noul for k in resp.answers if k not in hallway) / max(
        len(resp.answers) - len(hallway), 1
    )
    print(
        f"n={n:4d}  {dt:7.0f} ms  tokens={tokens}  model={resp.model}  "
        f"mean p(exclude|Hallway)={hallway_p:.2f}  mean p(exclude|other)={others_p:.2f}"
    )


async def main() -> None:
    load_dotenv()
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY not set — create .env first")
    async with AsyncTypeSafeClient(model=MODEL, timeout=30.0) as client:
        for n in (10, 25, 50, 100, 200, 400, 800):
            await probe(client, n)


if __name__ == "__main__":
    asyncio.run(main())
