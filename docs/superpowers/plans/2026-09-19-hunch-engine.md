# Hunch Engine Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `hunch`, the pure-Python engine that turns a prompt plus a snapshot of a home into a typed `Resolution` by asking Jev narrow parallel questions and composing the answers in code.

**Architecture:** Frozen dataclasses for the home model, vocabulary, questions and results. Pure functions build Round 1 (request shape) and Round 2 (targets/params) question sets; a `DecisionClient` protocol isolates the TypeSafe SDK; a `ScopePolicy` chain narrows candidates between rounds; a `Resolver` applies thresholds and risk tiers; `Engine` orchestrates and never executes anything.

**Tech Stack:** Python 3.13 (uv-managed), `typesafe-sdk` 0.7.0, `python-dotenv`, `pytest`, `pytest-asyncio`, `ruff`. No Home Assistant imports anywhere in this package.

**Spec:** `docs/superpowers/specs/2026-09-19-hunch-design.md`

## Global Constraints

- Python `>=3.13,<3.14` (HA 2026.8 target). Managed by uv: `.python-version` = `3.13`.
- Runtime dependencies of `hunch`: `typesafe-sdk>=0.7,<0.8` only. `python-dotenv` is a dev/spike dependency.
- Zero `homeassistant` imports in `packages/hunch/`. Enforce with a test.
- All public data types are `@dataclass(frozen=True)`.
- The engine's only side effect is the network call to Jev. `Engine.decide` never executes actions.
- Model id is mandatory in `EngineConfig` and pinned; every response's `model` field is recorded in the trace.
- Confidence of an action = `min` over contributing probabilities/confidences. Never averaged.
- No free text anywhere in a `Resolution`.
- Only Assist-exposed entities enter `HomeModel` — a rule for the integration; the engine trusts its input.
- `TYPESAFE_API_KEY` lives in `.env` (gitignored). Never print it, never commit it, never pass it as a literal.
- Commit message trailer on every commit: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## SDK facts used by this plan (typesafe-sdk 0.7.0)

```python
from typesafe_sdk import AsyncTypeSafeClient, Noul, Choice, Score, RetryPolicy
from typesafe_sdk import TypeSafeError, TypeSafeAPIError, TypeSafeRateLimitError, TypeSafeAPITimeoutError

async with AsyncTypeSafeClient(api_key=..., model="jev-1.13.0", timeout=1.5, retry=RetryPolicy(max_retries=2, timeout=1.5)) as client:
    resp = await client.system_one(state={...}, questions={"q": Noul(instructions="...")}, model="jev-1.13.0")
resp.model                                    # str
resp.usage.input_tokens                       # int | None
resp.answers["q"].type                        # "noul" | "choice" | "score"
resp.answers["q"].noul                        # float 0..1 (NoulAnswer)
resp.answers["q"].choice / .confidence / .probabilities   # ChoiceAnswer: str, float, dict[str, float]
resp.answers["q"].score / .confidence / .probabilities    # ScoreAnswer: float, float, dict[int, float]
```

`Choice(instructions=str, criteria=dict[str, str | None])`, `Score(instructions=str, criteria=list[str])` (min 2 levels), `Noul(instructions=str)`. State may be a string, JSON object or array; 64k tokens total, 32k for state plus the longest question. Errors: 401, 422, 429, 529; SDK retries 408/429/5xx with exponential backoff (0.5 s → 5 s, jitter 0.25), default 2 retries, default 30 s total budget.

## File structure

```
hass-hunch/
├── pyproject.toml                    # uv workspace root (members: packages/*)
├── .python-version                   # 3.13
├── packages/hunch/
│   ├── pyproject.toml                # the library; hatchling build
│   ├── src/hunch/
│   │   ├── __init__.py               # public re-exports
│   │   ├── model.py                  # Floor, Area, Entity, HomeModel
│   │   ├── vocabulary.py             # Risk, ScoreSpec, ChoiceSpec, Verb, Vocabulary, DEFAULT_VOCABULARY
│   │   ├── questions.py              # NoulQ/ChoiceQ/ScoreQ, NoulA/ChoiceA/ScoreA, Answers, JSON
│   │   ├── config.py                 # Thresholds, EngineConfig
│   │   ├── resolution.py             # Trace, Action, Condition, Resolved, NeedsConfirmation, NeedsClarification, Escalate
│   │   ├── client.py                 # DecisionClient protocol, DecisionBackendError, FakeDecisionClient, TypeSafeDecisionClient
│   │   ├── round1.py                 # build_round1_state, build_round1_questions, Shape, interpret_round1
│   │   ├── scope.py                  # strict_candidates, ranked_widen, scope_candidates + result types
│   │   ├── round2.py                 # build_round2_state, build_round2_questions, target_options
│   │   ├── resolver.py               # resolve
│   │   └── engine.py                 # Engine
│   └── tests/
│       ├── conftest.py               # `home` fixture (a small realistic house), `vocab`, `thresholds`
│       ├── test_no_ha_imports.py
│       ├── test_model.py
│       ├── test_vocabulary.py
│       ├── test_questions.py
│       ├── test_client.py
│       ├── test_round1.py
│       ├── test_scope.py
│       ├── test_round2.py
│       ├── test_resolver.py
│       └── test_engine.py
├── spikes/
│   └── question_count.py             # Task 1: real-API spike (throwaway, kept for reference)
└── golden/
    ├── corpus.yaml                   # (prompt, fixture, expected) rows
    └── run_golden.py                 # real-API runner, gated on TYPESAFE_API_KEY
```

Question-id conventions (strings, used by round1/round2/resolver — keep exact):

| Id pattern | Primitive | Where |
|---|---|---|
| `verb:<verb.name>` | Noul | Round 1 |
| `floor:<floor_id>` / `area:<area_id>` / `domain:<domain>` | Noul | Round 1 |
| `flag:collective` `flag:has_exception` `flag:has_condition` `flag:is_query` `flag:has_timing` `flag:is_destructive` | Noul | Round 1 |
| `scene` | Choice over scene names + `none` | Round 1 (only if `home.scenes`) |
| `condition_domain` | Choice over domains + `none` | Round 1 |
| `device_round` | Choice over device labels | optional extra round |
| `exclude:<verb>:<entity_id>` | Noul | Round 2 |
| `target:<verb>` | Choice over target labels | Round 2 |
| `param:<verb>` | Score or Choice | Round 2 |
| `cond_subject` / `cond_state` | Choice | Round 2 |
| `confirm_reply` | Choice `affirmative`/`negative`/`other` | integration, later |

---

### Task 1: Project scaffold and the Jev question-count spike

**Files:**
- Create: `pyproject.toml`, `.python-version`, `packages/hunch/pyproject.toml`, `packages/hunch/src/hunch/__init__.py`, `packages/hunch/tests/test_no_ha_imports.py`, `spikes/question_count.py`, `README.md`

**Interfaces:**
- Produces: an installable `hunch` package (empty), a runnable test suite, and a spike result recorded in `spikes/RESULTS.md`.

- [ ] **Step 1: Verify uv is installed and create the workspace**

Run: `uv --version` — if missing, stop and ask the user to `brew install uv`.

Create `.python-version`:
```
3.13
```

Create root `pyproject.toml`:
```toml
[project]
name = "hass-hunch-workspace"
version = "0.0.0"
requires-python = ">=3.13,<3.14"

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv]
dev-dependencies = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "python-dotenv>=1.0",
    "ruff>=0.6",
    "pyyaml>=6.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["packages/hunch/tests"]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

Create `packages/hunch/pyproject.toml`:
```toml
[project]
name = "hunch"
version = "0.1.0"
description = "Typed-decision fast path for Home Assistant requests, powered by Jev"
requires-python = ">=3.13,<3.14"
dependencies = ["typesafe-sdk>=0.7,<0.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/hunch"]
```

Create `packages/hunch/src/hunch/__init__.py`:
```python
"""Hunch: code calculates, Jev judges."""

__version__ = "0.1.0"
```

Run: `uv sync` — expected: creates `.venv` with Python 3.13, installs `typesafe-sdk` and dev deps, writes `uv.lock`.

- [ ] **Step 2: Write the no-HA-imports guard test**

`packages/hunch/tests/test_no_ha_imports.py`:
```python
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "hunch"


def test_hunch_never_imports_homeassistant():
    offenders = [
        p
        for p in SRC.rglob("*.py")
        if "homeassistant" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"HA imports found in engine library: {offenders}"
```

Run: `uv run pytest -q` — expected: `1 passed`.

- [ ] **Step 3: Commit the scaffold**

```bash
git add pyproject.toml .python-version uv.lock packages/hunch/pyproject.toml packages/hunch/src/hunch/__init__.py packages/hunch/tests/test_no_ha_imports.py
git commit -m "Scaffold uv workspace and empty hunch package

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 4: Write the spike script**

The question we need answered: how many Noul questions fit in one `system_one` call, and how does latency scale? This is a throwaway; it deliberately does not use `hunch` code.

`spikes/question_count.py`:
```python
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
        print(f"n={n:4d}  ERROR status={getattr(exc, 'status_code', '?')} {type(exc).__name__}")
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
```

- [ ] **Step 5: Run the spike**

Run: `uv run python spikes/question_count.py`

Expected: one line per `n`. Record the largest `n` that succeeds, latency per `n`, whether `p(exclude|Hallway)` stays clearly above `p(exclude|other)` as `n` grows (that's the context-rot check), and any 422/413 errors. If the key is missing the script exits with a message; do not proceed without a result.

- [ ] **Step 6: Record results**

Create `spikes/RESULTS.md` with a table of `n`, ms, tokens, hallway-vs-other means, and a one-paragraph conclusion answering: (a) max questions per request observed, (b) latency at n=50 and n=200, (c) does separation degrade with n. Update `docs/superpowers/specs/2026-09-19-hunch-design.md` §10 open question 2 with the finding.

- [ ] **Step 7: Commit**

```bash
git add spikes/question_count.py spikes/RESULTS.md docs/superpowers/specs/2026-09-19-hunch-design.md
git commit -m "Spike: Jev question-count and latency scaling

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Home model

**Files:**
- Create: `packages/hunch/src/hunch/model.py`, `packages/hunch/tests/conftest.py`, `packages/hunch/tests/test_model.py`

**Interfaces:**
- Produces:
  - `Floor(floor_id: str, name: str, area_ids: tuple[str, ...])`
  - `Area(area_id: str, name: str, aliases: tuple[str, ...], floor_id: str | None)`
  - `Entity(entity_id, domain, name, aliases, area_id, device_id, device_name, verbs: frozenset[str], state: str | None)`
  - `HomeModel(floors, areas, entities, scenes)` with `.area_by_id(area_id) -> Area | None`, `.areas_for_floor(floor_id) -> tuple[str, ...]`, `.domains -> tuple[str, ...]` (sorted), `.entity_by_id(entity_id) -> Entity | None`
  - test fixture `home` — the "Pufler house" used by every later test.

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/conftest.py`:
```python
import pytest

from hunch.model import Area, Entity, Floor, HomeModel

LIGHT_VERBS = frozenset({"turn_on", "turn_off", "set_brightness", "query_state"})
COVER_VERBS = frozenset({"open", "close", "set_position", "query_state"})
LOCK_VERBS = frozenset({"lock", "unlock", "query_state"})
SWITCH_VERBS = frozenset({"turn_on", "turn_off", "query_state"})
CLIMATE_VERBS = frozenset({"set_temperature", "query_state"})
SCENE_VERBS = frozenset({"activate"})


def _e(entity_id, name, area, device=None, verbs=LIGHT_VERBS, state="off", aliases=()):
    domain = entity_id.split(".")[0]
    return Entity(
        entity_id=entity_id,
        domain=domain,
        name=name,
        aliases=tuple(aliases),
        area_id=area,
        device_id=f"dev_{device}" if device else None,
        device_name=device,
        verbs=verbs,
        state=state,
    )


@pytest.fixture
def home() -> HomeModel:
    return HomeModel(
        floors=(
            Floor("downstairs", "Downstairs", ("kitchen", "living", "hallway")),
            Floor("upstairs", "Upstairs", ("bedroom", "office")),
        ),
        areas=(
            Area("kitchen", "Kitchen", (), "downstairs"),
            Area("living", "Living room", ("lounge",), "downstairs"),
            Area("hallway", "Hallway", (), "downstairs"),
            Area("bedroom", "Bedroom", (), "upstairs"),
            Area("office", "Office", ("study",), "upstairs"),
        ),
        entities=(
            _e("light.kitchen_ceiling", "Kitchen ceiling", "kitchen", "Kitchen ceiling"),
            _e("light.kitchen_counter", "Counter strip", "kitchen", "Counter strip"),
            _e("switch.fridge", "Fridge", "kitchen", "Fridge", SWITCH_VERBS, "on"),
            _e("light.living_main", "Living room main", "living", "Living room main"),
            _e("light.reading_lamp", "Reading lamp", "living", "Reading lamp", aliases=("lamp",)),
            _e("cover.living_blinds", "Living room blinds", "living", "Blinds", COVER_VERBS, "open"),
            _e("light.hallway", "Hallway light", "hallway", "Hallway light"),
            _e("lock.front_door", "Front door", "hallway", "Front door", LOCK_VERBS, "locked"),
            _e("light.bedroom_left", "Bedside left", "bedroom", "Bedside lamps"),
            _e("light.bedroom_right", "Bedside right", "bedroom", "Bedside lamps"),
            _e("climate.bedroom", "Bedroom thermostat", "bedroom", "Thermostat", CLIMATE_VERBS, "heat"),
            _e("light.office_desk", "Desk lamp", "office", "Desk lamp"),
            _e("light.christmas_tree", "Christmas tree", None, None),
        ),
        scenes=(
            _e("scene.movie_night", "Movie night", "living", None, SCENE_VERBS, None),
            _e("script.goodnight", "Goodnight", None, None, SCENE_VERBS, None),
        ),
    )
```

`packages/hunch/tests/test_model.py`:
```python
import dataclasses

import pytest

from hunch.model import Entity, HomeModel


def test_home_model_is_frozen(home: HomeModel):
    with pytest.raises(dataclasses.FrozenInstanceError):
        home.floors = ()  # type: ignore[misc]


def test_area_lookup(home: HomeModel):
    assert home.area_by_id("living").name == "Living room"
    assert home.area_by_id("nope") is None


def test_areas_for_floor(home: HomeModel):
    assert home.areas_for_floor("downstairs") == ("kitchen", "living", "hallway")
    assert home.areas_for_floor("attic") == ()


def test_domains_are_sorted_and_exclude_scenes(home: HomeModel):
    assert home.domains == ("climate", "cover", "light", "lock", "switch")


def test_entity_lookup(home: HomeModel):
    e = home.entity_by_id("light.reading_lamp")
    assert isinstance(e, Entity)
    assert e.aliases == ("lamp",)
    assert home.entity_by_id("light.none") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_model.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.model'`.

- [ ] **Step 3: Implement `model.py`**

```python
"""Immutable per-request snapshot of a home. Built by the integration; hand-built in tests."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class Floor:
    floor_id: str
    name: str
    area_ids: tuple[str, ...]


@dataclass(frozen=True)
class Area:
    area_id: str
    name: str
    aliases: tuple[str, ...]
    floor_id: str | None


@dataclass(frozen=True)
class Entity:
    entity_id: str
    domain: str
    name: str
    aliases: tuple[str, ...]
    area_id: str | None
    device_id: str | None
    device_name: str | None
    verbs: frozenset[str]
    state: str | None


@dataclass(frozen=True)
class HomeModel:
    floors: tuple[Floor, ...]
    areas: tuple[Area, ...]
    entities: tuple[Entity, ...]
    scenes: tuple[Entity, ...]

    def area_by_id(self, area_id: str) -> Area | None:
        return self._areas.get(area_id)

    def areas_for_floor(self, floor_id: str) -> tuple[str, ...]:
        for floor in self.floors:
            if floor.floor_id == floor_id:
                return floor.area_ids
        return ()

    def entity_by_id(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    @cached_property
    def domains(self) -> tuple[str, ...]:
        return tuple(sorted({e.domain for e in self.entities}))

    @cached_property
    def _areas(self) -> dict[str, Area]:
        return {a.area_id: a for a in self.areas}

    @cached_property
    def _entities(self) -> dict[str, Entity]:
        return {e.entity_id: e for e in (*self.entities, *self.scenes)}
```

Note: `cached_property` on a frozen dataclass works because it writes to `__dict__` directly, bypassing `__setattr__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_model.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/model.py packages/hunch/tests/conftest.py packages/hunch/tests/test_model.py
git commit -m "Add HomeModel dataclasses and shared test fixture

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Vocabulary and default verbs

**Files:**
- Create: `packages/hunch/src/hunch/vocabulary.py`, `packages/hunch/tests/test_vocabulary.py`
- Modify: `packages/hunch/tests/conftest.py` (add `vocab` fixture)

**Interfaces:**
- Produces:
  - `class Risk(Enum): SAFE, CONFIRM, DESTRUCTIVE`
  - `ScoreSpec(name: str, levels: tuple[str, ...])`, `ChoiceSpec(name: str, options: tuple[str, ...])`, `ParamSpec = ScoreSpec | ChoiceSpec`
  - `Verb(name, domains: frozenset[str], param: ParamSpec | None, risk: Risk, intent: str, phrasing: str, is_query: bool = False)`
  - `Vocabulary(verbs: tuple[Verb, ...])` with `.by_name(name) -> Verb`, `.names -> tuple[str, ...]`
  - `DEFAULT_VOCABULARY: Vocabulary`
  - `verbs_for_domain(domain: str, vocabulary: Vocabulary) -> frozenset[str]`

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_vocabulary.py`:
```python
import pytest

from hunch.vocabulary import (
    DEFAULT_VOCABULARY,
    ChoiceSpec,
    Risk,
    ScoreSpec,
    Verb,
    Vocabulary,
    verbs_for_domain,
)


def test_default_vocabulary_has_expected_core_verbs():
    names = set(DEFAULT_VOCABULARY.names)
    assert {"turn_on", "turn_off", "set_brightness", "open", "close", "lock", "unlock",
            "set_temperature", "activate", "query_state"} <= names


def test_lock_and_unlock_are_confirm_tier():
    assert DEFAULT_VOCABULARY.by_name("lock").risk is Risk.CONFIRM
    assert DEFAULT_VOCABULARY.by_name("unlock").risk is Risk.CONFIRM


def test_nothing_ships_destructive():
    assert all(v.risk is not Risk.DESTRUCTIVE for v in DEFAULT_VOCABULARY.verbs)


def test_brightness_is_a_score_param():
    v = DEFAULT_VOCABULARY.by_name("set_brightness")
    assert isinstance(v.param, ScoreSpec)
    assert v.param.name == "brightness_pct"
    assert 2 <= len(v.param.levels) <= 10


def test_query_state_is_flagged_as_query():
    assert DEFAULT_VOCABULARY.by_name("query_state").is_query is True


def test_verbs_for_domain():
    assert verbs_for_domain("light", DEFAULT_VOCABULARY) == frozenset(
        {"turn_on", "turn_off", "set_brightness", "query_state"}
    )
    assert verbs_for_domain("lock", DEFAULT_VOCABULARY) == frozenset({"lock", "unlock", "query_state"})
    assert verbs_for_domain("unknown_domain", DEFAULT_VOCABULARY) == frozenset({"query_state"})


def test_by_name_raises_on_unknown():
    with pytest.raises(KeyError):
        DEFAULT_VOCABULARY.by_name("teleport")


def test_vocabulary_extension_keeps_frozen_semantics():
    extra = Verb("party", frozenset({"light"}), ChoiceSpec("mode", ("disco", "chill")),
                 Risk.SAFE, "script.party", "start a party mode")
    v2 = Vocabulary(DEFAULT_VOCABULARY.verbs + (extra,))
    assert v2.by_name("party").intent == "script.party"
    assert "party" not in DEFAULT_VOCABULARY.names
```

Add to `conftest.py`:
```python
from hunch.vocabulary import DEFAULT_VOCABULARY, Vocabulary


@pytest.fixture
def vocab() -> Vocabulary:
    return DEFAULT_VOCABULARY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_vocabulary.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.vocabulary'`.

- [ ] **Step 3: Implement `vocabulary.py`**

```python
"""The verb set: what the fast path is allowed to do, and how risky each thing is."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from functools import cached_property


class Risk(Enum):
    SAFE = auto()         # auto-executes above threshold
    CONFIRM = auto()      # always asks first
    DESTRUCTIVE = auto()  # never executes from the fast path; escalates


@dataclass(frozen=True)
class ScoreSpec:
    name: str                  # parameter key in Action.params
    levels: tuple[str, ...]    # 2..10 ordered descriptions; index maps to a value via `values`
    values: tuple[float, ...]  # same length as levels; the number each level stands for


@dataclass(frozen=True)
class ChoiceSpec:
    name: str
    options: tuple[str, ...]


ParamSpec = ScoreSpec | ChoiceSpec


@dataclass(frozen=True)
class Verb:
    name: str
    domains: frozenset[str]
    param: ParamSpec | None
    risk: Risk
    intent: str
    phrasing: str
    is_query: bool = False


@dataclass(frozen=True)
class Vocabulary:
    verbs: tuple[Verb, ...]

    def by_name(self, name: str) -> Verb:
        return self._by_name[name]

    @cached_property
    def names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.verbs)

    @cached_property
    def _by_name(self) -> dict[str, Verb]:
        return {v.name: v for v in self.verbs}


def verbs_for_domain(domain: str, vocabulary: Vocabulary) -> frozenset[str]:
    return frozenset(v.name for v in vocabulary.verbs if domain in v.domains or v.is_query)


_ON_OFF = frozenset({"light", "switch", "fan", "media_player", "climate"})
_BRIGHTNESS = ScoreSpec(
    "brightness_pct",
    ("off", "very dim", "dim", "medium", "bright", "full"),
    (0, 10, 25, 50, 75, 100),
)
_POSITION = ScoreSpec(
    "position",
    ("fully closed", "mostly closed", "half open", "mostly open", "fully open"),
    (0, 25, 50, 75, 100),
)
_TEMPERATURE = ScoreSpec(
    "temperature",
    ("cold (16°C)", "cool (18°C)", "mild (20°C)", "warm (22°C)", "hot (24°C)"),
    (16, 18, 20, 22, 24),
)
_VOLUME = ScoreSpec(
    "volume_level",
    ("mute", "quiet", "medium", "loud", "max"),
    (0.0, 0.2, 0.5, 0.8, 1.0),
)

DEFAULT_VOCABULARY = Vocabulary(
    (
        Verb("turn_on", _ON_OFF, None, Risk.SAFE, "HassTurnOn", "turn something on"),
        Verb("turn_off", _ON_OFF, None, Risk.SAFE, "HassTurnOff", "turn something off"),
        Verb("set_brightness", frozenset({"light"}), _BRIGHTNESS, Risk.SAFE,
             "HassLightSet", "set how bright a light is"),
        Verb("open", frozenset({"cover"}), None, Risk.SAFE, "HassOpenCover",
             "open blinds, shades, curtains or a garage door"),
        Verb("close", frozenset({"cover"}), None, Risk.SAFE, "HassCloseCover",
             "close blinds, shades, curtains or a garage door"),
        Verb("set_position", frozenset({"cover"}), _POSITION, Risk.SAFE, "HassSetPosition",
             "set how far open blinds, shades or curtains are"),
        Verb("lock", frozenset({"lock"}), None, Risk.CONFIRM, "HassLock", "lock a door or lock"),
        Verb("unlock", frozenset({"lock"}), None, Risk.CONFIRM, "HassUnlock",
             "unlock a door or lock"),
        Verb("set_temperature", frozenset({"climate"}), _TEMPERATURE, Risk.SAFE,
             "HassClimateSetTemperature", "set a target temperature or make it warmer or cooler"),
        Verb("set_volume", frozenset({"media_player"}), _VOLUME, Risk.SAFE,
             "HassSetVolume", "change the volume of a speaker or TV"),
        Verb("media_pause", frozenset({"media_player"}), None, Risk.SAFE, "HassMediaPause",
             "pause playback"),
        Verb("media_play", frozenset({"media_player"}), None, Risk.SAFE, "HassMediaUnpause",
             "resume or start playback"),
        Verb("arm", frozenset({"alarm_control_panel"}), None, Risk.CONFIRM,
             "HassAlarmArm", "arm the alarm system"),
        Verb("disarm", frozenset({"alarm_control_panel"}), None, Risk.CONFIRM,
             "HassAlarmDisarm", "disarm the alarm system"),
        Verb("activate", frozenset({"scene", "script"}), None, Risk.SAFE, "HassTurnOn",
             "activate a scene or run a script by name"),
        Verb("query_state", frozenset(), None, Risk.SAFE, "HassGetState",
             "ask whether something is on, off, open, closed, locked, or what its value is",
             is_query=True),
    )
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_vocabulary.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/vocabulary.py packages/hunch/tests/test_vocabulary.py packages/hunch/tests/conftest.py
git commit -m "Add Vocabulary with default verbs and risk tiers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Question, answer, config and resolution types

**Files:**
- Create: `packages/hunch/src/hunch/questions.py`, `packages/hunch/src/hunch/config.py`, `packages/hunch/src/hunch/resolution.py`, `packages/hunch/tests/test_questions.py`
- Modify: `packages/hunch/tests/conftest.py` (add `thresholds`, `config` fixtures)

**Interfaces:**
- Produces (questions.py): `JSON = dict[str, Any]`; `NoulQ(instructions: str)`, `ChoiceQ(instructions: str, options: tuple[str, ...])`, `ScoreQ(instructions: str, levels: tuple[str, ...])`, `Question = NoulQ | ChoiceQ | ScoreQ`; `NoulA(probability: float)`, `ChoiceA(choice: str, confidence: float, probabilities: Mapping[str, float])`, `ScoreA(score: float, confidence: float, probabilities: Mapping[int, float])`, `Answer = NoulA | ChoiceA | ScoreA`; `Answers(model: str, answers: Mapping[str, Answer], input_tokens: int | None)` with `.noul(id) -> float`, `.choice(id) -> ChoiceA`, `.score(id) -> ScoreA`.
- Produces (config.py): `Thresholds(verb_fire=0.7, scope_fire=0.6, collective=0.65, target_choice_conf=0.7, auto_execute=0.75, confirm_band=0.5, flag=0.6)`; `EngineConfig(model: str, thresholds=Thresholds(), max_rounds=2, latency_budget_ms=600, request_timeout_ms=1500, max_silent_targets=20, scope_cap=60, device_round=False, supports_clarification=True, max_prompt_chars=500)`.
- Produces (resolution.py): `TraceEntry(round: int, question_id: str, answer: Answer)`, `ThresholdDecision(name: str, value: float, threshold: float, passed: bool)`, `Trace` (mutable builder: `.record(round, answers)`, `.decide(name, value, threshold) -> bool`, `.models: list[str]`, `.to_dict() -> JSON`), `Action(verb: Verb, targets: tuple[Entity, ...], params: Mapping[str, float | str])`, `Condition(subject: Entity, expected_state: str)`, `Resolved(actions, condition, confidence, trace)`, `NeedsConfirmation(actions, condition, reason, trace)`, `NeedsClarification(question_key: str, candidates: tuple[Entity, ...], trace)`, `Escalate(reason: str, partial: tuple[Action, ...], trace)`, `Resolution = Resolved | NeedsConfirmation | NeedsClarification | Escalate`.

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_questions.py`:
```python
import dataclasses
import json

import pytest

from hunch.config import EngineConfig, Thresholds
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA, NoulQ, ScoreA, ScoreQ
from hunch.resolution import Trace


def test_question_types_are_frozen():
    q = NoulQ("is it on?")
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.instructions = "x"  # type: ignore[misc]
    assert ChoiceQ("pick", ("a", "b")).options == ("a", "b")
    assert ScoreQ("rate", ("low", "high")).levels == ("low", "high")


def test_answers_typed_accessors():
    a = Answers(
        model="jev-1.13.0",
        answers={
            "n": NoulA(0.9),
            "c": ChoiceA("kitchen", 0.8, {"kitchen": 0.8, "none": 0.2}),
            "s": ScoreA(2.4, 0.7, {1: 0.1, 2: 0.4, 3: 0.5}),
        },
        input_tokens=120,
    )
    assert a.noul("n") == 0.9
    assert a.choice("c").choice == "kitchen"
    assert a.score("s").score == 2.4


def test_answers_accessor_type_mismatch_raises():
    a = Answers("m", {"c": ChoiceA("x", 1.0, {"x": 1.0})}, None)
    with pytest.raises(TypeError):
        a.noul("c")


def test_answers_missing_id_raises_keyerror():
    with pytest.raises(KeyError):
        Answers("m", {}, None).noul("nope")


def test_engine_config_requires_model_and_has_defaults():
    cfg = EngineConfig(model="jev-1.13.0")
    assert cfg.max_rounds == 2
    assert cfg.scope_cap == 60
    assert cfg.thresholds == Thresholds()
    with pytest.raises(TypeError):
        EngineConfig()  # type: ignore[call-arg]


def test_trace_records_answers_decisions_and_models():
    t = Trace()
    t.record(1, Answers("jev-1.13.0", {"verb:turn_off": NoulA(0.93)}, 300))
    assert t.decide("verb:turn_off", 0.93, 0.7) is True
    assert t.decide("flag:collective", 0.4, 0.65) is False
    d = t.to_dict()
    assert d["models"] == ["jev-1.13.0"]
    assert d["entries"][0] == {"round": 1, "question_id": "verb:turn_off", "answer": {"type": "noul", "probability": 0.93}}
    assert d["decisions"][1] == {"name": "flag:collective", "value": 0.4, "threshold": 0.65, "passed": False}
    json.dumps(d)  # must be JSON-serialisable
```

Add to `conftest.py`:
```python
from hunch.config import EngineConfig, Thresholds


@pytest.fixture
def thresholds() -> Thresholds:
    return Thresholds()


@pytest.fixture
def config() -> EngineConfig:
    return EngineConfig(model="jev-1.13.0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_questions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.config'`.

- [ ] **Step 3: Implement `questions.py`**

```python
"""Backend-neutral question and answer types. The client maps these to SDK types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

JSON = dict[str, Any]


@dataclass(frozen=True)
class NoulQ:
    instructions: str


@dataclass(frozen=True)
class ChoiceQ:
    instructions: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class ScoreQ:
    instructions: str
    levels: tuple[str, ...]


Question = NoulQ | ChoiceQ | ScoreQ


@dataclass(frozen=True)
class NoulA:
    probability: float


@dataclass(frozen=True)
class ChoiceA:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class ScoreA:
    score: float
    confidence: float
    probabilities: Mapping[int, float]


Answer = NoulA | ChoiceA | ScoreA


@dataclass(frozen=True)
class Answers:
    model: str
    answers: Mapping[str, Answer]
    input_tokens: int | None

    def noul(self, question_id: str) -> float:
        a = self.answers[question_id]
        if not isinstance(a, NoulA):
            raise TypeError(f"{question_id} is {type(a).__name__}, expected NoulA")
        return a.probability

    def choice(self, question_id: str) -> ChoiceA:
        a = self.answers[question_id]
        if not isinstance(a, ChoiceA):
            raise TypeError(f"{question_id} is {type(a).__name__}, expected ChoiceA")
        return a

    def score(self, question_id: str) -> ScoreA:
        a = self.answers[question_id]
        if not isinstance(a, ScoreA):
            raise TypeError(f"{question_id} is {type(a).__name__}, expected ScoreA")
        return a
```

- [ ] **Step 4: Implement `config.py`**

```python
"""Tunable thresholds and engine settings. Defaults are starting points, tuned from the golden corpus."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Thresholds:
    verb_fire: float = 0.7
    scope_fire: float = 0.6         # floor / area / domain Nouls
    collective: float = 0.65
    target_choice_conf: float = 0.7
    auto_execute: float = 0.75
    confirm_band: float = 0.5       # [confirm_band, auto_execute) -> NeedsConfirmation
    flag: float = 0.6               # has_exception / has_condition / is_query / has_timing / is_destructive


@dataclass(frozen=True)
class EngineConfig:
    model: str                      # pinned Jev model id, e.g. "jev-1.13.0"
    thresholds: Thresholds = field(default_factory=Thresholds)
    max_rounds: int = 2
    latency_budget_ms: int = 600
    request_timeout_ms: int = 1500
    max_silent_targets: int = 20
    scope_cap: int = 60
    device_round: bool = False
    supports_clarification: bool = True
    max_prompt_chars: int = 500
```

- [ ] **Step 5: Implement `resolution.py`**

```python
"""What the engine returns. No free text anywhere; every variant carries the full trace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from hunch.model import Entity
from hunch.questions import JSON, Answer, Answers, ChoiceA, NoulA, ScoreA
from hunch.vocabulary import Verb


@dataclass(frozen=True)
class TraceEntry:
    round: int
    question_id: str
    answer: Answer


@dataclass(frozen=True)
class ThresholdDecision:
    name: str
    value: float
    threshold: float
    passed: bool


@dataclass
class Trace:
    """Mutable builder; the only non-frozen type in the package. Frozen when embedded via to_dict."""

    entries: list[TraceEntry] = field(default_factory=list)
    decisions: list[ThresholdDecision] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def record(self, round: int, answers: Answers) -> None:
        self.models.append(answers.model)
        for qid, ans in answers.answers.items():
            self.entries.append(TraceEntry(round, qid, ans))

    def decide(self, name: str, value: float, threshold: float) -> bool:
        passed = value >= threshold
        self.decisions.append(ThresholdDecision(name, value, threshold, passed))
        return passed

    def note(self, text: str) -> None:
        self.notes.append(text)

    def to_dict(self) -> JSON:
        return {
            "models": list(self.models),
            "entries": [
                {"round": e.round, "question_id": e.question_id, "answer": _answer_dict(e.answer)}
                for e in self.entries
            ],
            "decisions": [asdict(d) for d in self.decisions],
            "notes": list(self.notes),
        }


def _answer_dict(a: Answer) -> JSON:
    if isinstance(a, NoulA):
        return {"type": "noul", "probability": a.probability}
    if isinstance(a, ChoiceA):
        return {"type": "choice", "choice": a.choice, "confidence": a.confidence,
                "probabilities": dict(a.probabilities)}
    if isinstance(a, ScoreA):
        return {"type": "score", "score": a.score, "confidence": a.confidence,
                "probabilities": {str(k): v for k, v in a.probabilities.items()}}
    raise TypeError(type(a))


@dataclass(frozen=True)
class Action:
    verb: Verb
    targets: tuple[Entity, ...]
    params: Mapping[str, float | str]


@dataclass(frozen=True)
class Condition:
    subject: Entity
    expected_state: str


@dataclass(frozen=True)
class Resolved:
    actions: tuple[Action, ...]
    condition: Condition | None
    confidence: float
    trace: Trace


@dataclass(frozen=True)
class NeedsConfirmation:
    actions: tuple[Action, ...]
    condition: Condition | None
    reason: str
    trace: Trace


@dataclass(frozen=True)
class NeedsClarification:
    question_key: str            # "which_area" | "which_device"
    candidates: tuple[Entity, ...]
    trace: Trace


@dataclass(frozen=True)
class Escalate:
    reason: str                  # "timing" | "no_intent" | "destructive" | "low_confidence" | "scope" | "decision_backend_unavailable" | "prompt_invalid"
    partial: tuple[Action, ...]
    trace: Trace


Resolution = Resolved | NeedsConfirmation | NeedsClarification | Escalate
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_questions.py -q`
Expected: `6 passed`.

- [ ] **Step 7: Commit**

```bash
git add packages/hunch/src/hunch/questions.py packages/hunch/src/hunch/config.py packages/hunch/src/hunch/resolution.py packages/hunch/tests/test_questions.py packages/hunch/tests/conftest.py
git commit -m "Add question/answer, config and resolution types with trace

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: DecisionClient protocol, fake and TypeSafe implementation

**Files:**
- Create: `packages/hunch/src/hunch/client.py`, `packages/hunch/tests/test_client.py`

**Interfaces:**
- Consumes: `Question`, `Answer`, `Answers`, `JSON` from Task 4.
- Produces:
  - `class DecisionBackendError(Exception)` — the only exception the engine catches; carries `.reason: str`.
  - `class DecisionClient(Protocol): async def ask(self, state: JSON, questions: Mapping[str, Question]) -> Answers`
  - `FakeDecisionClient(script: Mapping[str, Answer] | Callable[[JSON, Mapping[str, Question]], Mapping[str, Answer]], model="fake")` — records every call in `.calls: list[tuple[JSON, dict[str, Question]]]`; raises `KeyError` if a question id is missing from the script (so tests fail loudly on unexpected questions).
  - `TypeSafeDecisionClient(model: str, *, api_key: str | None = None, timeout_ms: int = 1500, sdk_client=None)` — maps our types to SDK types and back; wraps every SDK error in `DecisionBackendError`; raises `DecisionBackendError("model_mismatch")` if `resp.model != model`.
  - `to_sdk_question(q: Question)` and `from_sdk_answer(a) -> Answer` as module-level pure functions (tested without the network).

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_client.py`:
```python
from types import SimpleNamespace

import pytest
from typesafe_sdk import Choice, Noul, Score, TypeSafeRateLimitError

from hunch.client import (
    DecisionBackendError,
    FakeDecisionClient,
    TypeSafeDecisionClient,
    from_sdk_answer,
    to_sdk_question,
)
from hunch.questions import ChoiceA, ChoiceQ, NoulA, NoulQ, ScoreA, ScoreQ


def test_to_sdk_question_maps_all_three_primitives():
    n = to_sdk_question(NoulQ("is it on?"))
    c = to_sdk_question(ChoiceQ("which?", ("a", "b")))
    s = to_sdk_question(ScoreQ("how bright?", ("dim", "bright")))
    assert isinstance(n, Noul) and n.instructions == "is it on?"
    assert isinstance(c, Choice) and set(c.criteria) == {"a", "b"}
    assert isinstance(s, Score) and list(s.criteria) == ["dim", "bright"]


def test_from_sdk_answer_maps_all_three_primitives():
    assert from_sdk_answer(SimpleNamespace(type="noul", noul=0.9)) == NoulA(0.9)
    assert from_sdk_answer(
        SimpleNamespace(type="choice", choice="a", confidence=0.8, probabilities={"a": 0.8, "b": 0.2})
    ) == ChoiceA("a", 0.8, {"a": 0.8, "b": 0.2})
    assert from_sdk_answer(
        SimpleNamespace(type="score", score=1.5, confidence=0.6, probabilities={1: 0.5, 2: 0.5})
    ) == ScoreA(1.5, 0.6, {1: 0.5, 2: 0.5})


def test_from_sdk_answer_rejects_unknown_type():
    with pytest.raises(DecisionBackendError):
        from_sdk_answer(SimpleNamespace(type="poem"))


async def test_fake_client_returns_script_and_records_calls():
    fake = FakeDecisionClient({"verb:turn_off": NoulA(0.9)})
    out = await fake.ask({"request": "off"}, {"verb:turn_off": NoulQ("x")})
    assert out.noul("verb:turn_off") == 0.9
    assert out.model == "fake"
    assert fake.calls == [({"request": "off"}, {"verb:turn_off": NoulQ("x")})]


async def test_fake_client_fails_loudly_on_unscripted_question():
    fake = FakeDecisionClient({})
    with pytest.raises(KeyError):
        await fake.ask({}, {"verb:turn_off": NoulQ("x")})


async def test_fake_client_accepts_callable_script():
    fake = FakeDecisionClient(lambda state, qs: {qid: NoulA(0.5) for qid in qs})
    out = await fake.ask({}, {"a": NoulQ("a"), "b": NoulQ("b")})
    assert out.noul("a") == 0.5 and out.noul("b") == 0.5


class _StubSDK:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def system_one(self, *, state, questions, model, timeout=None):
        self.calls.append((state, questions, model))
        if self.error:
            raise self.error
        return self.response


def _resp(model="jev-1.13.0"):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(input_tokens=42),
        answers={"q": SimpleNamespace(type="noul", noul=0.77)},
    )


async def test_typesafe_client_round_trips_and_pins_model():
    sdk = _StubSDK(response=_resp())
    client = TypeSafeDecisionClient(model="jev-1.13.0", sdk_client=sdk)
    out = await client.ask({"request": "hi"}, {"q": NoulQ("x")})
    assert out.noul("q") == 0.77 and out.model == "jev-1.13.0" and out.input_tokens == 42
    assert sdk.calls[0][2] == "jev-1.13.0"
    assert isinstance(sdk.calls[0][1]["q"], Noul)


async def test_typesafe_client_rejects_model_mismatch():
    client = TypeSafeDecisionClient(model="jev-1.13.0", sdk_client=_StubSDK(response=_resp("jev-1.14.0")))
    with pytest.raises(DecisionBackendError) as ei:
        await client.ask({}, {"q": NoulQ("x")})
    assert ei.value.reason == "model_mismatch"


async def test_typesafe_client_wraps_sdk_errors():
    err = TypeSafeRateLimitError.__new__(TypeSafeRateLimitError)
    client = TypeSafeDecisionClient(model="jev-1.13.0", sdk_client=_StubSDK(error=err))
    with pytest.raises(DecisionBackendError) as ei:
        await client.ask({}, {"q": NoulQ("x")})
    assert ei.value.reason == "decision_backend_unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_client.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.client'`.

- [ ] **Step 3: Implement `client.py`**

```python
"""Backend boundary. Everything network-shaped lives here and nowhere else."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    Noul,
    RetryPolicy,
    Score,
    TypeSafeError,
)

from hunch.questions import JSON, Answer, Answers, ChoiceA, ChoiceQ, NoulA, NoulQ, Question, ScoreA, ScoreQ


class DecisionBackendError(Exception):
    def __init__(self, reason: str, cause: BaseException | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.__cause__ = cause


class DecisionClient(Protocol):
    async def ask(self, state: JSON, questions: Mapping[str, Question]) -> Answers: ...


ScriptFn = Callable[[JSON, Mapping[str, Question]], Mapping[str, Answer]]


class FakeDecisionClient:
    """Scripted answers for tests. Missing ids raise KeyError so unexpected questions fail loudly."""

    def __init__(self, script: Mapping[str, Answer] | ScriptFn, model: str = "fake") -> None:
        self._script = script
        self._model = model
        self.calls: list[tuple[JSON, dict[str, Question]]] = []

    async def ask(self, state: JSON, questions: Mapping[str, Question]) -> Answers:
        self.calls.append((state, dict(questions)))
        if callable(self._script):
            answers = dict(self._script(state, questions))
        else:
            answers = {qid: self._script[qid] for qid in questions}
        return Answers(model=self._model, answers=answers, input_tokens=None)


def to_sdk_question(q: Question) -> Noul | Choice | Score:
    if isinstance(q, NoulQ):
        return Noul(instructions=q.instructions)
    if isinstance(q, ChoiceQ):
        return Choice(instructions=q.instructions, criteria={o: None for o in q.options})
    if isinstance(q, ScoreQ):
        return Score(instructions=q.instructions, criteria=list(q.levels))
    raise TypeError(type(q))


def from_sdk_answer(a: Any) -> Answer:
    kind = getattr(a, "type", None)
    if kind == "noul":
        return NoulA(float(a.noul))
    if kind == "choice":
        return ChoiceA(str(a.choice), float(a.confidence), dict(a.probabilities))
    if kind == "score":
        return ScoreA(float(a.score), float(a.confidence), {int(k): float(v) for k, v in a.probabilities.items()})
    raise DecisionBackendError("malformed_response")


class TypeSafeDecisionClient:
    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        timeout_ms: int = 1500,
        sdk_client: Any | None = None,
    ) -> None:
        self._model = model
        timeout_s = timeout_ms / 1000
        self._sdk = sdk_client or AsyncTypeSafeClient(
            api_key=api_key,
            model=model,
            timeout=timeout_s,
            retry=RetryPolicy(max_retries=2, timeout=timeout_s),
        )

    async def ask(self, state: JSON, questions: Mapping[str, Question]) -> Answers:
        sdk_questions = {qid: to_sdk_question(q) for qid, q in questions.items()}
        try:
            resp = await self._sdk.system_one(state=state, questions=sdk_questions, model=self._model)
        except TypeSafeError as exc:
            raise DecisionBackendError("decision_backend_unavailable", exc) from exc
        if resp.model != self._model:
            raise DecisionBackendError("model_mismatch")
        answers = {qid: from_sdk_answer(a) for qid, a in resp.answers.items()}
        usage = getattr(resp, "usage", None)
        return Answers(model=resp.model, answers=answers, input_tokens=getattr(usage, "input_tokens", None))

    async def aclose(self) -> None:
        aclose = getattr(self._sdk, "aclose", None)
        if aclose:
            await aclose()
```

If `TypeSafeRateLimitError.__new__` in the test does not produce a usable instance (SDK constructor requires args), replace that line with `err = TypeSafeError("rate limited")` — the wrap path is identical.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_client.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/client.py packages/hunch/tests/test_client.py
git commit -m "Add DecisionClient protocol with fake and TypeSafe implementations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Round 1 — state, questions and shape interpretation

Reference: TypeSafe's [smart-home demo](https://docs.typesafe.ai/demos/smart-home) uses the same speculative fan-out: ask every shape question upfront in one call, most irrelevant to any given request.

**Files:**
- Create: `packages/hunch/src/hunch/round1.py`, `packages/hunch/tests/test_round1.py`

**Interfaces:**
- Consumes: `HomeModel`, `Vocabulary`, `Thresholds`, `Answers`, `NoulQ`, `ChoiceQ`, `Trace`.
- Produces:
  - `build_round1_state(home: HomeModel, prompt: str) -> JSON`
  - `build_round1_questions(home: HomeModel, vocab: Vocabulary) -> dict[str, Question]`
  - `FLAGS = ("collective", "has_exception", "has_condition", "is_query", "has_timing", "is_destructive")`
  - `Shape(fired_verbs: tuple[Verb, ...], scope_areas: tuple[str, ...], scope_domains: tuple[str, ...], area_probs: Mapping[str, float], domain_probs: Mapping[str, float], flags: Mapping[str, float], scene: Entity | None, condition_domain: str | None)` with helper `.flag(name) -> float`
  - `interpret_round1(home, vocab, answers: Answers, thresholds: Thresholds, trace: Trace) -> Shape`

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_round1.py`:
```python
from hunch.questions import Answers, ChoiceA, ChoiceQ, NoulA, NoulQ
from hunch.resolution import Trace
from hunch.round1 import FLAGS, build_round1_questions, build_round1_state, interpret_round1


def test_state_is_small_and_names_only(home):
    state = build_round1_state(home, "turn off the downstairs lights")
    assert state["request"] == "turn off the downstairs lights"
    assert state["floors"] == ["Downstairs", "Upstairs"]
    assert state["areas"] == ["Kitchen", "Living room (lounge)", "Hallway", "Bedroom", "Office (study)"]
    assert state["domains"] == ["climate", "cover", "light", "lock", "switch"]
    assert state["scenes"] == ["Movie night", "Goodnight"]
    assert "entities" not in state  # never show Round 1 the entity list


def test_questions_cover_verbs_floors_areas_domains_flags_scene_condition(home, vocab):
    qs = build_round1_questions(home, vocab)
    assert {f"verb:{n}" for n in vocab.names} <= set(qs)
    assert {"floor:downstairs", "floor:upstairs"} <= set(qs)
    assert {"area:kitchen", "area:office"} <= set(qs)
    assert {"domain:light", "domain:lock"} <= set(qs)
    assert {f"flag:{f}" for f in FLAGS} <= set(qs)
    assert isinstance(qs["scene"], ChoiceQ) and qs["scene"].options == ("Movie night", "Goodnight", "none")
    assert isinstance(qs["condition_domain"], ChoiceQ) and qs["condition_domain"].options[-1] == "none"
    assert all(isinstance(q, NoulQ) for k, q in qs.items() if k.startswith(("verb:", "floor:", "area:", "domain:", "flag:")))


def test_area_question_mentions_aliases(home, vocab):
    qs = build_round1_questions(home, vocab)
    assert "lounge" in qs["area:living"].instructions


def test_no_scene_question_when_home_has_no_scenes(home, vocab):
    bare = type(home)(home.floors, home.areas, home.entities, ())
    assert "scene" not in build_round1_questions(bare, vocab)


def _answers(home, vocab, **overrides):
    qs = build_round1_questions(home, vocab)
    base = {}
    for qid, q in qs.items():
        if isinstance(q, ChoiceQ):
            base[qid] = ChoiceA("none", 0.9, {o: (0.9 if o == "none" else 0.0) for o in q.options})
        else:
            base[qid] = NoulA(0.05)
    base.update(overrides)
    return Answers("jev-1.13.0", base, 400)


def test_interpret_floor_expands_to_areas_and_fires_verb(home, vocab, thresholds):
    ans = _answers(home, vocab, **{
        "verb:turn_off": NoulA(0.95),
        "floor:downstairs": NoulA(0.9),
        "domain:light": NoulA(0.9),
        "flag:collective": NoulA(0.9),
    })
    shape = interpret_round1(home, vocab, ans, thresholds, Trace())
    assert [v.name for v in shape.fired_verbs] == ["turn_off"]
    assert set(shape.scope_areas) == {"kitchen", "living", "hallway"}
    assert shape.scope_domains == ("light",)
    assert shape.flag("collective") == 0.9
    assert shape.scene is None and shape.condition_domain is None


def test_interpret_keeps_probabilities_for_ranked_widening(home, vocab, thresholds):
    ans = _answers(home, vocab, **{"verb:turn_on": NoulA(0.9), "area:living": NoulA(0.45)})
    shape = interpret_round1(home, vocab, ans, thresholds, Trace())
    assert shape.scope_areas == ()             # 0.45 < scope_fire
    assert shape.area_probs["living"] == 0.45  # but retained


def test_interpret_scene_and_condition_domain(home, vocab, thresholds):
    ans = _answers(home, vocab, **{
        "verb:activate": NoulA(0.9),
        "scene": ChoiceA("Movie night", 0.85, {"Movie night": 0.85, "Goodnight": 0.1, "none": 0.05}),
        "flag:has_condition": NoulA(0.8),
        "condition_domain": ChoiceA("lock", 0.8, {"lock": 0.8, "none": 0.2}),
    })
    shape = interpret_round1(home, vocab, ans, thresholds, Trace())
    assert shape.scene is not None and shape.scene.entity_id == "scene.movie_night"
    assert shape.condition_domain == "lock"


def test_interpret_ignores_scene_below_confidence(home, vocab, thresholds):
    ans = _answers(home, vocab, **{
        "scene": ChoiceA("Movie night", 0.4, {"Movie night": 0.4, "Goodnight": 0.35, "none": 0.25}),
    })
    assert interpret_round1(home, vocab, ans, thresholds, Trace()).scene is None


def test_interpret_records_trace(home, vocab, thresholds):
    trace = Trace()
    interpret_round1(home, vocab, _answers(home, vocab, **{"verb:turn_off": NoulA(0.9)}), thresholds, trace)
    assert trace.models == ["jev-1.13.0"]
    assert any(d.name == "verb:turn_off" and d.passed for d in trace.decisions)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_round1.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.round1'`.

- [ ] **Step 3: Implement `round1.py`**

```python
"""Round 1: judge the *shape* of the request over a tiny state. Never shows Jev an entity list."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hunch.config import Thresholds
from hunch.model import Area, Entity, HomeModel
from hunch.questions import JSON, Answers, ChoiceQ, NoulQ, Question
from hunch.resolution import Trace
from hunch.vocabulary import Verb, Vocabulary

FLAGS = ("collective", "has_exception", "has_condition", "is_query", "has_timing", "is_destructive")

_FLAG_INSTRUCTIONS = {
    "collective": "Does the request target all matching devices in its scope (plural or 'all'), rather than one specific device?",
    "has_exception": "Does the request exclude something, e.g. 'except', 'but not', 'apart from', 'other than'?",
    "has_condition": "Does the request make the action depend on a condition, e.g. 'if', 'when', 'unless', 'only if'?",
    "is_query": "Is the request asking about the current state of something, rather than asking to change it?",
    "has_timing": "Does the request involve a delay, schedule, duration or sequence, e.g. 'in ten minutes', 'after', 'later', 'then'?",
    "is_destructive": "Would fulfilling the request cause irreversible, unsafe or security-relevant effects (unlocking, disarming, opening to the outside)?",
}


def _label(area: Area) -> str:
    return f"{area.name} ({', '.join(area.aliases)})" if area.aliases else area.name


def build_round1_state(home: HomeModel, prompt: str) -> JSON:
    return {
        "request": prompt,
        "floors": [f.name for f in home.floors],
        "areas": [_label(a) for a in home.areas],
        "domains": list(home.domains),
        "scenes": [s.name for s in home.scenes],
    }


def build_round1_questions(home: HomeModel, vocab: Vocabulary) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    for v in vocab.verbs:
        qs[f"verb:{v.name}"] = NoulQ(f"Does the request ask to {v.phrasing}?")
    for f in home.floors:
        qs[f"floor:{f.floor_id}"] = NoulQ(f"Does the request refer to the floor '{f.name}' or to all of it?")
    for a in home.areas:
        qs[f"area:{a.area_id}"] = NoulQ(f"Does the request refer to the area '{_label(a)}'?")
    for d in home.domains:
        qs[f"domain:{d}"] = NoulQ(f"Does the request involve devices of type '{d}'?")
    for flag in FLAGS:
        qs[f"flag:{flag}"] = NoulQ(_FLAG_INSTRUCTIONS[flag])
    if home.scenes:
        qs["scene"] = ChoiceQ(
            "Which scene or script does the request name, if any?",
            tuple(s.name for s in home.scenes) + ("none",),
        )
    qs["condition_domain"] = ChoiceQ(
        "If the request contains a condition, which device type is the condition about?",
        tuple(home.domains) + ("none",),
    )
    return qs


@dataclass(frozen=True)
class Shape:
    fired_verbs: tuple[Verb, ...]
    scope_areas: tuple[str, ...]
    scope_domains: tuple[str, ...]
    area_probs: Mapping[str, float]
    domain_probs: Mapping[str, float]
    flags: Mapping[str, float]
    scene: Entity | None
    condition_domain: str | None

    def flag(self, name: str) -> float:
        return self.flags[name]


def interpret_round1(
    home: HomeModel, vocab: Vocabulary, answers: Answers, thresholds: Thresholds, trace: Trace
) -> Shape:
    trace.record(1, answers)

    fired_verbs = tuple(
        v for v in vocab.verbs
        if trace.decide(f"verb:{v.name}", answers.noul(f"verb:{v.name}"), thresholds.verb_fire)
    )

    area_probs: dict[str, float] = {a.area_id: answers.noul(f"area:{a.area_id}") for a in home.areas}
    scope_areas: list[str] = []
    for f in home.floors:
        if trace.decide(f"floor:{f.floor_id}", answers.noul(f"floor:{f.floor_id}"), thresholds.scope_fire):
            scope_areas.extend(f.area_ids)
    for a in home.areas:
        if trace.decide(f"area:{a.area_id}", area_probs[a.area_id], thresholds.scope_fire):
            if a.area_id not in scope_areas:
                scope_areas.append(a.area_id)

    domain_probs = {d: answers.noul(f"domain:{d}") for d in home.domains}
    scope_domains = tuple(
        d for d in home.domains if trace.decide(f"domain:{d}", domain_probs[d], thresholds.scope_fire)
    )

    flags = {flag: answers.noul(f"flag:{flag}") for flag in FLAGS}

    scene: Entity | None = None
    if "scene" in answers.answers:
        c = answers.choice("scene")
        if c.choice != "none" and trace.decide("scene", c.confidence, thresholds.target_choice_conf):
            scene = next((s for s in home.scenes if s.name == c.choice), None)

    condition_domain: str | None = None
    if trace.decide("flag:has_condition", flags["has_condition"], thresholds.flag):
        c = answers.choice("condition_domain")
        if c.choice != "none" and trace.decide("condition_domain", c.confidence, thresholds.target_choice_conf):
            condition_domain = c.choice

    return Shape(
        fired_verbs=fired_verbs,
        scope_areas=tuple(scope_areas),
        scope_domains=scope_domains,
        area_probs=area_probs,
        domain_probs=domain_probs,
        flags=flags,
        scene=scene,
        condition_domain=condition_domain,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_round1.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/round1.py packages/hunch/tests/test_round1.py docs/superpowers/specs/2026-09-19-hunch-design.md
git commit -m "Add Round 1 state, questions and shape interpretation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Scope policy chain

**Files:**
- Create: `packages/hunch/src/hunch/scope.py`, `packages/hunch/tests/test_scope.py`

**Interfaces:**
- Consumes: `HomeModel`, `Entity`, `Verb`, `Shape`, `EngineConfig`, `Trace`.
- Produces:
  - `Candidates(entities: tuple[Entity, ...], widened: bool)`
  - `DeviceRound(entities: tuple[Entity, ...])` — engine must run a `device_round` Choice over these; label via `device_label(e)`
  - `Clarify(question_key: str, candidates: tuple[Entity, ...])`
  - `ScopeEscalate(reason: str)`
  - `ScopeResult = Candidates | DeviceRound | Clarify | ScopeEscalate`
  - `strict_candidates(home, verb, shape) -> tuple[Entity, ...]`
  - `ranked_widen(home, verb, shape, trace) -> tuple[Entity, ...]`
  - `scope_candidates(home, verb, shape, config, trace) -> ScopeResult`
  - `device_label(e: Entity) -> str` — `device_name` if set else `name`

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_scope.py`:
```python
import dataclasses

import pytest

from hunch.config import EngineConfig
from hunch.resolution import Trace
from hunch.round1 import Shape
from hunch.scope import (
    Candidates,
    Clarify,
    DeviceRound,
    ScopeEscalate,
    device_label,
    ranked_widen,
    scope_candidates,
    strict_candidates,
)


def _shape(home, verbs=("turn_off",), areas=(), domains=(), area_probs=None, domain_probs=None, vocab=None):
    from hunch.vocabulary import DEFAULT_VOCABULARY
    vocab = vocab or DEFAULT_VOCABULARY
    ap = {a.area_id: 0.05 for a in home.areas}
    ap.update(area_probs or {})
    dp = {d: 0.05 for d in home.domains}
    dp.update(domain_probs or {})
    return Shape(
        fired_verbs=tuple(vocab.by_name(v) for v in verbs),
        scope_areas=tuple(areas),
        scope_domains=tuple(domains),
        area_probs=ap,
        domain_probs=dp,
        flags={f: 0.05 for f in ("collective", "has_exception", "has_condition", "is_query", "has_timing", "is_destructive")},
        scene=None,
        condition_domain=None,
    )


def test_strict_intersects_verb_area_domain(home, vocab):
    shape = _shape(home, areas=("kitchen", "living", "hallway"), domains=("light",))
    ids = {e.entity_id for e in strict_candidates(home, vocab.by_name("turn_off"), shape)}
    assert ids == {"light.kitchen_ceiling", "light.kitchen_counter", "light.living_main",
                   "light.reading_lamp", "light.hallway"}


def test_strict_respects_verb_applicability(home, vocab):
    shape = _shape(home, verbs=("open",), areas=("living",), domains=())
    ids = {e.entity_id for e in strict_candidates(home, vocab.by_name("open"), shape)}
    assert ids == {"cover.living_blinds"}


def test_strict_empty_domain_means_all_domains_for_verb(home, vocab):
    shape = _shape(home, areas=("kitchen",))
    ids = {e.entity_id for e in strict_candidates(home, vocab.by_name("turn_off"), shape)}
    assert ids == {"light.kitchen_ceiling", "light.kitchen_counter", "switch.fridge"}


def test_ranked_widen_adds_best_area_first(home, vocab):
    shape = _shape(home, areas=(), domains=("light",), area_probs={"living": 0.45, "office": 0.2})
    ids = {e.entity_id for e in ranked_widen(home, vocab.by_name("turn_on"), shape, Trace())}
    assert ids == {"light.living_main", "light.reading_lamp"}


def test_ranked_widen_falls_back_to_domain_then_everything(home, vocab):
    # No area signal at all, domain light at 0.3: widen to all lights (incl. the arealess christmas tree)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    ids = {e.entity_id for e in ranked_widen(home, vocab.by_name("turn_on"), shape, Trace())}
    assert "light.christmas_tree" in ids and "switch.fridge" not in ids


def test_scope_returns_strict_when_non_empty(home, vocab, config):
    shape = _shape(home, areas=("hallway",), domains=("light",))
    r = scope_candidates(home, vocab.by_name("turn_off"), shape, config, Trace())
    assert isinstance(r, Candidates) and not r.widened
    assert [e.entity_id for e in r.entities] == ["light.hallway"]


def test_scope_widens_when_strict_empty(home, vocab, config):
    shape = _shape(home, areas=("garage",), domains=("light",), area_probs={"living": 0.4})
    r = scope_candidates(home, vocab.by_name("turn_off"), shape, config, Trace())
    assert isinstance(r, Candidates) and r.widened
    assert {e.entity_id for e in r.entities} == {"light.living_main", "light.reading_lamp"}


def test_scope_cap_then_device_round_when_enabled(home, vocab):
    cfg = EngineConfig(model="m", scope_cap=2, device_round=True)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    r = scope_candidates(home, vocab.by_name("turn_on"), shape, cfg, Trace())
    assert isinstance(r, DeviceRound)
    assert len(r.entities) > 2


def test_scope_cap_then_clarify_when_device_round_disabled(home, vocab):
    cfg = EngineConfig(model="m", scope_cap=2, device_round=False, supports_clarification=True)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    r = scope_candidates(home, vocab.by_name("turn_on"), shape, cfg, Trace())
    assert isinstance(r, Clarify) and r.question_key == "which_area"


def test_scope_cap_then_escalate_when_nothing_else_allowed(home, vocab):
    cfg = EngineConfig(model="m", scope_cap=2, device_round=False, supports_clarification=False)
    shape = _shape(home, areas=(), domains=(), domain_probs={"light": 0.3})
    r = scope_candidates(home, vocab.by_name("turn_on"), shape, cfg, Trace())
    assert isinstance(r, ScopeEscalate) and r.reason == "scope"


def test_scope_escalates_when_verb_applies_to_nothing(home, vocab, config):
    shape = _shape(home, verbs=("arm",))
    r = scope_candidates(home, vocab.by_name("arm"), shape, config, Trace())
    assert isinstance(r, ScopeEscalate) and r.reason == "scope"


def test_device_label_prefers_device_name(home):
    assert device_label(home.entity_by_id("light.bedroom_left")) == "Bedside lamps"
    assert device_label(home.entity_by_id("light.christmas_tree")) == "Christmas tree"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_scope.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.scope'`.

- [ ] **Step 3: Implement `scope.py`**

```python
"""Between rounds: turn the request shape into a candidate set. Widens, never narrows, never hard-fails."""

from __future__ import annotations

from dataclasses import dataclass

from hunch.config import EngineConfig
from hunch.model import Entity, HomeModel
from hunch.resolution import Trace
from hunch.round1 import Shape
from hunch.vocabulary import Verb


@dataclass(frozen=True)
class Candidates:
    entities: tuple[Entity, ...]
    widened: bool


@dataclass(frozen=True)
class DeviceRound:
    entities: tuple[Entity, ...]


@dataclass(frozen=True)
class Clarify:
    question_key: str
    candidates: tuple[Entity, ...]


@dataclass(frozen=True)
class ScopeEscalate:
    reason: str


ScopeResult = Candidates | DeviceRound | Clarify | ScopeEscalate

_WIDEN_FLOOR = 0.1  # probabilities at or below this are noise; never widen on them


def device_label(e: Entity) -> str:
    return e.device_name or e.name


def _applicable(home: HomeModel, verb: Verb) -> tuple[Entity, ...]:
    return tuple(e for e in home.entities if verb.name in e.verbs)


def _filter(entities: tuple[Entity, ...], areas: tuple[str, ...], domains: tuple[str, ...]) -> tuple[Entity, ...]:
    return tuple(
        e for e in entities
        if (not areas or e.area_id in areas) and (not domains or e.domain in domains)
    )


def strict_candidates(home: HomeModel, verb: Verb, shape: Shape) -> tuple[Entity, ...]:
    """Verb ∧ area-scope ∧ domain-scope. With *no* scope signal at all there is nothing strict about it:
    return () so the widening chain (and its cap) decides."""
    if not shape.scope_areas and not shape.scope_domains:
        return ()
    return _filter(_applicable(home, verb), shape.scope_areas, shape.scope_domains)


def ranked_widen(home: HomeModel, verb: Verb, shape: Shape, trace: Trace) -> tuple[Entity, ...]:
    """Add areas in descending Round 1 probability until non-empty; then domains; then everything applicable."""
    applicable = _applicable(home, verb)
    if not applicable:
        return ()

    areas = list(shape.scope_areas)
    for area_id, p in sorted(shape.area_probs.items(), key=lambda kv: -kv[1]):
        if area_id in areas or p <= _WIDEN_FLOOR:
            continue
        areas.append(area_id)
        found = _filter(applicable, tuple(areas), shape.scope_domains)
        if found:
            trace.note(f"widen:{verb.name}:area:{area_id}")
            return found

    domains = list(shape.scope_domains)
    for domain, p in sorted(shape.domain_probs.items(), key=lambda kv: -kv[1]):
        if domain in domains or p <= _WIDEN_FLOOR:
            continue
        domains.append(domain)
        found = _filter(applicable, (), tuple(domains))
        if found:
            trace.note(f"widen:{verb.name}:domain:{domain}")
            return found

    trace.note(f"widen:{verb.name}:all")
    return applicable


def scope_candidates(
    home: HomeModel, verb: Verb, shape: Shape, config: EngineConfig, trace: Trace
) -> ScopeResult:
    strict = strict_candidates(home, verb, shape)
    if strict:
        return Candidates(strict, widened=False)

    widened = ranked_widen(home, verb, shape, trace)
    if not widened:
        return ScopeEscalate("scope")
    if len(widened) <= config.scope_cap:
        return Candidates(widened, widened=True)

    trace.note(f"scope_cap:{verb.name}:{len(widened)}>{config.scope_cap}")
    if config.device_round:
        return DeviceRound(widened)
    if config.supports_clarification:
        return Clarify("which_area", widened)
    return ScopeEscalate("scope")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_scope.py -q`
Expected: `12 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/scope.py packages/hunch/tests/test_scope.py
git commit -m "Add scope policy chain: strict, ranked widening, cap, device round, clarify

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Round 2 — target and parameter questions

Design note: the spec's "Choice over devices, then over that device's entities" is collapsed into **one** Choice so the common case stays at two round-trips. Options are built by `target_options`: a device with exactly one applicable entity is one option labelled by device; a device with several applicable entities contributes one option per entity labelled `"<device> — <entity name>"`. Arealess/deviceless entities are their own option.

**Files:**
- Create: `packages/hunch/src/hunch/round2.py`, `packages/hunch/tests/test_round2.py`

**Interfaces:**
- Consumes: `Shape`, `Entity`, `Verb`, `ScoreSpec`, `ChoiceSpec`, `Thresholds`, `NoulQ`, `ChoiceQ`, `ScoreQ`.
- Produces:
  - `TargetOption(label: str, entities: tuple[Entity, ...])`
  - `target_options(candidates: tuple[Entity, ...]) -> tuple[TargetOption, ...]` — deterministic order, unique labels (suffix `#2`, `#3` on collision)
  - `Round2Plan(exclude: dict[str, tuple[Entity, ...]], singular: dict[str, tuple[TargetOption, ...]], collective: dict[str, tuple[Entity, ...]], params: tuple[str, ...], condition_candidates: tuple[Entity, ...])` — keyed by verb name
  - `plan_round2(home, shape, per_verb: Mapping[str, tuple[Entity, ...]], thresholds) -> Round2Plan`
  - `build_round2_state(prompt: str, plan: Round2Plan) -> JSON`
  - `build_round2_questions(shape, plan: Round2Plan, home) -> dict[str, Question]` — empty dict means "skip Round 2"

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_round2.py`:
```python
from hunch.questions import ChoiceQ, NoulQ, ScoreQ
from hunch.round1 import Shape
from hunch.round2 import build_round2_questions, build_round2_state, plan_round2, target_options
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def _shape(home, verbs, flags=None, condition_domain=None):
    f = {k: 0.05 for k in ("collective", "has_exception", "has_condition", "is_query", "has_timing", "is_destructive")}
    f.update(flags or {})
    return Shape(tuple(V.by_name(v) for v in verbs), (), (), {}, {}, f, None, condition_domain)


def _ents(home, *ids):
    return tuple(home.entity_by_id(i) for i in ids)


def test_target_options_collapse_single_entity_devices_and_expand_multi(home):
    opts = target_options(_ents(home, "light.bedroom_left", "light.bedroom_right", "light.office_desk", "light.christmas_tree"))
    labels = [o.label for o in opts]
    assert labels == ["Bedside lamps — Bedside left", "Bedside lamps — Bedside right", "Desk lamp", "Christmas tree"]
    assert all(len(o.entities) == 1 for o in opts)


def test_target_options_dedupes_labels(home):
    a = home.entity_by_id("light.office_desk")
    b = type(a)(**{**a.__dict__, "entity_id": "light.office_desk_2", "device_id": "dev_other"})
    labels = [o.label for o in target_options((a, b))]
    assert labels == ["Desk lamp", "Desk lamp #2"]


def test_collective_without_exception_skips_round2(home, thresholds):
    shape = _shape(home, ["turn_off"], {"collective": 0.9})
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter")
    plan = plan_round2(home, shape, {"turn_off": cands}, thresholds)
    assert plan.collective == {"turn_off": cands}
    assert build_round2_questions(shape, plan, home) == {}


def test_collective_with_exception_asks_exclude_per_candidate(home, thresholds):
    shape = _shape(home, ["turn_off"], {"collective": 0.9, "has_exception": 0.8})
    cands = _ents(home, "light.kitchen_ceiling", "switch.fridge")
    plan = plan_round2(home, shape, {"turn_off": cands}, thresholds)
    qs = build_round2_questions(shape, plan, home)
    assert set(qs) == {"exclude:turn_off:light.kitchen_ceiling", "exclude:turn_off:switch.fridge"}
    assert all(isinstance(q, NoulQ) for q in qs.values())
    assert "Fridge" in qs["exclude:turn_off:switch.fridge"].instructions


def test_singular_asks_one_choice_over_target_options(home, thresholds):
    shape = _shape(home, ["turn_on"], {"collective": 0.1})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, thresholds)
    qs = build_round2_questions(shape, plan, home)
    assert isinstance(qs["target:turn_on"], ChoiceQ)
    assert qs["target:turn_on"].options == ("Living room main", "Reading lamp")


def test_param_question_uses_verb_spec(home, thresholds):
    shape = _shape(home, ["set_brightness"], {"collective": 0.9})
    plan = plan_round2(home, shape, {"set_brightness": _ents(home, "light.office_desk")}, thresholds)
    qs = build_round2_questions(shape, plan, home)
    assert isinstance(qs["param:set_brightness"], ScoreQ)
    assert qs["param:set_brightness"].levels == V.by_name("set_brightness").param.levels


def test_condition_questions_when_condition_domain_set(home, thresholds):
    shape = _shape(home, ["arm"], {"collective": 0.9, "has_condition": 0.8}, condition_domain="lock")
    plan = plan_round2(home, shape, {"arm": ()}, thresholds)
    qs = build_round2_questions(shape, plan, home)
    assert qs["cond_subject"].options == ("Front door",)
    assert set(qs["cond_state"].options) >= {"locked", "unlocked"}


def test_round2_state_lists_only_relevant_candidates(home, thresholds):
    shape = _shape(home, ["turn_on"], {"collective": 0.1})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, thresholds)
    state = build_round2_state("turn on the lamp", plan)
    assert state["request"] == "turn on the lamp"
    names = {c["name"] for c in state["candidates"]}
    assert names == {"Living room main", "Reading lamp"}
    assert {"aliases", "area", "device"} <= set(state["candidates"][0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_round2.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.round2'`.

- [ ] **Step 3: Implement `round2.py`**

```python
"""Round 2: resolve targets and parameters, only for verbs Round 1 couldn't finish."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from hunch.config import Thresholds
from hunch.model import Entity, HomeModel
from hunch.questions import JSON, ChoiceQ, NoulQ, Question, ScoreQ
from hunch.round1 import Shape
from hunch.scope import device_label
from hunch.vocabulary import ChoiceSpec, ScoreSpec

# Known states per domain for condition questions. Extend as domains are added.
DOMAIN_STATES: dict[str, tuple[str, ...]] = {
    "lock": ("locked", "unlocked", "jammed"),
    "cover": ("open", "closed", "opening", "closing"),
    "light": ("on", "off"),
    "switch": ("on", "off"),
    "fan": ("on", "off"),
    "media_player": ("playing", "paused", "idle", "off"),
    "climate": ("heat", "cool", "auto", "off"),
    "alarm_control_panel": ("armed_home", "armed_away", "armed_night", "disarmed", "triggered"),
    "binary_sensor": ("on", "off"),
    "person": ("home", "not_home"),
}


@dataclass(frozen=True)
class TargetOption:
    label: str
    entities: tuple[Entity, ...]


def target_options(candidates: tuple[Entity, ...]) -> tuple[TargetOption, ...]:
    by_device: dict[str, list[Entity]] = {}
    for e in candidates:
        by_device.setdefault(e.device_id or f"entity:{e.entity_id}", []).append(e)
    raw: list[TargetOption] = []
    for group in by_device.values():
        if len(group) == 1:
            raw.append(TargetOption(device_label(group[0]), (group[0],)))
        else:
            raw.extend(TargetOption(f"{device_label(e)} — {e.name}", (e,)) for e in group)
    seen: dict[str, int] = {}
    out: list[TargetOption] = []
    for opt in raw:
        n = seen.get(opt.label, 0) + 1
        seen[opt.label] = n
        out.append(opt if n == 1 else TargetOption(f"{opt.label} #{n}", opt.entities))
    return tuple(out)


@dataclass(frozen=True)
class Round2Plan:
    exclude: dict[str, tuple[Entity, ...]] = field(default_factory=dict)
    singular: dict[str, tuple[TargetOption, ...]] = field(default_factory=dict)
    collective: dict[str, tuple[Entity, ...]] = field(default_factory=dict)
    params: tuple[str, ...] = ()
    condition_candidates: tuple[Entity, ...] = ()

    def all_candidates(self) -> tuple[Entity, ...]:
        seen: dict[str, Entity] = {}
        for ents in (*self.exclude.values(), *self.collective.values(), self.condition_candidates):
            for e in ents:
                seen.setdefault(e.entity_id, e)
        for opts in self.singular.values():
            for o in opts:
                for e in o.entities:
                    seen.setdefault(e.entity_id, e)
        return tuple(seen.values())


def plan_round2(
    home: HomeModel, shape: Shape, per_verb: Mapping[str, tuple[Entity, ...]], thresholds: Thresholds
) -> Round2Plan:
    collective = shape.flag("collective") >= thresholds.collective
    has_exception = shape.flag("has_exception") >= thresholds.flag
    exclude: dict[str, tuple[Entity, ...]] = {}
    singular: dict[str, tuple[TargetOption, ...]] = {}
    coll: dict[str, tuple[Entity, ...]] = {}
    params: list[str] = []
    for verb in shape.fired_verbs:
        cands = per_verb.get(verb.name, ())
        if verb.param is not None:
            params.append(verb.name)
        if not cands:
            continue
        if collective and not has_exception:
            coll[verb.name] = cands
        elif collective:
            exclude[verb.name] = cands
        else:
            opts = target_options(cands)
            if len(opts) == 1:
                coll[verb.name] = cands
            else:
                singular[verb.name] = opts
    cond: tuple[Entity, ...] = ()
    if shape.condition_domain and shape.flag("has_condition") >= thresholds.flag:
        cond = tuple(e for e in home.entities if e.domain == shape.condition_domain)
    return Round2Plan(exclude, singular, coll, tuple(params), cond)


def build_round2_state(prompt: str, plan: Round2Plan) -> JSON:
    return {
        "request": prompt,
        "candidates": [
            {
                "name": e.name,
                "aliases": list(e.aliases),
                "area": e.area_id,
                "device": e.device_name,
                "type": e.domain,
                "state": e.state,
            }
            for e in plan.all_candidates()
        ],
    }


def build_round2_questions(shape: Shape, plan: Round2Plan, home: HomeModel) -> dict[str, Question]:
    qs: dict[str, Question] = {}
    for verb_name, ents in plan.exclude.items():
        for e in ents:
            qs[f"exclude:{verb_name}:{e.entity_id}"] = NoulQ(
                f"Should the device '{e.name}' ({device_label(e)}) be excluded from this request?"
            )
    for verb_name, opts in plan.singular.items():
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        qs[f"target:{verb_name}"] = ChoiceQ(
            f"Which single device does the request want to {verb.phrasing}?",
            tuple(o.label for o in opts),
        )
    for verb_name in plan.params:
        verb = next(v for v in shape.fired_verbs if v.name == verb_name)
        spec = verb.param
        if isinstance(spec, ScoreSpec):
            qs[f"param:{verb_name}"] = ScoreQ(f"What {spec.name.replace('_', ' ')} does the request ask for?", spec.levels)
        elif isinstance(spec, ChoiceSpec):
            qs[f"param:{verb_name}"] = ChoiceQ(f"Which {spec.name.replace('_', ' ')} does the request ask for?", spec.options)
    if plan.condition_candidates and shape.condition_domain:
        qs["cond_subject"] = ChoiceQ(
            "Which device is the request's condition about?",
            tuple(o.label for o in target_options(plan.condition_candidates)),
        )
        qs["cond_state"] = ChoiceQ(
            "Which state must that device be in for the request's condition to hold?",
            DOMAIN_STATES.get(shape.condition_domain, ("on", "off")),
        )
    return qs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_round2.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/round2.py packages/hunch/tests/test_round2.py
git commit -m "Add Round 2 planning, state and target/param/condition questions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Resolver

**Files:**
- Create: `packages/hunch/src/hunch/resolver.py`, `packages/hunch/tests/test_resolver.py`

**Interfaces:**
- Consumes: `Shape`, `Round2Plan`, `TargetOption`, `Answers`, `EngineConfig`, `Trace`, `Action`, `Condition`, all `Resolution` variants, `ScoreSpec`, `ChoiceSpec`, `Risk`.
- Produces:
  - `resolve(shape: Shape, plan: Round2Plan, round2: Answers | None, config: EngineConfig, trace: Trace) -> Resolution`
  - `score_to_value(spec: ScoreSpec, score: float) -> float` — linear interpolation between `spec.values` at the (1-based) `score`

Rules, in order (spec §6 step 5): any `DESTRUCTIVE` verb or `is_destructive` flag → `Escalate("destructive")`; then compute actions; any `CONFIRM` verb, any collective action over `max_silent_targets`, or confidence in `[confirm_band, auto_execute)` → `NeedsConfirmation`; confidence ≥ `auto_execute` → `Resolved`; else `Escalate("low_confidence", partial)`.

Confidence contributions per action: the verb Noul; for collective — the `collective` flag; for exclusions — `1 - p(exclude)` for kept entities and `p(exclude)` for excluded ones (both are decisions we're relying on); for singular — the target Choice confidence; for params — the param confidence; for conditions — both Choice confidences.

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_resolver.py`:
```python
from hunch.config import EngineConfig
from hunch.questions import Answers, ChoiceA, NoulA, ScoreA
from hunch.resolution import Escalate, NeedsConfirmation, Resolved, Trace
from hunch.resolver import resolve, score_to_value
from hunch.round1 import Shape
from hunch.round2 import plan_round2
from hunch.vocabulary import DEFAULT_VOCABULARY as V


def _shape(verbs, flags=None, verb_probs=None, condition_domain=None):
    f = {k: 0.05 for k in ("collective", "has_exception", "has_condition", "is_query", "has_timing", "is_destructive")}
    f.update(flags or {})
    return Shape(tuple(V.by_name(v) for v in verbs), (), (), {}, {}, f, None, condition_domain), (verb_probs or {})


def _trace_with_verbs(verb_probs):
    t = Trace()
    for name, p in verb_probs.items():
        t.decide(f"verb:{name}", p, 0.7)
    return t


def _ents(home, *ids):
    return tuple(home.entity_by_id(i) for i in ids)


def test_score_to_value_interpolates():
    spec = V.by_name("set_brightness").param
    assert score_to_value(spec, 1.0) == 0
    assert score_to_value(spec, 6.0) == 100
    assert score_to_value(spec, 3.5) == 37.5


def test_collective_resolves_without_round2(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.9}, {"turn_off": 0.95})
    cands = _ents(home, "light.kitchen_ceiling", "light.kitchen_counter")
    plan = plan_round2(home, shape, {"turn_off": cands}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert r.actions[0].verb.name == "turn_off" and r.actions[0].targets == cands
    assert r.confidence == 0.9  # min(verb 0.95, collective 0.9)


def test_exceptions_drop_excluded_entities(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.9, "has_exception": 0.85}, {"turn_off": 0.95})
    cands = _ents(home, "light.kitchen_ceiling", "switch.fridge")
    plan = plan_round2(home, shape, {"turn_off": cands}, config.thresholds)
    r2 = Answers("m", {
        "exclude:turn_off:light.kitchen_ceiling": NoulA(0.05),
        "exclude:turn_off:switch.fridge": NoulA(0.92),
    }, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert [e.entity_id for e in r.actions[0].targets] == ["light.kitchen_ceiling"]
    assert abs(r.confidence - 0.85) < 1e-9  # min(0.95, 0.9, 0.85 exception flag, 0.95 kept, 0.92 excluded)


def test_singular_picks_choice_target(home, config):
    shape, vp = _shape(["turn_on"], {"collective": 0.1}, {"turn_on": 0.9})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, config.thresholds)
    r2 = Answers("m", {"target:turn_on": ChoiceA("Reading lamp", 0.88, {"Reading lamp": 0.88, "Living room main": 0.12})}, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert [e.entity_id for e in r.actions[0].targets] == ["light.reading_lamp"]
    assert r.confidence == 0.88


def test_param_is_interpolated_into_action(home, config):
    shape, vp = _shape(["set_brightness"], {"collective": 0.9}, {"set_brightness": 0.9})
    plan = plan_round2(home, shape, {"set_brightness": _ents(home, "light.office_desk")}, config.thresholds)
    r2 = Answers("m", {"param:set_brightness": ScoreA(3.5, 0.8, {})}, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Resolved)
    assert r.actions[0].params == {"brightness_pct": 37.5}


def test_confirm_tier_verb_needs_confirmation(home, config):
    shape, vp = _shape(["unlock"], {"collective": 0.9}, {"unlock": 0.95})
    plan = plan_round2(home, shape, {"unlock": _ents(home, "lock.front_door")}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, NeedsConfirmation) and r.reason == "risk:confirm"


def test_blast_radius_needs_confirmation(home, config):
    cfg = EngineConfig(model="m", max_silent_targets=2)
    shape, vp = _shape(["turn_off"], {"collective": 0.95}, {"turn_off": 0.95})
    cands = tuple(e for e in home.entities if "turn_off" in e.verbs)
    plan = plan_round2(home, shape, {"turn_off": cands}, cfg.thresholds)
    r = resolve(shape, plan, None, cfg, _trace_with_verbs(vp))
    assert isinstance(r, NeedsConfirmation) and r.reason == "blast_radius"


def test_mid_confidence_needs_confirmation(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.66}, {"turn_off": 0.72})
    plan = plan_round2(home, shape, {"turn_off": _ents(home, "light.hallway")}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, NeedsConfirmation) and r.reason == "confidence"


def test_low_confidence_escalates_with_partial(home, config):
    shape, vp = _shape(["turn_on"], {"collective": 0.1}, {"turn_on": 0.9})
    cands = _ents(home, "light.living_main", "light.reading_lamp")
    plan = plan_round2(home, shape, {"turn_on": cands}, config.thresholds)
    r2 = Answers("m", {"target:turn_on": ChoiceA("Reading lamp", 0.4, {"Reading lamp": 0.4, "Living room main": 0.35})}, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    assert isinstance(r, Escalate) and r.reason == "low_confidence"
    assert r.partial[0].verb.name == "turn_on"


def test_destructive_flag_escalates_before_anything(home, config):
    shape, vp = _shape(["turn_off"], {"collective": 0.9, "is_destructive": 0.8}, {"turn_off": 0.95})
    plan = plan_round2(home, shape, {"turn_off": _ents(home, "light.hallway")}, config.thresholds)
    r = resolve(shape, plan, None, config, _trace_with_verbs(vp))
    assert isinstance(r, Escalate) and r.reason == "destructive"


def test_condition_is_attached_not_evaluated(home, config):
    # "if the blinds are closed, lock the front door"
    shape, vp = _shape(["lock"], {"collective": 0.9, "has_condition": 0.8}, {"lock": 0.9}, condition_domain="cover")
    plan = plan_round2(home, shape, {"lock": _ents(home, "lock.front_door")}, config.thresholds)
    r2 = Answers("m", {
        "cond_subject": ChoiceA("Blinds", 0.9, {"Blinds": 0.9}),
        "cond_state": ChoiceA("closed", 0.85, {"closed": 0.85, "open": 0.15}),
    }, None)
    r = resolve(shape, plan, r2, config, _trace_with_verbs(vp))
    # lock is CONFIRM tier, so we get NeedsConfirmation, but the condition rides along
    assert isinstance(r, NeedsConfirmation) and r.reason == "risk:confirm"
    assert r.condition is not None and r.condition.subject.entity_id == "cover.living_blinds"
    assert r.condition.expected_state == "closed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_resolver.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.resolver'`.

- [ ] **Step 3: Implement `resolver.py`**

```python
"""Turn probabilities into a Resolution. Confidence is the minimum of everything we relied on."""

from __future__ import annotations

from hunch.config import EngineConfig
from hunch.model import Entity
from hunch.questions import Answers
from hunch.resolution import (
    Action,
    Condition,
    Escalate,
    NeedsConfirmation,
    Resolution,
    Resolved,
    Trace,
)
from hunch.round1 import Shape
from hunch.round2 import Round2Plan, target_options
from hunch.vocabulary import ChoiceSpec, Risk, ScoreSpec


def score_to_value(spec: ScoreSpec, score: float) -> float:
    idx = score - 1.0  # Jev scores are 1-based over the rubric levels
    lo = max(0, min(len(spec.values) - 1, int(idx)))
    hi = min(len(spec.values) - 1, lo + 1)
    frac = idx - lo
    return spec.values[lo] + (spec.values[hi] - spec.values[lo]) * frac


def _verb_prob(trace: Trace, verb_name: str) -> float:
    for d in reversed(trace.decisions):
        if d.name == f"verb:{verb_name}":
            return d.value
    return 1.0


def resolve(
    shape: Shape, plan: Round2Plan, round2: Answers | None, config: EngineConfig, trace: Trace
) -> Resolution:
    th = config.thresholds
    if round2 is not None:
        trace.record(2, round2)

    if trace.decide("flag:is_destructive", shape.flag("is_destructive"), th.flag) or any(
        v.risk is Risk.DESTRUCTIVE for v in shape.fired_verbs
    ):
        return Escalate("destructive", (), trace)

    actions: list[Action] = []
    contributions: list[float] = []
    reasons: list[str] = []

    for verb in shape.fired_verbs:
        contributions.append(_verb_prob(trace, verb.name))
        targets: tuple[Entity, ...] = ()

        if verb.name in plan.collective:
            targets = plan.collective[verb.name]
            if len(targets) > 1:  # a single deterministic candidate never relied on the collective flag
                contributions.append(shape.flag("collective"))
        elif verb.name in plan.exclude and round2 is not None:
            kept: list[Entity] = []
            contributions.append(shape.flag("collective"))
            contributions.append(shape.flag("has_exception"))
            for e in plan.exclude[verb.name]:
                p = round2.noul(f"exclude:{verb.name}:{e.entity_id}")
                excluded = trace.decide(f"exclude:{verb.name}:{e.entity_id}", p, 0.5)
                contributions.append(p if excluded else 1.0 - p)
                if not excluded:
                    kept.append(e)
            targets = tuple(kept)
        elif verb.name in plan.singular and round2 is not None:
            c = round2.choice(f"target:{verb.name}")
            contributions.append(c.confidence)
            trace.decide(f"target:{verb.name}", c.confidence, th.target_choice_conf)
            opt = next((o for o in plan.singular[verb.name] if o.label == c.choice), None)
            targets = opt.entities if opt else ()
        elif shape.scene is not None and verb.name == "activate":
            targets = (shape.scene,)

        params: dict[str, float | str] = {}
        if verb.param is not None and round2 is not None and f"param:{verb.name}" in round2.answers:
            if isinstance(verb.param, ScoreSpec):
                s = round2.score(f"param:{verb.name}")
                params[verb.param.name] = score_to_value(verb.param, s.score)
                contributions.append(s.confidence)
            elif isinstance(verb.param, ChoiceSpec):
                c = round2.choice(f"param:{verb.name}")
                params[verb.param.name] = c.choice
                contributions.append(c.confidence)

        if not targets:
            continue
        actions.append(Action(verb, targets, params))
        if verb.risk is Risk.CONFIRM:
            reasons.append("risk:confirm")
        if len(targets) > config.max_silent_targets:
            reasons.append("blast_radius")

    condition: Condition | None = None
    if plan.condition_candidates and round2 is not None and "cond_subject" in round2.answers:
        subj = round2.choice("cond_subject")
        state = round2.choice("cond_state")
        contributions.extend((subj.confidence, state.confidence))
        opt = next((o for o in target_options(plan.condition_candidates) if o.label == subj.choice), None)
        if opt:
            condition = Condition(opt.entities[0], state.choice)

    if not actions:
        return Escalate("low_confidence", (), trace)

    confidence = min(contributions) if contributions else 0.0
    trace.decide("confidence", confidence, th.auto_execute)

    if reasons:
        return NeedsConfirmation(tuple(actions), condition, reasons[0], trace)
    if confidence >= th.auto_execute:
        return Resolved(tuple(actions), condition, confidence, trace)
    if confidence >= th.confirm_band:
        return NeedsConfirmation(tuple(actions), condition, "confidence", trace)
    return Escalate("low_confidence", tuple(actions), trace)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/hunch/tests/test_resolver.py -q`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/resolver.py packages/hunch/tests/test_resolver.py
git commit -m "Add resolver: risk tiers, blast radius, min-confidence gating

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Engine orchestration

**Files:**
- Create: `packages/hunch/src/hunch/engine.py`, `packages/hunch/tests/test_engine.py`
- Modify: `packages/hunch/src/hunch/__init__.py` (public re-exports)

**Interfaces:**
- Consumes: everything above.
- Produces: `Engine(client: DecisionClient, vocabulary: Vocabulary, config: EngineConfig)` with `async def decide(self, home: HomeModel, prompt: str) -> Resolution`.

Flow: pre-checks → Round 1 → `interpret_round1` → `has_timing` / no-intent escalation → scene short-circuit → per-verb `scope_candidates` (a `Clarify` or `ScopeEscalate` from any verb ends the request; a `DeviceRound` triggers one extra `ask` with a `device_round` Choice, honouring `max_rounds`) → `plan_round2` → Round 2 if any questions → `resolve`. Any `DecisionBackendError` → `Escalate("decision_backend_unavailable")`.

- [ ] **Step 1: Write the failing tests**

`packages/hunch/tests/test_engine.py`:
```python
import pytest

from hunch.client import DecisionBackendError, FakeDecisionClient
from hunch.config import EngineConfig
from hunch.engine import Engine
from hunch.questions import ChoiceA, ChoiceQ, NoulA, NoulQ, ScoreQ
from hunch.resolution import Escalate, NeedsClarification, Resolved


def _scripted(round1: dict, round2: dict | None = None, device: dict | None = None):
    """Answer Round 1 by id override, default everything else to 'no'; Round 2 / device round by id."""
    calls = {"n": 0}

    def script(state, qs):
        calls["n"] += 1
        out = {}
        for qid, q in qs.items():
            if qid in round1:
                out[qid] = round1[qid]
            elif round2 and qid in round2:
                out[qid] = round2[qid]
            elif device and qid in device:
                out[qid] = device[qid]
            elif isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
            elif isinstance(q, ScoreQ):
                from hunch.questions import ScoreA
                out[qid] = ScoreA(1.0, 0.9, {})
            else:
                out[qid] = NoulA(0.05)
        return out

    return FakeDecisionClient(script), calls


async def test_collective_downstairs_resolves_in_one_round(home, vocab, config):
    client, calls = _scripted({
        "verb:turn_off": NoulA(0.95), "floor:downstairs": NoulA(0.9),
        "domain:light": NoulA(0.9), "flag:collective": NoulA(0.9),
    })
    r = await Engine(client, vocab, config).decide(home, "turn off the downstairs lights")
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {
        "light.kitchen_ceiling", "light.kitchen_counter", "light.living_main", "light.reading_lamp", "light.hallway"}
    assert calls["n"] == 1


async def test_exception_uses_second_round(home, vocab, config):
    client, calls = _scripted(
        {"verb:turn_off": NoulA(0.95), "area:kitchen": NoulA(0.9), "flag:collective": NoulA(0.9), "flag:has_exception": NoulA(0.85)},
        {"exclude:turn_off:switch.fridge": NoulA(0.93)},
    )
    r = await Engine(client, vocab, config).decide(home, "turn off everything in the kitchen except the fridge")
    assert isinstance(r, Resolved)
    assert {e.entity_id for e in r.actions[0].targets} == {"light.kitchen_ceiling", "light.kitchen_counter"}
    assert calls["n"] == 2
    assert "entities" not in client.calls[0][0] and "candidates" in client.calls[1][0]


async def test_singular_uses_choice(home, vocab, config):
    client, _ = _scripted(
        {"verb:turn_on": NoulA(0.9), "area:living": NoulA(0.85), "domain:light": NoulA(0.8), "flag:collective": NoulA(0.1)},
        {"target:turn_on": ChoiceA("Reading lamp", 0.9, {})},
    )
    r = await Engine(client, vocab, config).decide(home, "turn on the lamp in the lounge")
    assert isinstance(r, Resolved)
    assert [e.entity_id for e in r.actions[0].targets] == ["light.reading_lamp"]


async def test_timing_escalates_immediately(home, vocab, config):
    client, calls = _scripted({"verb:turn_off": NoulA(0.9), "flag:has_timing": NoulA(0.8)})
    r = await Engine(client, vocab, config).decide(home, "turn off the lights in ten minutes")
    assert isinstance(r, Escalate) and r.reason == "timing" and calls["n"] == 1


async def test_no_intent_escalates(home, vocab, config):
    client, _ = _scripted({})
    r = await Engine(client, vocab, config).decide(home, "tell me a joke")
    assert isinstance(r, Escalate) and r.reason == "no_intent"


async def test_scene_short_circuits(home, vocab, config):
    client, calls = _scripted({
        "verb:activate": NoulA(0.9),
        "scene": ChoiceA("Movie night", 0.9, {"Movie night": 0.9, "Goodnight": 0.05, "none": 0.05}),
    })
    r = await Engine(client, vocab, config).decide(home, "movie night please")
    assert isinstance(r, Resolved) and r.actions[0].targets[0].entity_id == "scene.movie_night"
    assert calls["n"] == 1


async def test_clarify_when_scope_too_wide(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=False)
    client, _ = _scripted({"verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.1), "domain:light": NoulA(0.3)})
    r = await Engine(client, vocab, cfg).decide(home, "turn on the light")
    assert isinstance(r, NeedsClarification) and r.question_key == "which_area"


async def test_device_round_when_enabled(home, vocab):
    cfg = EngineConfig(model="jev-1.13.0", scope_cap=2, device_round=True, max_rounds=3)
    client, calls = _scripted(
        {"verb:turn_on": NoulA(0.9), "flag:collective": NoulA(0.1), "domain:light": NoulA(0.3)},
        {"target:turn_on": ChoiceA("Christmas tree", 0.9, {})},
        {"device_round": ChoiceA("Christmas tree", 0.9, {})},
    )
    r = await Engine(client, vocab, cfg).decide(home, "turn on the christmas tree")
    assert isinstance(r, Resolved) and r.actions[0].targets[0].entity_id == "light.christmas_tree"
    assert calls["n"] == 2  # round 1 + device round; single-option target needs no round 2


async def test_backend_error_escalates(home, vocab, config):
    class Boom:
        async def ask(self, state, questions):
            raise DecisionBackendError("decision_backend_unavailable")
    r = await Engine(Boom(), vocab, config).decide(home, "turn off the lights")
    assert isinstance(r, Escalate) and r.reason == "decision_backend_unavailable"


async def test_prompt_prechecks(home, vocab, config):
    eng = Engine(FakeDecisionClient({}), vocab, config)
    assert isinstance(await eng.decide(home, "   "), Escalate)
    assert (await eng.decide(home, "x" * 501)).reason == "prompt_invalid"


async def test_trace_travels_with_result(home, vocab, config):
    client, _ = _scripted({"verb:turn_off": NoulA(0.95), "area:hallway": NoulA(0.9), "flag:collective": NoulA(0.9)})
    r = await Engine(client, vocab, config).decide(home, "hallway off")
    assert r.trace.models == ["fake"]
    assert any(e.question_id == "verb:turn_off" for e in r.trace.entries)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/hunch/tests/test_engine.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hunch.engine'`.

- [ ] **Step 3: Implement `engine.py`**

```python
"""Orchestrates rounds. Decides; never executes."""

from __future__ import annotations

from hunch.client import DecisionBackendError, DecisionClient
from hunch.config import EngineConfig
from hunch.model import Entity, HomeModel
from hunch.questions import ChoiceQ, Question
from hunch.resolution import Escalate, NeedsClarification, Resolution, Trace
from hunch.resolver import resolve
from hunch.round1 import build_round1_questions, build_round1_state, interpret_round1
from hunch.round2 import build_round2_questions, build_round2_state, plan_round2, target_options
from hunch.scope import Candidates, Clarify, DeviceRound, ScopeEscalate, scope_candidates
from hunch.vocabulary import Vocabulary


class Engine:
    def __init__(self, client: DecisionClient, vocabulary: Vocabulary, config: EngineConfig) -> None:
        self._client = client
        self._vocab = vocabulary
        self._config = config

    async def decide(self, home: HomeModel, prompt: str) -> Resolution:
        trace = Trace()
        prompt = prompt.strip()
        if not prompt or len(prompt) > self._config.max_prompt_chars:
            return Escalate("prompt_invalid", (), trace)
        try:
            return await self._decide(home, prompt, trace)
        except DecisionBackendError as exc:
            trace.note(f"backend_error:{exc.reason}")
            return Escalate("decision_backend_unavailable", (), trace)

    async def _decide(self, home: HomeModel, prompt: str, trace: Trace) -> Resolution:
        th = self._config.thresholds
        rounds = 0

        answers = await self._client.ask(build_round1_state(home, prompt), build_round1_questions(home, self._vocab))
        rounds += 1
        shape = interpret_round1(home, self._vocab, answers, th, trace)

        if trace.decide("flag:has_timing", shape.flag("has_timing"), th.flag):
            return Escalate("timing", (), trace)
        if not shape.fired_verbs:
            return Escalate("no_intent", (), trace)

        per_verb: dict[str, tuple[Entity, ...]] = {}
        for verb in shape.fired_verbs:
            if shape.scene is not None and verb.name == "activate":
                per_verb[verb.name] = (shape.scene,)
                continue
            result = scope_candidates(home, verb, shape, self._config, trace)
            if isinstance(result, Candidates):
                per_verb[verb.name] = result.entities
            elif isinstance(result, Clarify):
                return NeedsClarification(result.question_key, result.candidates, trace)
            elif isinstance(result, ScopeEscalate):
                return Escalate(result.reason, (), trace)
            elif isinstance(result, DeviceRound):
                if rounds >= self._config.max_rounds:
                    return NeedsClarification("which_device", result.entities, trace) \
                        if self._config.supports_clarification else Escalate("scope", (), trace)
                per_verb[verb.name] = await self._device_round(prompt, result.entities, trace)
                rounds += 1
                if not per_verb[verb.name]:
                    return Escalate("scope", (), trace)

        plan = plan_round2(home, shape, per_verb, th)
        questions: dict[str, Question] = build_round2_questions(shape, plan, home)
        round2 = None
        if questions:
            if rounds >= self._config.max_rounds:
                trace.note("max_rounds_reached_before_round2")
                return Escalate("low_confidence", (), trace)
            round2 = await self._client.ask(build_round2_state(prompt, plan), questions)
            rounds += 1
        return resolve(shape, plan, round2, self._config, trace)

    async def _device_round(self, prompt: str, entities: tuple[Entity, ...], trace: Trace) -> tuple[Entity, ...]:
        opts = target_options(entities)
        q = ChoiceQ("Which device does the request refer to?", tuple(o.label for o in opts))
        answers = await self._client.ask(
            {"request": prompt, "devices": [o.label for o in opts]}, {"device_round": q}
        )
        trace.record(2, answers)
        c = answers.choice("device_round")
        if not trace.decide("device_round", c.confidence, self._config.thresholds.target_choice_conf):
            return ()
        opt = next((o for o in opts if o.label == c.choice), None)
        return opt.entities if opt else ()
```

Update `packages/hunch/src/hunch/__init__.py`:
```python
"""Hunch: code calculates, Jev judges."""

from hunch.client import DecisionBackendError, DecisionClient, FakeDecisionClient, TypeSafeDecisionClient
from hunch.config import EngineConfig, Thresholds
from hunch.engine import Engine
from hunch.model import Area, Entity, Floor, HomeModel
from hunch.resolution import (
    Action,
    Condition,
    Escalate,
    NeedsClarification,
    NeedsConfirmation,
    Resolution,
    Resolved,
    Trace,
)
from hunch.vocabulary import DEFAULT_VOCABULARY, ChoiceSpec, Risk, ScoreSpec, Verb, Vocabulary

__version__ = "0.1.0"

__all__ = [
    "Action", "Area", "ChoiceSpec", "Condition", "DEFAULT_VOCABULARY", "DecisionBackendError",
    "DecisionClient", "Engine", "EngineConfig", "Entity", "Escalate", "FakeDecisionClient", "Floor",
    "HomeModel", "NeedsClarification", "NeedsConfirmation", "Resolution", "Resolved", "Risk",
    "ScoreSpec", "Thresholds", "Trace", "TypeSafeDecisionClient", "Verb", "Vocabulary",
]
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q && uv run ruff check packages/`
Expected: all tests pass (≈70), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add packages/hunch/src/hunch/engine.py packages/hunch/src/hunch/__init__.py packages/hunch/tests/test_engine.py
git commit -m "Add Engine orchestration with device round and backend error handling

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Golden corpus against the real API

**Files:**
- Create: `golden/corpus.yaml`, `golden/run_golden.py`, `golden/README.md`

**Interfaces:**
- Consumes: the public `hunch` API (`Engine`, `TypeSafeDecisionClient`, `EngineConfig`, `DEFAULT_VOCABULARY`), the test fixture home (imported from `packages/hunch/tests/conftest.py` via a small loader).
- Produces: a runnable report — per prompt: expected vs actual resolution kind, verb, target ids, latency, tokens, input cost; a summary line with agreement rate and p50/p95 latency. Exit code 1 if agreement < 0.8.

- [ ] **Step 1: Write the corpus**

`golden/corpus.yaml` — 24 rows covering every path. `expect.kind` ∈ `resolved | confirm | clarify | escalate`; `targets` are entity ids (order-insensitive); `reason` only for escalate/confirm.
```yaml
- prompt: turn off the downstairs lights
  expect: {kind: resolved, verb: turn_off, targets: [light.kitchen_ceiling, light.kitchen_counter, light.living_main, light.reading_lamp, light.hallway]}
- prompt: turn off everything in the kitchen except the fridge
  expect: {kind: resolved, verb: turn_off, targets: [light.kitchen_ceiling, light.kitchen_counter]}
- prompt: switch on the lamp in the lounge
  expect: {kind: resolved, verb: turn_on, targets: [light.reading_lamp]}
- prompt: turn on the reading lamp
  expect: {kind: resolved, verb: turn_on, targets: [light.reading_lamp]}
- prompt: dim the office light to about half
  expect: {kind: resolved, verb: set_brightness, targets: [light.office_desk], params: {brightness_pct: [40, 60]}}
- prompt: close the living room blinds
  expect: {kind: resolved, verb: close, targets: [cover.living_blinds]}
- prompt: open the blinds halfway
  expect: {kind: resolved, verb: set_position, targets: [cover.living_blinds]}
- prompt: lock the front door
  expect: {kind: confirm, verb: lock, targets: [lock.front_door], reason: "risk:confirm"}
- prompt: unlock the front door
  expect: {kind: confirm, verb: unlock, targets: [lock.front_door], reason: "risk:confirm"}
- prompt: movie night
  expect: {kind: resolved, verb: activate, targets: [scene.movie_night]}
- prompt: run the goodnight script
  expect: {kind: resolved, verb: activate, targets: [script.goodnight]}
- prompt: turn everything off
  expect: {kind: confirm, verb: turn_off, reason: blast_radius}
- prompt: set the bedroom to 22 degrees
  expect: {kind: resolved, verb: set_temperature, targets: [climate.bedroom], params: {temperature: [21, 23]}}
- prompt: make the bedroom warmer
  expect: {kind: resolved, verb: set_temperature, targets: [climate.bedroom]}
- prompt: turn on the christmas tree
  expect: {kind: resolved, verb: turn_on, targets: [light.christmas_tree]}
- prompt: turn on both bedside lamps
  expect: {kind: resolved, verb: turn_on, targets: [light.bedroom_left, light.bedroom_right]}
- prompt: is the front door locked
  expect: {kind: resolved, verb: query_state, targets: [lock.front_door]}
- prompt: are the blinds open
  expect: {kind: resolved, verb: query_state, targets: [cover.living_blinds]}
- prompt: turn off the lights in ten minutes
  expect: {kind: escalate, reason: timing}
- prompt: turn off the lights after the movie finishes
  expect: {kind: escalate, reason: timing}
- prompt: tell me a joke
  expect: {kind: escalate, reason: no_intent}
- prompt: what's the weather like tomorrow
  expect: {kind: escalate, reason: no_intent}
- prompt: if the blinds are closed lock the front door
  expect: {kind: confirm, verb: lock, targets: [lock.front_door], condition: {subject: cover.living_blinds, state: closed}}
- prompt: turn off the kitchen lights and close the blinds
  expect: {kind: resolved, verbs: [turn_off, close]}
```

- [ ] **Step 2: Write the runner**

`golden/run_golden.py`:
```python
"""Run the golden corpus against the real Jev API. Gated on TYPESAFE_API_KEY; not part of CI.

Run:  uv run python golden/run_golden.py [--model jev-1.13.0] [--only 'kitchen']
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
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
    spec = importlib.util.spec_from_file_location("conftest", ROOT / "packages/hunch/tests/conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.home.__wrapped__()  # unwrap the pytest fixture


def kind_of(r) -> str:
    return {Resolved: "resolved", NeedsConfirmation: "confirm", NeedsClarification: "clarify", Escalate: "escalate"}[type(r)]


def check(row, r) -> list[str]:
    exp, problems = row["expect"], []
    if kind_of(r) != exp["kind"]:
        problems.append(f"kind {kind_of(r)} != {exp['kind']}")
        return problems
    actions = getattr(r, "actions", ())
    if "verb" in exp and not any(a.verb.name == exp["verb"] for a in actions):
        problems.append(f"verb {[a.verb.name for a in actions]} lacks {exp['verb']}")
    if "verbs" in exp and {a.verb.name for a in actions} != set(exp["verbs"]):
        problems.append(f"verbs {[a.verb.name for a in actions]} != {exp['verbs']}")
    if "targets" in exp:
        got = {e.entity_id for a in actions if a.verb.name == exp.get("verb") for e in a.targets}
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
        if c is None or c.subject.entity_id != exp["condition"]["subject"] or c.expected_state != exp["condition"]["state"]:
            problems.append(f"condition {c} != {exp['condition']}")
    return problems


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="jev-1.13.0")
    ap.add_argument("--only", default=None)
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
    for row in rows:
        t0 = time.perf_counter()
        r = await engine.decide(home, row["prompt"])
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        toks = sum(e.answer.probability * 0 for e in r.trace.entries)  # placeholder-free: tokens come from trace notes below
        problems = check(row, r)
        ok += not problems
        mark = "PASS" if not problems else "FAIL"
        rounds = len(r.trace.models)
        print(f"{mark}  {ms:6.0f} ms  rounds={rounds}  {row['prompt']!r}")
        for pr in problems:
            print(f"        - {pr}")
    await client.aclose()

    agreement = ok / max(len(rows), 1)
    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1] if len(latencies) >= 20 else max(latencies)
    print(f"\n{ok}/{len(rows)} agree ({agreement:.0%})  p50={p50:.0f} ms  p95={p95:.0f} ms")
    return 0 if agreement >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

Remove the `toks = ...` line and the `tokens` accumulator before running — token accounting is added in Step 4 once `Answers.input_tokens` is surfaced through the trace.

- [ ] **Step 3: Surface token usage in the trace**

Modify `packages/hunch/src/hunch/resolution.py` — add `input_tokens: list[int | None]` to `Trace` and append `answers.input_tokens` in `record`. Add to `test_questions.py::test_trace_records_answers_decisions_and_models` the assertion `assert t.input_tokens == [300]`. Run `uv run pytest -q` — expected: all pass.

- [ ] **Step 4: Finish the runner's cost line**

In `run_golden.py`, replace the `toks` line with `toks = sum(t or 0 for t in r.trace.input_tokens)`, accumulate `tokens += toks`, and extend the summary: `cost=${tokens / 1e6 * PRICE_PER_M_TOKENS:.4f}  tokens={tokens}`.

- [ ] **Step 5: Run the corpus**

Run: `uv run python golden/run_golden.py`
Expected: a PASS/FAIL line per prompt and a summary. First run will likely land below 80%. For each FAIL, inspect `r.trace` (add `--verbose` printing `r.trace.to_dict()` if useful) and decide: threshold tweak in `Thresholds` defaults, instruction wording in `round1.py` / `round2.py`, or a corpus expectation that was wrong. Iterate until ≥ 80%. Record final numbers in `golden/README.md` with the model id and date.

- [ ] **Step 6: Commit**

```bash
git add golden/ packages/hunch/src/hunch/resolution.py packages/hunch/tests/test_questions.py packages/hunch/src/hunch/round1.py packages/hunch/src/hunch/round2.py packages/hunch/src/hunch/config.py
git commit -m "Add golden corpus runner and first tuning pass against Jev

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage.** §4.1 components → Tasks 2–10. §5 data model → Tasks 2–4 (one deviation: `ScoreSpec` gained `values` so a rubric level maps to a number; `Thresholds` gained `flag`; `EngineConfig` gained `max_prompt_chars`). §6 pipeline → Tasks 6–10; the device→entity two-step Choice is collapsed into one Choice with composite labels (Task 8 note). §6 failure handling → Task 5 + Task 10. §7 safety → Task 9 (tiers, blast radius, destructive) and Task 6 (`is_destructive` flag). §8 conversation flows → integration sub-project; `confirm_reply` id reserved. §9 testing → every task plus Task 11. §10 open question 2 → Task 1.

**Deliberate deviations from the spec, to fold back into it after execution:** `strict_candidates` returns empty when *both* area and domain scope are empty, so "no signal" always flows through the widening chain and its cap; ranked widening ignores probabilities ≤ 0.1 as noise; the collective flag only counts toward confidence when more than one target was selected.

**Type consistency.** `Shape` is constructed positionally in three test files in field order `(fired_verbs, scope_areas, scope_domains, area_probs, domain_probs, flags, scene, condition_domain)`. `Trace.decide(name, value, threshold) -> bool` everywhere. Question ids follow the table at the top. `target_options` is shared by `round2`, `resolver` and `engine`.
