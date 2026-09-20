# Hunch HA Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A HACS-installable Home Assistant custom integration `custom_components/hunch` that runs the `hunch` engine as a conversation agent: builds the home model from HA registries, executes resolved actions as exact service calls, holds Jev-judged confirmation/clarification turns, and escalates everything else to a fallback agent with the unchanged text.

**Architecture:** Thin adapter. `HunchRuntime` (client, engine, builder, pending store, trace buffer) lives in `entry.runtime_data`. `HomeModelBuilder` assembles an export-shaped dict from the registries and reuses `hunch.loaders.home_from_export`, so the mapping has one implementation. `HunchConversationEntity` dispatches on the engine's four result types; `Executor` maps verbs to services; `Responder` renders every sentence (en/de); `PendingStore` holds one pending turn per conversation id for 120 s.

**Tech Stack:** Python 3.13, uv workspace, `hunch-engine` (this repo's `packages/hunch`, renamed for PyPI), Home Assistant 2026.9.3 test target via `pytest-homeassistant-custom-component==0.13.366` in uv group `ha`, ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-09-21-hunch-integration-design.md` (integration). Parent: `docs/superpowers/specs/2026-09-19-hunch-design.md` (engine).

## Global Constraints

- Engine changes are limited to: `NeedsClarification` gains `verb: Verb | None` and `params: Mapping[str, float | str]`; distribution renamed to `hunch-engine` (import stays `hunch`). Nothing else in `packages/hunch/src` changes.
- Component path `custom_components/hunch`; domain `hunch`; one config entry (`unique_id = DOMAIN`).
- `manifest.json`: `"requirements": ["hunch-engine==0.2.0"]`, `"dependencies": ["conversation", "homeassistant"]`, `"integration_type": "service"`, `"iot_class": "cloud_polling"`, `"config_flow": true`.
- Execution = `hass.services.async_call(domain, service, data, blocking=True, context=context)` with exact `entity_id` lists. Never intents.
- Engine `Phrasebook` is always `EN`. Response language: option `response_language` (`auto`|`en`|`de`), `auto` = request language prefix, anything not `de` → `en`.
- Pending turns: TTL 120 s, single-use, one per conversation id. Reply judgment confidence bar 0.7.
- Escalation: `conversation.async_converse(hass, text, conversation_id, context, language=..., agent_id=fallback_or_None, device_id=..., satellite_id=..., extra_system_prompt=...)` with the **unchanged** user text. Hunch never escalates to its own entity id.
- Traces: every Hunch-authored answer appends `AssistantContent(agent_id=self.entity_id, content=<sentence>, native=trace.to_dict())` to the chat log and pushes to `runtime.traces` (maxlen 50).
- The HA token / TypeSafe key never appear in logs, traces, diagnostics or tests.
- All new Python passes `uv run ruff format` and `uv run ruff check`. Default `uv run pytest` must keep passing without HA installed; HA tests run with `uv run --group ha pytest tests/integration`.
- Commit after every green step; commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File structure

```
packages/hunch/pyproject.toml                 rename dist → hunch-engine, version 0.2.0
packages/hunch/src/hunch/resolution.py        NeedsClarification.verb/params
packages/hunch/src/hunch/resolver.py          fill them
packages/hunch/src/hunch/engine.py            fill them (scope-time clarifications)
custom_components/hunch/__init__.py           HunchRuntime, build_client, setup/unload
custom_components/hunch/manifest.json
custom_components/hunch/const.py
custom_components/hunch/config_flow.py        ConfigFlow + OptionsFlowWithReload
custom_components/hunch/home_model.py         HomeModelBuilder
custom_components/hunch/executor.py           plan_calls, Executor
custom_components/hunch/responder.py          render, describe_*, pending_context
custom_components/hunch/pending.py            PendingStore, PendingConfirm, PendingClarify
custom_components/hunch/conversation.py       HunchConversationEntity
custom_components/hunch/diagnostics.py
custom_components/hunch/strings.json
custom_components/hunch/translations/en.json, de.json
hacs.json
tests/unit/                                   pure tests (default pytest)
tests/integration/                            HA-harness tests (group ha)
pyproject.toml                                ha group, testpaths, conflicts
```

---

### Task 1: Engine — `NeedsClarification` carries verb and params; rename distribution

**Files:**
- Modify: `packages/hunch/src/hunch/resolution.py:117-121`
- Modify: `packages/hunch/src/hunch/resolver.py` (pending_clarify sites ~lines 104, 203, 236, 326)
- Modify: `packages/hunch/src/hunch/engine.py:158,170`
- Modify: `packages/hunch/pyproject.toml`
- Test: `packages/hunch/tests/test_engine.py`, `packages/hunch/tests/test_resolver.py`

**Interfaces:**
- Produces: `NeedsClarification(question_key: str, candidates: tuple[Entity, ...], trace: Trace, verb: Verb | None = None, params: Mapping[str, float | str] = {})`. `verb` is the verb being clarified; `params` are the parameters resolved for it in Round 2, or `{}` when the clarification happened before Round 2 (scope-time) — the integration escalates a pick whose verb needs a param it does not have.

- [ ] **Step 1: Failing tests**

Append to `packages/hunch/tests/test_engine.py`:

```python
async def test_clarification_carries_the_verb_and_params(home, vocab, config):
    # weak pick among real options -> clarify; the integration needs verb + params to act on the pick
    client, _ = _scripted(
        {
            "verb:set_brightness": NoulA(0.95),
            "domain:light": NoulA(0.95),
            "area:bedroom": NoulA(0.99),
            "flag:names_specific": NoulA(0.9),
            "area_primary": ChoiceA("Bedroom", 0.99, {"Bedroom": 0.99}),
        },
        {
            "target:set_brightness": ChoiceA(
                "Bedside left", 0.45, {"Bedside left": 0.45, "Bedside right": 0.4}
            ),
            "all_of:set_brightness": NoulA(0.1),
            "param_value:set_brightness": ChoiceA("50%", 0.95, {"50%": 0.95}),
            "param_relative:set_brightness": NoulA(0.05),
            "param:set_brightness": ScoreA(3.0, 0.9, {}),
        },
    )
    r = await Engine(client, vocab, config).decide(home, "bedside lamp to 50% in the bedroom")
    assert isinstance(r, NeedsClarification)
    assert r.verb is not None and r.verb.name == "set_brightness"
    assert r.params == {"brightness_pct": 50.0}


async def test_scope_time_clarification_has_verb_but_no_params(home, vocab):
    # device_round on with max_rounds=3 but budget spent -> DeviceRound turns into a clarification
    from hunch.config import EngineConfig

    cfg = EngineConfig(model="m", device_round=True, max_rounds=3, scope_cap=1)
    client, _ = _scripted(
        {
            "verb:turn_on": NoulA(0.95),
            "domain:light": NoulA(0.95),
            "area:bedroom": NoulA(0.99),
            "flag:names_specific": NoulA(0.9),
            "area_primary": ChoiceA("Bedroom", 0.99, {"Bedroom": 0.99}),
        }
    )
    r = await Engine(client, vocab, cfg).decide(home, "bedside lamp on")
    if isinstance(r, NeedsClarification):
        assert r.verb is not None and r.verb.name == "turn_on"
        assert r.params == {}
```

(The second test tolerates the engine choosing the device round instead; it only asserts the invariant when a clarification is returned.)

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest packages/hunch/tests/test_engine.py -k clarification -v`
Expected: FAIL with `TypeError: NeedsClarification.__init__() got an unexpected keyword` / `AttributeError: 'NeedsClarification' object has no attribute 'verb'`.

- [ ] **Step 3: Implement**

`resolution.py`: replace the class with

```python
@dataclass(frozen=True)
class NeedsClarification:
    question_key: str  # "which_area" | "which_device"
    candidates: tuple[Entity, ...]
    trace: Trace
    # the verb being clarified and the params already resolved for it ({} when the
    # clarification happened before Round 2). Lets a caller act on the user's pick.
    verb: Verb | None = None
    params: Mapping[str, float | str] = field(default_factory=dict)
```

Add `from collections.abc import Mapping`, `from dataclasses import dataclass, field`, and `from hunch.vocabulary import Verb` (check it is not already imported; `Action` already references `Verb`).

`resolver.py`: next to `pending_clarify: tuple[Entity, ...] | None = None` (line ~104) add

```python
    pending_clarify_verb: Verb | None = None
    pending_clarify_params: dict[str, float | str] = {}
```

At both sites that set `pending_clarify = candidates[: config.clarify_max_candidates]` add `pending_clarify_verb = verb` on the following line (same indentation). After the params block, right before `if not targets:` add:

```python
        if pending_clarify_verb is verb and not pending_clarify_params:
            pending_clarify_params = dict(params)
```

Change the return at ~line 328 to
`return NeedsClarification("which_device", pending_clarify, trace, pending_clarify_verb, pending_clarify_params)`.
Import `Verb` from `hunch.vocabulary` if missing.

`engine.py`: line 158 → `NeedsClarification("which_device", result.entities, trace, verb)`; line 170 →

```python
                return NeedsClarification(
                    pending_clarify.question_key,
                    pending_clarify.candidates,
                    trace,
                    pending_clarify_verb,
                )
```

where `pending_clarify_verb: Verb | None = None` is declared next to `pending_clarify: Clarify | None = None` and set (`pending_clarify_verb = verb`) wherever `pending_clarify = result` is assigned in the scope loop. Update the docstring line 54 to mention `verb`, `params`.

- [ ] **Step 4: Run all engine tests**

Run: `uv run ruff format packages && uv run ruff check packages && uv run pytest packages/hunch/tests -q`
Expected: all pass (179 + 2).

- [ ] **Step 5: Rename the distribution**

In `packages/hunch/pyproject.toml` set `name = "hunch-engine"` and `version = "0.2.0"`. In the root `pyproject.toml` change `dependencies = ["hunch"]` → `["hunch-engine"]` and `[tool.uv.sources] hunch = …` → `hunch-engine = { workspace = true }`. Run `uv lock && uv sync && uv run pytest packages/hunch/tests -q` (import name `hunch` unchanged). Run `uv build --package hunch-engine` and check `dist/hunch_engine-0.2.0-py3-none-any.whl` exists.

- [ ] **Step 6: Commit**

```bash
git add packages/hunch pyproject.toml uv.lock
git commit -m "Engine: NeedsClarification carries verb and params; distribution renamed hunch-engine 0.2.0

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 7: Publish — STOP and ask Julian**

Publishing to PyPI is an outward-facing action. Report "ready to publish hunch-engine 0.2.0 with `uv publish` (needs `UV_PUBLISH_TOKEN` in `.env`)" and wait. Until published, the integration can be developed against the workspace package; HA installs will fail on the requirement, which is expected.

---

### Task 2: Component skeleton, runtime, HA test harness

**Files:**
- Create: `custom_components/hunch/__init__.py`, `manifest.json`, `const.py`, `conversation.py` (stub), `hacs.json`, `custom_components/__init__.py` (empty, for test discovery)
- Modify: `pyproject.toml` (ha group, testpaths)
- Create: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/integration/conftest.py`, `tests/integration/test_init.py`

**Interfaces:**
- Produces:
  ```python
  # const.py
  DOMAIN = "hunch"
  CONF_API_KEY = "api_key"
  OPT_FALLBACK_AGENT = "fallback_agent"
  OPT_MODEL = "model"; DEFAULT_MODEL = "jev-1.13.0"
  OPT_RESPONSE_LANGUAGE = "response_language"; DEFAULT_RESPONSE_LANGUAGE = "auto"
  OPT_TIMEOUT_MS = "timeout_ms"; DEFAULT_TIMEOUT_MS = 1500
  OPT_MAX_SILENT_TARGETS = "max_silent_targets"
  OPT_DEVICE_ROUND = "device_round"
  OPT_MAX_ROUNDS = "max_rounds"
  OPT_THRESHOLDS = "thresholds"   # dict[str, float], keys = Thresholds field names
  PENDING_TTL_SECONDS = 120
  TRACE_BUFFER = 50
  # __init__.py
  @dataclass class HunchRuntime: client, engine, builder, pending, traces, fallback_agent_id, response_language
  type HunchConfigEntry = ConfigEntry[HunchRuntime]
  def build_engine_config(options: Mapping[str, Any]) -> EngineConfig
  def build_client(entry: ConfigEntry) -> DecisionClient      # patched in tests
  ```
- Tests patch `custom_components.hunch.build_client` to return a `hunch.FakeDecisionClient`.

- [ ] **Step 1: Tooling**

Root `pyproject.toml`:

```toml
[dependency-groups]
dev = [ ...unchanged... ]
ha = [
    "homeassistant==2026.9.3",
    "pytest-homeassistant-custom-component==0.13.366",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["packages/hunch/tests", "tests/unit"]
```

Run `uv lock`. If uv reports a conflict between `dev` and `ha` (both pin pytest/pytest-asyncio), add

```toml
[tool.uv]
package = false
conflicts = [[{ group = "dev" }, { group = "ha" }]]
```

and run `uv lock` again, then `uv sync --group ha` to verify HA installs (this takes a few minutes the first time).

Create `custom_components/__init__.py` (empty) and `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py` (empty).

- [ ] **Step 2: Failing HA test**

`tests/integration/conftest.py`:

```python
"""HA-harness fixtures. Run with: uv run --group ha pytest tests/integration"""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hunch.const import DOMAIN
from hunch import FakeDecisionClient
from hunch.questions import ChoiceA, ChoiceQ, NoulA, ScoreA, ScoreQ


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def scripted(round1: dict, round2: dict | None = None, reply: dict | None = None):
    """A FakeDecisionClient answering engine questions by id, and reply-judgment questions
    (state has a 'reply' key) from `reply`. Everything unlisted answers 'no'."""
    calls: list[tuple[dict, dict]] = []

    def script(state, qs):
        calls.append((state, dict(qs)))
        out = {}
        for qid, q in qs.items():
            if "reply" in state and reply and qid in reply:
                out[qid] = reply[qid]
            elif qid in round1:
                out[qid] = round1[qid]
            elif round2 and qid in round2:
                out[qid] = round2[qid]
            elif qid in ("verb_primary", "area_primary"):
                out[qid] = ChoiceA("several", 0.9, {})
            elif isinstance(q, ChoiceQ):
                out[qid] = ChoiceA("none" if "none" in q.options else q.options[0], 0.9, {})
            elif isinstance(q, ScoreQ):
                out[qid] = ScoreA(1.0, 0.9, {})
            else:
                out[qid] = NoulA(0.05)
        return out

    return FakeDecisionClient(script), calls


@pytest.fixture
async def setup_hunch(hass: HomeAssistant):
    """Set up homeassistant + conversation, then a Hunch entry with the given fake client.
    Usage: entry, calls = await setup_hunch(client, calls, options={...})"""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})

    async def _setup(client, calls, options=None):
        entry = MockConfigEntry(
            domain=DOMAIN, data={"api_key": "test-key"}, options=options or {}, title="Hunch"
        )
        entry.add_to_hass(hass)
        with patch("custom_components.hunch.build_client", return_value=client):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return entry, calls

    return _setup
```

`tests/integration/test_init.py`:

```python
from homeassistant.core import HomeAssistant

from tests.integration.conftest import scripted


async def test_entry_sets_up_a_conversation_entity(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    state = hass.states.get("conversation.hunch")
    assert state is not None
    assert entry.runtime_data.engine is not None
    assert entry.runtime_data.fallback_agent_id is None


async def test_unload_removes_the_entity(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("conversation.hunch") is None
```

- [ ] **Step 3: Run, expect failure**

Run: `uv run --group ha pytest tests/integration/test_init.py -v`
Expected: FAIL (integration `hunch` not found / import errors).

- [ ] **Step 4: Implement the skeleton**

`custom_components/hunch/manifest.json`:

```json
{
  "domain": "hunch",
  "name": "Hunch",
  "codeowners": ["@julianpufler"],
  "config_flow": true,
  "dependencies": ["conversation", "homeassistant"],
  "documentation": "https://github.com/julianpufler/hass-hunch",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/julianpufler/hass-hunch/issues",
  "requirements": ["hunch-engine==0.2.0"],
  "version": "0.1.0"
}
```

(Replace the GitHub owner with the repo's actual remote if it differs; `git remote -v`.)

`hacs.json`:

```json
{ "name": "Hunch", "homeassistant": "2026.8.0", "render_readme": true }
```

`custom_components/hunch/const.py`: the constants from the Interfaces block, plus

```python
THRESHOLD_FIELDS = (
    "verb_fire",
    "scope_fire",
    "place_override",
    "collective",
    "target_choice_conf",
    "auto_execute",
    "confirm_band",
    "flag",
    "specific_device",
    "collective_fallback",
    "no_match_clarify",
)
```

(Verify the names against `hunch.config.Thresholds` with `uv run python -c "import dataclasses, hunch.config as c; print([f.name for f in dataclasses.fields(c.Thresholds)])"` and use exactly that list.)

`custom_components/hunch/__init__.py`:

```python
"""Hunch: a Jev-backed fast path in front of your conversation agent."""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from hunch import DEFAULT_VOCABULARY, DecisionClient, Engine, EngineConfig, Thresholds
from hunch.client import TypeSafeDecisionClient

from .const import (
    CONF_API_KEY,
    DEFAULT_MODEL,
    DEFAULT_RESPONSE_LANGUAGE,
    DEFAULT_TIMEOUT_MS,
    OPT_DEVICE_ROUND,
    OPT_FALLBACK_AGENT,
    OPT_MAX_ROUNDS,
    OPT_MAX_SILENT_TARGETS,
    OPT_MODEL,
    OPT_RESPONSE_LANGUAGE,
    OPT_THRESHOLDS,
    OPT_TIMEOUT_MS,
    TRACE_BUFFER,
)
from .home_model import HomeModelBuilder
from .pending import PendingStore

PLATFORMS = [Platform.CONVERSATION]


@dataclass
class HunchRuntime:
    client: DecisionClient
    engine: Engine
    builder: HomeModelBuilder
    pending: PendingStore
    traces: deque[dict[str, Any]]
    fallback_agent_id: str | None
    response_language: str


type HunchConfigEntry = ConfigEntry[HunchRuntime]


def build_engine_config(options: Mapping[str, Any]) -> EngineConfig:
    defaults = Thresholds()
    raw = options.get(OPT_THRESHOLDS) or {}
    th = dataclasses.replace(
        defaults,
        **{
            k: float(v)
            for k, v in raw.items()
            if k in {f.name for f in dataclasses.fields(defaults)}
        },
    )
    device_round = bool(options.get(OPT_DEVICE_ROUND, False))
    max_rounds = int(options.get(OPT_MAX_ROUNDS, 2))
    if device_round and max_rounds < 3:
        max_rounds = 3
    return EngineConfig(
        model=options.get(OPT_MODEL, DEFAULT_MODEL),
        thresholds=th,
        max_rounds=max_rounds,
        max_silent_targets=int(options.get(OPT_MAX_SILENT_TARGETS, 20)),
        device_round=device_round,
    )


def build_client(entry: ConfigEntry) -> DecisionClient:
    """The real Jev client. Tests patch this to inject a FakeDecisionClient."""
    return TypeSafeDecisionClient(
        model=entry.options.get(OPT_MODEL, DEFAULT_MODEL),
        api_key=entry.data[CONF_API_KEY],
        timeout_ms=int(entry.options.get(OPT_TIMEOUT_MS, DEFAULT_TIMEOUT_MS)),
    )


async def async_setup_entry(hass: HomeAssistant, entry: HunchConfigEntry) -> bool:
    client = build_client(entry)
    builder = HomeModelBuilder(hass, DEFAULT_VOCABULARY)
    builder.async_start()
    entry.async_on_unload(builder.async_stop)
    entry.runtime_data = HunchRuntime(
        client=client,
        engine=Engine(client, DEFAULT_VOCABULARY, build_engine_config(entry.options)),
        builder=builder,
        pending=PendingStore(),
        traces=deque(maxlen=TRACE_BUFFER),
        fallback_agent_id=entry.options.get(OPT_FALLBACK_AGENT) or None,
        response_language=entry.options.get(OPT_RESPONSE_LANGUAGE, DEFAULT_RESPONSE_LANGUAGE),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HunchConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    aclose = getattr(entry.runtime_data.client, "aclose", None)
    if aclose is not None:
        await aclose()
    return ok
```

Check `EngineConfig`'s constructor field names with `uv run python -c "import dataclasses, hunch.config as c; print([f.name for f in dataclasses.fields(c.EngineConfig)])"` and adjust the keyword names to match exactly (the spec lists `model, thresholds, max_rounds, max_silent_targets, scope_cap, device_round, supports_clarification, max_prompt_chars, clarify_max_candidates`).

Minimal `custom_components/hunch/home_model.py` for this task (Task 4 fills it in):

```python
"""HomeModel from HA registries (filled in by Task 4)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback

from hunch import HomeModel, Vocabulary


class HomeModelBuilder:
    def __init__(self, hass: HomeAssistant, vocabulary: Vocabulary) -> None:
        self._hass = hass
        self._vocabulary = vocabulary

    @callback
    def async_start(self) -> None: ...

    @callback
    def async_stop(self) -> None: ...

    def build(self) -> HomeModel:
        return HomeModel(floors=(), areas=(), entities=(), scenes=())
```

Minimal `custom_components/hunch/pending.py` (Task 7 fills it in):

```python
"""Pending confirmation / clarification turns (filled in by Task 7)."""


class PendingStore:
    pass
```

Stub `custom_components/hunch/conversation.py` that escalates everything (Task 8 replaces the body):

```python
"""Hunch conversation entity."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HunchConversationEntity(entry)])


class HunchConversationEntity(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = None

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        return await conversation.async_converse(
            self.hass,
            user_input.text,
            user_input.conversation_id,
            user_input.context,
            language=user_input.language,
            agent_id=None,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
        )
```

With `_attr_has_entity_name = True` and `_attr_name = None` the entity id derives from the entry title → `conversation.hunch`. If the test finds a different entity id, set `_attr_name = "Hunch"` and `_attr_has_entity_name = False` instead. If `AddConfigEntryEntitiesCallback` does not exist in 2026.9, use `AddEntitiesCallback`.

- [ ] **Step 5: Run**

Run: `uv run --group ha pytest tests/integration/test_init.py -v`
Expected: 2 passed.

Run: `uv run ruff format custom_components tests && uv run ruff check custom_components tests && uv run pytest -q`
Expected: default suite unaffected (`tests/unit` empty is fine).

- [ ] **Step 6: Commit**

```bash
git add custom_components hacs.json pyproject.toml uv.lock tests
git commit -m "Integration skeleton: manifest, runtime, stub conversation entity, HA test harness

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Config flow (API key) with translations

**Files:**
- Create: `custom_components/hunch/config_flow.py`, `strings.json`, `translations/en.json`, `translations/de.json`
- Test: `tests/integration/test_config_flow.py`

**Interfaces:**
- Produces: `HunchConfigFlow(ConfigFlow, domain=DOMAIN)` with step `user` (`api_key`), `async_validate_api_key(hass, api_key, model) -> None` raising `InvalidAuth | UnknownModel | CannotConnect`. `async_get_options_flow` returns `HunchOptionsFlow` (Task 9 fills the form; this task registers an options flow with an empty `init` form so the button exists).

- [ ] **Step 1: Failing tests**

`tests/integration/test_config_flow.py`:

```python
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hunch.config_flow import CannotConnect, InvalidAuth, UnknownModel
from custom_components.hunch.const import DOMAIN


async def test_user_step_creates_the_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with (
        patch("custom_components.hunch.config_flow.async_validate_api_key", return_value=None),
        patch("custom_components.hunch.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": "k"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hunch"
    assert result["data"] == {"api_key": "k"}


async def test_errors_map_to_form_errors(hass: HomeAssistant):
    for exc, key in (
        (InvalidAuth, "invalid_auth"),
        (UnknownModel, "unknown_model"),
        (CannotConnect, "cannot_connect"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        with patch("custom_components.hunch.config_flow.async_validate_api_key", side_effect=exc):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"api_key": "k"}
            )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": key}


async def test_single_instance(hass: HomeAssistant, setup_hunch):
    from tests.integration.conftest import scripted

    client, calls = scripted({})
    await setup_hunch(client, calls)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run --group ha pytest tests/integration/test_config_flow.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`custom_components/hunch/config_flow.py`:

```python
"""Config flow: the API key. Everything else lives in the options flow."""

from __future__ import annotations

from typing import Any

import probatio  # noqa: F401  (HA re-exports voluptuous as `voluptuous`; use the import HA uses)
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from hunch.client import TypeSafeDecisionClient
from hunch.questions import NoulQ

from .const import CONF_API_KEY, DEFAULT_MODEL, DOMAIN


class InvalidAuth(HomeAssistantError):
    """The key was rejected."""


class UnknownModel(HomeAssistantError):
    """The pinned model does not exist."""


class CannotConnect(HomeAssistantError):
    """Network or timeout."""


async def async_validate_api_key(hass: HomeAssistant, api_key: str, model: str) -> None:
    """One cheap Jev call. Raises one of the three errors above."""
    from typesafe import TypeSafeError  # imported lazily: only the flow needs it

    client = TypeSafeDecisionClient(model=model, api_key=api_key, timeout_ms=5000)
    try:
        await client.ask({"probe": True}, {"probe": NoulQ("Is `probe` true?")})
    except TypeSafeError as err:
        status = getattr(err, "status", None)
        if status in (401, 403):
            raise InvalidAuth from err
        if status == 404 or "model" in str(err).lower():
            raise UnknownModel from err
        raise CannotConnect from err
    except Exception as err:  # noqa: BLE001 - timeouts and transport errors
        raise CannotConnect from err
    finally:
        await client.aclose()


STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


class HunchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await async_validate_api_key(self.hass, user_input[CONF_API_KEY], DEFAULT_MODEL)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except UnknownModel:
                errors["base"] = "unknown_model"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Hunch", data={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HunchOptionsFlow:
        return HunchOptionsFlow()


class HunchOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Task 9 fills the form; for now: save whatever comes in.
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))
```

Remove the `probatio` line (it is a reminder, not real code): HA uses `voluptuous as vol`. Check how the `typesafe` SDK exposes its error type: `uv run python -c "import typesafe; print([n for n in dir(typesafe) if 'Error' in n])"` and import accordingly (the engine's `client.py` already imports it — copy that import). The abort reason `single_instance_allowed` is what `_abort_if_unique_id_configured` produces for a configured unique id **only** when called with `reason=`; HA's default reason is `already_configured`. Use `self._abort_if_unique_id_configured()` and change the test's expected reason to `already_configured` if that is what HA returns — either is acceptable; match the framework.

`custom_components/hunch/strings.json`:

```json
{
  "config": {
    "step": { "user": { "title": "Connect Hunch to TypeSafe", "data": { "api_key": "TypeSafe API key" } } },
    "error": {
      "invalid_auth": "The API key was rejected.",
      "unknown_model": "The pinned Jev model does not exist.",
      "cannot_connect": "Could not reach the TypeSafe API."
    },
    "abort": { "already_configured": "Hunch is already set up." }
  },
  "options": {
    "step": { "init": { "title": "Hunch options" } }
  }
}
```

`translations/en.json` = same content. `translations/de.json`:

```json
{
  "config": {
    "step": { "user": { "title": "Hunch mit TypeSafe verbinden", "data": { "api_key": "TypeSafe-API-Schlüssel" } } },
    "error": {
      "invalid_auth": "Der API-Schlüssel wurde abgelehnt.",
      "unknown_model": "Das festgelegte Jev-Modell existiert nicht.",
      "cannot_connect": "Die TypeSafe-API ist nicht erreichbar."
    },
    "abort": { "already_configured": "Hunch ist bereits eingerichtet." }
  },
  "options": { "step": { "init": { "title": "Hunch-Optionen" } } }
}
```

- [ ] **Step 4: Run**

`uv run --group ha pytest tests/integration/test_config_flow.py -v` → 3 passed. Then ruff.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "Config flow: API key with a probe call; en/de strings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: HomeModelBuilder from registries

**Files:**
- Modify: `custom_components/hunch/home_model.py`
- Test: `tests/unit/test_home_model_snapshot.py`, `tests/integration/test_home_model.py`

**Interfaces:**
- Produces:
  ```python
  class HomeModelBuilder:
      def __init__(self, hass, vocabulary) -> None
      @callback def async_start(self) -> None          # subscribe registry + exposure listeners
      @callback def async_stop(self) -> None
      @callback def invalidate(self) -> None
      def snapshot(self) -> dict                        # export-shaped dict WITHOUT "states" (cached)
      def build(self) -> HomeModel                      # snapshot + live states → home_from_export
  def export_shape(floors, areas, devices, entities, exposed_ids) -> dict   # pure; registry entries → export dict
  ```
  The export shape is exactly what `tools/export_home.py` writes: `{"floors": [{floor_id,name,aliases}], "areas": [{area_id,name,aliases,floor_id}], "devices": [{id,name,name_by_user,area_id}], "entities": [{entity_id,name,original_name,aliases,area_id,device_id}], "exposed": [ids], "states": {id: {"state","friendly_name"}}}`.

- [ ] **Step 1: Pure test**

`tests/unit/test_home_model_snapshot.py`:

```python
from types import SimpleNamespace as NS

from custom_components.hunch.home_model import export_shape
from hunch import home_from_export


def test_export_shape_matches_the_exporter():
    floors = [NS(floor_id="ug", name="Untergeschoss", aliases={"unten"})]
    areas = [
        NS(id="kuche", name="Küche", aliases=set(), floor_id="ug"),
        NS(id="loose", name="Virtuell", aliases={"virtual"}, floor_id=None),
    ]
    devices = [NS(id="d1", name="Hue island", name_by_user="Kücheninsel", area_id="kuche")]
    entities = [
        NS(
            entity_id="light.kuche_kucheninsel",
            name=None,
            original_name="Kücheninsel",
            aliases=set(),
            area_id=None,
            device_id="d1",
        ),
        NS(
            entity_id="light.hidden",
            name="Hidden",
            original_name=None,
            aliases=set(),
            area_id="kuche",
            device_id=None,
        ),
        NS(
            entity_id="scene.abend",
            name="Abend",
            original_name=None,
            aliases=set(),
            area_id=None,
            device_id=None,
        ),
    ]
    shape = export_shape(
        floors, areas, devices, entities, {"light.kuche_kucheninsel", "scene.abend"}
    )
    assert shape["floors"] == [{"floor_id": "ug", "name": "Untergeschoss", "aliases": ["unten"]}]
    assert shape["areas"][0] == {
        "area_id": "kuche",
        "name": "Küche",
        "aliases": [],
        "floor_id": "ug",
    }
    assert shape["devices"] == [
        {"id": "d1", "name": "Hue island", "name_by_user": "Kücheninsel", "area_id": "kuche"}
    ]
    assert [e["entity_id"] for e in shape["entities"]] == [
        "light.kuche_kucheninsel",
        "scene.abend",
    ]  # exposed only
    assert shape["exposed"] == ["light.kuche_kucheninsel", "scene.abend"]
    shape["states"] = {"light.kuche_kucheninsel": {"state": "off", "friendly_name": "Kücheninsel"}}
    home = home_from_export(shape)
    e = home.entity_by_id("light.kuche_kucheninsel")
    assert e.area_id == "kuche" and e.device_name == "Kücheninsel" and e.state == "off"
    assert [s.entity_id for s in home.scenes] == ["scene.abend"]
    assert home.floors[0].aliases == ("unten",) and home.floors[0].area_ids == ("kuche",)
```

Note `tests/unit` imports `custom_components.hunch.home_model`, which imports `homeassistant.core`. To keep the default suite HA-free, `home_model.py` must import HA **inside** the class/methods, or `export_shape` must live in a module without HA imports. Do the latter: put `export_shape` in `custom_components/hunch/home_shape.py` (pure) and import it from `home_model.py`. Update the test import to `from custom_components.hunch.home_shape import export_shape`.

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/unit -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`custom_components/hunch/home_shape.py`:

```python
"""Registry entries → the export dict shape that hunch.loaders.home_from_export reads.

Pure: takes anything with the registry entries' attribute names (works with HA's entries and
with SimpleNamespace in tests). Only exposed entities are included.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def export_shape(
    floors: Iterable[Any],
    areas: Iterable[Any],
    devices: Iterable[Any],
    entities: Iterable[Any],
    exposed_ids: set[str],
) -> dict[str, Any]:
    exposed = [e.entity_id for e in entities if e.entity_id in exposed_ids]
    return {
        "floors": [
            {"floor_id": f.floor_id, "name": f.name, "aliases": sorted(f.aliases or ())}
            for f in floors
        ],
        "areas": [
            {
                "area_id": a.id,
                "name": a.name,
                "aliases": sorted(a.aliases or ()),
                "floor_id": a.floor_id,
            }
            for a in areas
        ],
        "devices": [
            {"id": d.id, "name": d.name, "name_by_user": d.name_by_user, "area_id": d.area_id}
            for d in devices
        ],
        "entities": [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "original_name": e.original_name,
                "aliases": sorted(e.aliases or ()),
                "area_id": e.area_id,
                "device_id": e.device_id,
            }
            for e in entities
            if e.entity_id in exposed_ids
        ],
        "exposed": exposed,
        "states": {},
    }
```

`custom_components/hunch/home_model.py`:

```python
"""HomeModel from HA registries: cached skeleton, live states stamped per request."""

from __future__ import annotations

from typing import Any

from homeassistant.components.homeassistant.exposed_entities import (
    async_get_assistant_settings,
    async_listen_entity_updates,
)
from homeassistant.const import EVENT_STATE_CHANGED  # noqa: F401  (not used: states are read live)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)

from hunch import HomeModel, Vocabulary, home_from_export

from .home_shape import export_shape

ASSISTANT = "conversation"


class HomeModelBuilder:
    def __init__(self, hass: HomeAssistant, vocabulary: Vocabulary) -> None:
        self._hass = hass
        self._vocabulary = vocabulary
        self._skeleton: dict[str, Any] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    @callback
    def async_start(self) -> None:
        bus = self._hass.bus
        for event in (
            ar.EVENT_AREA_REGISTRY_UPDATED,
            fr.EVENT_FLOOR_REGISTRY_UPDATED,
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
        ):
            self._unsubs.append(bus.async_listen(event, self._on_event))
        self._unsubs.append(async_listen_entity_updates(self._hass, ASSISTANT, self.invalidate))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _on_event(self, _event: Event) -> None:
        self.invalidate()

    @callback
    def invalidate(self) -> None:
        self._skeleton = None

    def snapshot(self) -> dict[str, Any]:
        if self._skeleton is None:
            settings = async_get_assistant_settings(self._hass, ASSISTANT)
            exposed = {eid for eid, s in settings.items() if s.get("should_expose")}
            self._skeleton = export_shape(
                fr.async_get(self._hass).async_list_floors(),
                ar.async_get(self._hass).async_list_areas(),
                dr.async_get(self._hass).devices.values(),
                er.async_get(self._hass).entities.values(),
                exposed,
            )
        return self._skeleton

    def build(self) -> HomeModel:
        shape = dict(self.snapshot())
        states: dict[str, dict[str, Any]] = {}
        for eid in shape["exposed"]:
            st = self._hass.states.get(eid)
            if st is not None:
                states[eid] = {"state": st.state, "friendly_name": st.name}
        shape["states"] = states
        return home_from_export(shape, self._vocabulary)
```

Remove the unused `EVENT_STATE_CHANGED` import. `async_get_assistant_settings` is `@callback`-style synchronous in current HA (it reads in-memory data); if the harness shows it as a coroutine, make `snapshot` async and await it (then `build` becomes `async def build`; update the callers in Task 8 accordingly). Check the event constant names exist: `uv run --group ha python -c "from homeassistant.helpers import area_registry as ar, floor_registry as fr, entity_registry as er, device_registry as dr; print(ar.EVENT_AREA_REGISTRY_UPDATED, fr.EVENT_FLOOR_REGISTRY_UPDATED, er.EVENT_ENTITY_REGISTRY_UPDATED, dr.EVENT_DEVICE_REGISTRY_UPDATED)"`.

- [ ] **Step 4: HA test**

`tests/integration/test_home_model.py`:

```python
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)
from homeassistant.setup import async_setup_component

from custom_components.hunch.home_model import HomeModelBuilder
from hunch import DEFAULT_VOCABULARY


async def _populate(hass: HomeAssistant):
    floor = fr.async_get(hass).async_create("Untergeschoss", aliases={"unten"})
    area = ar.async_get(hass).async_create("Küche", floor_id=floor.floor_id)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id="x", identifiers={("test", "d1")}, name="Hue island"
    )
    dr.async_get(hass).async_update_device(dev.id, area_id=area.id, name_by_user="Kücheninsel")
    reg = er.async_get(hass)
    e1 = reg.async_get_or_create(
        "light",
        "test",
        "1",
        suggested_object_id="kuche_kucheninsel",
        original_name="Kücheninsel",
        device_id=dev.id,
        config_entry=None,
    )
    e2 = reg.async_get_or_create(
        "light", "test", "2", suggested_object_id="hidden", original_name="Hidden"
    )
    hass.states.async_set(e1.entity_id, "off")
    hass.states.async_set(e2.entity_id, "on")
    return e1, e2


async def test_builder_uses_exposed_entities_and_live_state(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})
    e1, e2 = await _populate(hass)
    async_expose_entity(hass, "conversation", e1.entity_id, True)
    b = HomeModelBuilder(hass, DEFAULT_VOCABULARY)
    b.async_start()
    home = b.build()
    assert [e.entity_id for e in home.entities] == [e1.entity_id]
    ent = home.entities[0]
    assert ent.area_id is not None and ent.device_name == "Kücheninsel" and ent.state == "off"
    assert home.floors[0].aliases == ("unten",)
    hass.states.async_set(e1.entity_id, "on")
    assert b.build().entities[0].state == "on"  # states are never cached
    # exposure change invalidates the skeleton
    async_expose_entity(hass, "conversation", e2.entity_id, True)
    await hass.async_block_till_done()
    assert {e.entity_id for e in b.build().entities} == {e1.entity_id, e2.entity_id}
    b.async_stop()
```

If `async_get_or_create`'s `config_entry=None` keyword is rejected, drop it. If `async_expose_entity` requires the config entry of `homeassistant` to be loaded, the `async_setup_component(hass, "homeassistant", {})` line covers it.

- [ ] **Step 5: Run both** — `uv run pytest tests/unit -q` and `uv run --group ha pytest tests/integration/test_home_model.py -v` → pass. Ruff.

- [ ] **Step 6: Commit**

```bash
git add custom_components tests
git commit -m "HomeModelBuilder: registries -> export shape -> home_from_export; live states; invalidation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Executor — verb → service map, condition check, state readings

**Files:**
- Create: `custom_components/hunch/executor.py` (HA-bound), `custom_components/hunch/service_map.py` (pure)
- Test: `tests/unit/test_service_map.py`, `tests/integration/test_executor.py`

**Interfaces:**
- Produces (pure, `service_map.py`):
  ```python
  @dataclass(frozen=True) class ServiceCall: domain: str; service: str; data: dict[str, Any]   # data includes "entity_id": [...]
  def plan_calls(action: Action) -> list[ServiceCall]        # grouped per target domain; [] for query_state
  ```
  HA-bound (`executor.py`):
  ```python
  @dataclass(frozen=True) class TargetResult: entity_id: str; ok: bool; error: str | None = None
  @dataclass(frozen=True) class StateReading: entity_id: str; name: str; area_id: str | None; state: str | None; unit: str | None; device_class: str | None
  class Executor:
      def __init__(self, hass) -> None
      async def execute(self, actions: Sequence[Action], context: Context) -> list[TargetResult]
      def condition_holds(self, condition: Condition) -> bool | None    # None = subject has no state
      def read_states(self, entities: Sequence[Entity]) -> list[StateReading]
  ```

- [ ] **Step 1: Pure tests**

`tests/unit/test_service_map.py`:

```python
import pytest

from custom_components.hunch.service_map import plan_calls
from hunch import DEFAULT_VOCABULARY as V, Action, Entity


def _e(eid, domain=None):
    d = domain or eid.split(".")[0]
    return Entity(eid, d, eid, (), None, None, None, frozenset(), None)


def test_turn_off_groups_everything_into_one_homeassistant_call():
    a = Action(V.by_name("turn_off"), (_e("light.a"), _e("switch.b"), _e("fan.c")), {})
    calls = plan_calls(a)
    assert len(calls) == 1
    assert (calls[0].domain, calls[0].service) == ("homeassistant", "turn_off")
    assert calls[0].data == {"entity_id": ["light.a", "switch.b", "fan.c"]}


def test_params_are_converted():
    assert plan_calls(
        Action(V.by_name("set_brightness"), (_e("light.a"),), {"brightness_pct": 35.0})
    )[0].data == {"entity_id": ["light.a"], "brightness_pct": 35}
    assert plan_calls(Action(V.by_name("set_position"), (_e("cover.a"),), {"position": 85.0}))[
        0
    ].data == {"entity_id": ["cover.a"], "position": 85}
    vol = plan_calls(
        Action(V.by_name("set_volume"), (_e("media_player.a"),), {"volume_level": 20.0})
    )[0]
    assert vol.service == "volume_set" and vol.data["volume_level"] == pytest.approx(0.2)
    t = plan_calls(Action(V.by_name("set_temperature"), (_e("climate.a"),), {"temperature": 22.0}))[
        0
    ]
    assert (t.domain, t.service, t.data["temperature"]) == ("climate", "set_temperature", 22.0)


def test_activate_splits_scene_and_script_by_domain():
    calls = plan_calls(Action(V.by_name("activate"), (_e("scene.a"), _e("script.b")), {}))
    assert {(c.domain, c.service) for c in calls} == {("scene", "turn_on"), ("script", "turn_on")}


def test_every_vocabulary_verb_has_a_mapping():
    for verb in V.verbs:
        if verb.is_query:
            assert plan_calls(Action(verb, (_e("sensor.x"),), {})) == []
            continue
        domain = sorted(verb.domains)[0]
        calls = plan_calls(
            Action(verb, (_e(f"{domain}.x"),), {verb.param.name: 50.0} if verb.param else {})
        )
        assert calls, verb.name


def test_arm_means_arm_away_and_lock_uses_lock_services():
    assert (
        plan_calls(Action(V.by_name("arm"), (_e("alarm_control_panel.a"),), {}))[0].service
        == "alarm_arm_away"
    )
    assert plan_calls(Action(V.by_name("unlock"), (_e("lock.a"),), {}))[0].service == "unlock"
```

Adjust the `Entity(...)` positional construction to the real field order (`entity_id, domain, name, aliases, area_id, device_id, device_name, verbs, state`); use keywords if unsure.

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/unit/test_service_map.py -v` → FAIL.

- [ ] **Step 3: Implement `service_map.py`**

```python
"""Verb -> Home Assistant service. Pure; no HA imports."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from hunch import Action

HOMEASSISTANT = "homeassistant"


@dataclass(frozen=True)
class ServiceCall:
    domain: str
    service: str
    data: dict[str, Any]


def _int(name: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return lambda p: {name: int(round(float(p[name])))} if name in p else {}


def _float(name: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return lambda p: {name: float(p[name])} if name in p else {}


def _volume(p: Mapping[str, Any]) -> dict[str, Any]:
    return {"volume_level": float(p["volume_level"]) / 100.0} if "volume_level" in p else {}


_NONE: Callable[[Mapping[str, Any]], dict[str, Any]] = lambda p: {}  # noqa: E731

# verb -> (service domain or None = the target's own domain, service, data builder)
SERVICE_MAP: dict[str, tuple[str | None, str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    "turn_on": (HOMEASSISTANT, "turn_on", _NONE),
    "turn_off": (HOMEASSISTANT, "turn_off", _NONE),
    "set_brightness": ("light", "turn_on", _int("brightness_pct")),
    "open": ("cover", "open_cover", _NONE),
    "close": ("cover", "close_cover", _NONE),
    "set_position": ("cover", "set_cover_position", _int("position")),
    "lock": ("lock", "lock", _NONE),
    "unlock": ("lock", "unlock", _NONE),
    "set_temperature": ("climate", "set_temperature", _float("temperature")),
    "set_volume": ("media_player", "volume_set", _volume),
    "media_play": ("media_player", "media_play", _NONE),
    "media_pause": ("media_player", "media_pause", _NONE),
    "arm": ("alarm_control_panel", "alarm_arm_away", _NONE),  # the engine has no arming mode
    "disarm": ("alarm_control_panel", "alarm_disarm", _NONE),
    "activate": (None, "turn_on", _NONE),  # scene.turn_on / script.turn_on by target domain
}


def plan_calls(action: Action) -> list[ServiceCall]:
    if action.verb.is_query:
        return []
    domain, service, build = SERVICE_MAP[action.verb.name]
    extra = build(action.params)
    if domain is not None:
        return [
            ServiceCall(
                domain, service, {"entity_id": [e.entity_id for e in action.targets], **extra}
            )
        ]
    groups: dict[str, list[str]] = defaultdict(list)
    for e in action.targets:
        groups[e.domain].append(e.entity_id)
    return [ServiceCall(d, service, {"entity_id": ids, **extra}) for d, ids in groups.items()]
```

- [ ] **Step 4: Run** → pass.

- [ ] **Step 5: HA-bound executor + test**

`custom_components/hunch/executor.py`:

```python
"""Runs planned service calls with the caller's context; reads states for queries/conditions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound, Unauthorized

from hunch import Action, Condition, Entity

from .service_map import plan_calls


@dataclass(frozen=True)
class TargetResult:
    entity_id: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class StateReading:
    entity_id: str
    name: str
    area_id: str | None
    state: str | None
    unit: str | None
    device_class: str | None


class Executor:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def execute(self, actions: Sequence[Action], context: Context) -> list[TargetResult]:
        results: list[TargetResult] = []
        for action in actions:
            for call in plan_calls(action):
                ids: list[str] = call.data["entity_id"]
                try:
                    await self._hass.services.async_call(
                        call.domain, call.service, call.data, blocking=True, context=context
                    )
                except (Unauthorized, ServiceNotFound, HomeAssistantError) as err:
                    results.extend(TargetResult(i, False, type(err).__name__) for i in ids)
                else:
                    results.extend(TargetResult(i, True) for i in ids)
        return results

    def condition_holds(self, condition: Condition) -> bool | None:
        st = self._hass.states.get(condition.subject.entity_id)
        if st is None:
            return None
        return st.state == condition.expected_state

    def read_states(self, entities: Sequence[Entity]) -> list[StateReading]:
        out = []
        for e in entities:
            st = self._hass.states.get(e.entity_id)
            attrs = st.attributes if st is not None else {}
            out.append(
                StateReading(
                    e.entity_id,
                    e.name,
                    e.area_id,
                    st.state if st else None,
                    attrs.get("unit_of_measurement"),
                    attrs.get("device_class"),
                )
            )
        return out
```

`tests/integration/test_executor.py`:

```python
from homeassistant.core import Context, HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hunch.executor import Executor
from hunch import DEFAULT_VOCABULARY as V, Action, Condition, Entity


def _e(eid, name="x", area=None):
    return Entity(
        entity_id=eid,
        domain=eid.split(".")[0],
        name=name,
        aliases=(),
        area_id=area,
        device_id=None,
        device_name=None,
        verbs=frozenset(),
        state=None,
    )


async def test_execute_calls_services_with_context_and_reports_per_target(hass: HomeAssistant):
    calls = async_mock_service(hass, "homeassistant", "turn_off")
    ctx = Context(user_id="u1")
    res = await Executor(hass).execute(
        [Action(V.by_name("turn_off"), (_e("light.a"), _e("switch.b")), {})], ctx
    )
    assert len(calls) == 1 and calls[0].data["entity_id"] == ["light.a", "switch.b"]
    assert calls[0].context is ctx
    assert all(r.ok for r in res) and [r.entity_id for r in res] == ["light.a", "switch.b"]


async def test_missing_service_marks_targets_failed_and_continues(hass: HomeAssistant):
    ok_calls = async_mock_service(hass, "cover", "close_cover")
    res = await Executor(hass).execute(
        [
            Action(V.by_name("lock"), (_e("lock.front"),), {}),
            Action(V.by_name("close"), (_e("cover.a"),), {}),
        ],
        Context(),
    )
    assert [r.ok for r in res] == [False, True] and res[0].error == "ServiceNotFound"
    assert len(ok_calls) == 1


async def test_condition_and_state_readings(hass: HomeAssistant):
    hass.states.async_set("binary_sensor.door", "on", {"device_class": "door"})
    hass.states.async_set("sensor.temp", "23.6", {"unit_of_measurement": "°C"})
    ex = Executor(hass)
    assert ex.condition_holds(Condition(_e("binary_sensor.door"), "on")) is True
    assert ex.condition_holds(Condition(_e("binary_sensor.door"), "off")) is False
    assert ex.condition_holds(Condition(_e("binary_sensor.missing"), "on")) is None
    r = ex.read_states([_e("sensor.temp", "Temperatur", "wohnzimmer")])[0]
    assert (r.state, r.unit, r.area_id) == ("23.6", "°C", "wohnzimmer")
```

- [ ] **Step 6: Run** — `uv run --group ha pytest tests/integration/test_executor.py -v` → pass; ruff.

- [ ] **Step 7: Commit**

```bash
git add custom_components tests
git commit -m "Executor: verb->service map with exact entity ids, per-target results, condition check

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Responder — every sentence, en and de

**Files:**
- Create: `custom_components/hunch/responder.py` (pure, no HA imports)
- Test: `tests/unit/test_responder.py`

**Interfaces:**
- Produces:
  ```python
  OUTCOMES = ("action_done","query_answer","confirm","clarify","cancelled","condition_not_met","expired","fallback_unavailable","execution_failed")
  def resolve_language(option: str, request_language: str | None) -> str    # "de" | "en"
  def verb_phrase(verb_name: str, language: str, *, done: bool) -> str      # "turned off" / "ausgeschaltet"; infinitive when done=False
  def describe_targets(targets: Sequence[Entity], area_names: Mapping[str, str], language: str) -> str   # ≤3 names "(room)" else "13 devices in Küche, Wohnzimmer"
  def describe_state(reading, language: str) -> str                          # "offen", "23.6 °C"
  def render(outcome: str, language: str, **slots) -> str
  def pending_context(question: str, description: str) -> str                # English, for extra_system_prompt
  ```
  Slots per outcome — `action_done(phrase, targets)`, `query_answer(lines: list[str])`, `confirm(phrase, targets, reason, condition: str | None)`, `clarify(options: list[str])`, `cancelled()`, `condition_not_met(subject, expected)`, `expired()`, `fallback_unavailable()`, `execution_failed(failed: list[str])`.

- [ ] **Step 1: Failing tests**

`tests/unit/test_responder.py`:

```python
import pytest

from custom_components.hunch.responder import (
    OUTCOMES,
    describe_state,
    describe_targets,
    pending_context,
    render,
    resolve_language,
    verb_phrase,
)
from hunch import DEFAULT_VOCABULARY as V, Entity


def _e(eid, name, area):
    return Entity(
        entity_id=eid,
        domain=eid.split(".")[0],
        name=name,
        aliases=(),
        area_id=area,
        device_id=None,
        device_name=None,
        verbs=frozenset(),
        state=None,
    )


AREAS = {"kuche": "Küche", "wohnzimmer": "Wohnzimmer"}


def test_language_resolution():
    assert resolve_language("auto", "de-AT") == "de"
    assert resolve_language("auto", "en-GB") == "en"
    assert resolve_language("auto", None) == "en"
    assert resolve_language("de", "en") == "de"
    assert resolve_language("auto", "fr") == "en"


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_outcome_renders_in_both_languages(language):
    slots = {
        "action_done": dict(
            phrase=verb_phrase("turn_off", language, done=True), targets="13 lights"
        ),
        "query_answer": dict(lines=["Temperatur (Wohnzimmer): 23.6 °C"]),
        "confirm": dict(
            phrase=verb_phrase("turn_off", language, done=False),
            targets="26 lights",
            reason="blast_radius",
            condition=None,
        ),
        "clarify": dict(options=["Tür (Galerie)", "Tür (Schlafzimmer)"]),
        "cancelled": {},
        "condition_not_met": dict(subject="Dachterrassentür", expected="open"),
        "expired": {},
        "fallback_unavailable": {},
        "execution_failed": dict(failed=["Spots (Küche)"]),
    }
    for outcome in OUTCOMES:
        text = render(outcome, language, **slots[outcome])
        assert text and "{" not in text, (outcome, text)


def test_every_verb_has_phrases():
    for verb in V.verbs:
        for lang in ("en", "de"):
            assert verb_phrase(verb.name, lang, done=True)
            assert verb_phrase(verb.name, lang, done=False)


def test_targets_short_and_long():
    e = [_e("light.a", "Spots", "kuche"), _e("light.b", "Kücheninsel", "kuche")]
    assert describe_targets(e, AREAS, "de") == "Spots (Küche), Kücheninsel (Küche)"
    many = [_e(f"light.{i}", f"L{i}", "kuche" if i % 2 else "wohnzimmer") for i in range(5)]
    assert describe_targets(many, AREAS, "en") == "5 devices in Küche, Wohnzimmer"


def test_state_words():
    from custom_components.hunch.executor import (
        StateReading,
    )  # only the dataclass; the test env has HA? -> see note
```

Replace the last test with a version that does not import `executor.py` (which imports HA): define `StateReading` in `responder.py`'s expectations via a tiny protocol — `describe_state` takes anything with `.domain`-derivable `entity_id`, `.state`, `.unit`, `.device_class`. Test with `SimpleNamespace(entity_id="binary_sensor.d", state="on", unit=None, device_class="door")` → `"offen"` (de) / `"open"` (en); `SimpleNamespace(entity_id="sensor.t", state="23.6", unit="°C", device_class=None)` → `"23.6 °C"`; cover `closed` → `"geschlossen"`; unknown domain/state → the raw state.

Also test `pending_context("Turn off 13 lights?", "turn off 13 lights on the Untergeschoss")` contains both strings.

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/unit/test_responder.py -v` → FAIL.

- [ ] **Step 3: Implement `responder.py`**

```python
"""Everything Hunch says. Pure tables, en + de, en as fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hunch import Entity

OUTCOMES = (
    "action_done",
    "query_answer",
    "confirm",
    "clarify",
    "cancelled",
    "condition_not_met",
    "expired",
    "fallback_unavailable",
    "execution_failed",
)

# verb -> (past participle / done form, infinitive / question form)
VERB_PHRASES: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "turn_on": ("turned on", "turn on"),
        "turn_off": ("turned off", "turn off"),
        "set_brightness": ("set the brightness of", "set the brightness of"),
        "open": ("opened", "open"),
        "close": ("closed", "close"),
        "set_position": ("set the position of", "set the position of"),
        "lock": ("locked", "lock"),
        "unlock": ("unlocked", "unlock"),
        "set_temperature": ("set the temperature of", "set the temperature of"),
        "set_volume": ("set the volume of", "set the volume of"),
        "media_play": ("resumed", "resume"),
        "media_pause": ("paused", "pause"),
        "arm": ("armed (away)", "arm (away mode)"),
        "disarm": ("disarmed", "disarm"),
        "activate": ("activated", "activate"),
        "query_state": ("read", "read"),
    },
    "de": {
        "turn_on": ("eingeschaltet", "einschalten"),
        "turn_off": ("ausgeschaltet", "ausschalten"),
        "set_brightness": ("Helligkeit gesetzt für", "Helligkeit setzen für"),
        "open": ("geöffnet", "öffnen"),
        "close": ("geschlossen", "schließen"),
        "set_position": ("Position gesetzt für", "Position setzen für"),
        "lock": ("abgesperrt", "absperren"),
        "unlock": ("aufgesperrt", "aufsperren"),
        "set_temperature": ("Temperatur gesetzt für", "Temperatur setzen für"),
        "set_volume": ("Lautstärke gesetzt für", "Lautstärke setzen für"),
        "media_play": ("fortgesetzt", "fortsetzen"),
        "media_pause": ("pausiert", "pausieren"),
        "arm": ("scharfgeschaltet (abwesend)", "scharfschalten (Modus abwesend)"),
        "disarm": ("unscharf geschaltet", "unscharf schalten"),
        "activate": ("aktiviert", "aktivieren"),
        "query_state": ("abgelesen", "ablesen"),
    },
}

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "action_done": "Done: {phrase} {targets}.",
        "query_answer": "{lines}",
        "confirm": "{phrase} {targets}{condition}? {why}",
        "clarify": "Which one: {options}?",
        "cancelled": "Okay, nothing changed.",
        "condition_not_met": "{subject} is not {expected}, so I left everything as it is.",
        "expired": "That question has expired; I'm treating this as a new request.",
        "fallback_unavailable": "I can't do that myself, and no other assistant is available.",
        "execution_failed": "Done, except: {failed}.",
    },
    "de": {
        "action_done": "Erledigt: {targets} {phrase}.",
        "query_answer": "{lines}",
        "confirm": "{targets} {phrase}{condition}? {why}",
        "clarify": "Welches: {options}?",
        "cancelled": "Okay, nichts geändert.",
        "condition_not_met": "{subject} ist nicht {expected}, darum habe ich nichts geändert.",
        "expired": "Diese Frage ist abgelaufen; ich behandle das als neue Anfrage.",
        "fallback_unavailable": "Das kann ich selbst nicht, und kein anderer Assistent ist verfügbar.",
        "execution_failed": "Erledigt, außer: {failed}.",
    },
}

REASONS = {
    "en": {
        "blast_radius": "That is a lot at once.",
        "risk:confirm": "This needs a confirmation.",
        "confidence": "I'm not completely sure that's what you meant.",
        "": "",
    },
    "de": {
        "blast_radius": "Das ist viel auf einmal.",
        "risk:confirm": "Das braucht eine Bestätigung.",
        "confidence": "Ich bin nicht ganz sicher, ob du das meinst.",
        "": "",
    },
}

STATE_WORDS: dict[str, dict[tuple[str, str], str]] = {
    "en": {
        ("binary_sensor", "on"): "open / active",
        ("binary_sensor", "off"): "closed / clear",
        ("cover", "open"): "open",
        ("cover", "closed"): "closed",
        ("lock", "locked"): "locked",
        ("lock", "unlocked"): "unlocked",
        ("light", "on"): "on",
        ("light", "off"): "off",
        ("switch", "on"): "on",
        ("switch", "off"): "off",
    },
    "de": {
        ("binary_sensor", "on"): "offen / aktiv",
        ("binary_sensor", "off"): "geschlossen / inaktiv",
        ("cover", "open"): "offen",
        ("cover", "closed"): "geschlossen",
        ("lock", "locked"): "abgesperrt",
        ("lock", "unlocked"): "aufgesperrt",
        ("light", "on"): "an",
        ("light", "off"): "aus",
        ("switch", "on"): "an",
        ("switch", "off"): "aus",
    },
}
DOOR_WORDS = {"en": {"on": "open", "off": "closed"}, "de": {"on": "offen", "off": "geschlossen"}}
DOOR_CLASSES = {"door", "window", "garage_door", "opening"}
CONDITION_WORD = {"en": " if {subject} is {state}", "de": ", wenn {subject} {state} ist"}
MANY = {"en": "{n} devices in {places}", "de": "{n} Geräte in {places}"}


def resolve_language(option: str, request_language: str | None) -> str:
    code = option if option != "auto" else (request_language or "en")
    return "de" if code.lower().startswith("de") else "en"


def verb_phrase(verb_name: str, language: str, *, done: bool) -> str:
    table = VERB_PHRASES.get(language, VERB_PHRASES["en"])
    pair = table.get(verb_name) or VERB_PHRASES["en"][verb_name]
    return pair[0] if done else pair[1]


def describe_targets(
    targets: Sequence[Entity], area_names: Mapping[str, str], language: str
) -> str:
    if len(targets) <= 3:
        return ", ".join(
            f"{e.name} ({area_names[e.area_id]})" if e.area_id in area_names else e.name
            for e in targets
        )
    places = sorted({area_names[e.area_id] for e in targets if e.area_id in area_names})
    return MANY.get(language, MANY["en"]).format(n=len(targets), places=", ".join(places) or "—")


def describe_state(reading: Any, language: str) -> str:
    domain = reading.entity_id.split(".", 1)[0]
    state = reading.state if reading.state is not None else "?"
    if (
        domain == "binary_sensor"
        and reading.device_class in DOOR_CLASSES
        and state in DOOR_WORDS["en"]
    ):
        return DOOR_WORDS.get(language, DOOR_WORDS["en"])[state]
    words = STATE_WORDS.get(language, STATE_WORDS["en"])
    if (domain, state) in words:
        return words[(domain, state)]
    if reading.unit:
        value = state.replace(".", ",") if language == "de" else state
        return f"{value} {reading.unit}"
    return state


def render(outcome: str, language: str, **slots: Any) -> str:
    lang = language if language in TEMPLATES else "en"
    tpl = TEMPLATES[lang][outcome]
    if outcome == "query_answer":
        return "\n".join(slots["lines"])
    if outcome == "clarify":
        return tpl.format(options=", ".join(slots["options"]))
    if outcome == "execution_failed":
        return tpl.format(failed=", ".join(slots["failed"]))
    if outcome == "confirm":
        cond = slots.get("condition")
        return tpl.format(
            phrase=slots["phrase"],
            targets=slots["targets"],
            condition=cond or "",
            why=REASONS[lang].get(slots.get("reason", ""), ""),
        ).strip()
    return tpl.format(**slots)


def condition_clause(subject: str, state: str, language: str) -> str:
    return CONDITION_WORD.get(language, CONDITION_WORD["en"]).format(subject=subject, state=state)


def pending_context(question: str, description: str) -> str:
    return (
        "Context from the Hunch assistant: it proposed to " + description + " and asked the user: "
        f'"{question}". The user did not simply confirm or decline; their reply follows. '
        "Handle the reply as a modification or a new request about that proposal."
    )
```

Add `condition_clause` to the interface list and test it renders in both languages.

- [ ] **Step 4: Run** → pass; ruff.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "Responder: en/de sentences for every outcome, verb phrases, state words

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Pending turns

**Files:**
- Modify: `custom_components/hunch/pending.py`
- Test: `tests/unit/test_pending.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True) class PendingConfirm: actions: tuple[Action, ...]; condition: Condition | None; question: str; created: float
  @dataclass(frozen=True) class PendingClarify: verb: Verb; params: Mapping[str, float | str]; candidates: tuple[Entity, ...]; labels: tuple[str, ...]; question: str; created: float
  class PendingStore:
      def __init__(self, ttl_seconds: float = 120.0, clock: Callable[[], float] = time.monotonic)
      def put(self, conversation_id: str, turn) -> None
      def take(self, conversation_id: str) -> PendingConfirm | PendingClarify | None   # removes; None if missing or expired
  ```
  Callers construct turns with `created=store.now()`.

- [ ] **Step 1: Failing test**

`tests/unit/test_pending.py`:

```python
from custom_components.hunch.pending import PendingConfirm, PendingStore


def test_take_is_single_use_and_expires():
    t = [100.0]
    store = PendingStore(ttl_seconds=120, clock=lambda: t[0])
    turn = PendingConfirm(actions=(), condition=None, question="q", created=store.now())
    store.put("c1", turn)
    assert store.take("c1") is turn
    assert store.take("c1") is None
    store.put("c1", turn)
    t[0] = 221.0
    assert store.take("c1") is None
    store.put("c1", turn)
    store.put("c1", PendingConfirm(actions=(), condition=None, question="q2", created=store.now()))
    assert store.take("c1").question == "q2"  # a new request replaces the old turn
```

- [ ] **Step 2: Run, expect failure**, **Step 3: Implement**

```python
"""One pending confirmation or clarification per conversation id. In memory, 120 s, single use."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hunch import Action, Condition, Entity
from hunch.vocabulary import Verb


@dataclass(frozen=True)
class PendingConfirm:
    actions: tuple[Action, ...]
    condition: Condition | None
    question: str
    created: float


@dataclass(frozen=True)
class PendingClarify:
    verb: Verb
    params: Mapping[str, float | str]
    candidates: tuple[Entity, ...]
    labels: tuple[str, ...]  # offered to the user and to Jev, parallel to candidates
    question: str
    created: float


class PendingStore:
    def __init__(
        self, ttl_seconds: float = 120.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._turns: dict[str, PendingConfirm | PendingClarify] = {}

    def now(self) -> float:
        return self._clock()

    def put(self, conversation_id: str, turn: PendingConfirm | PendingClarify) -> None:
        self._turns[conversation_id] = turn

    def take(self, conversation_id: str) -> PendingConfirm | PendingClarify | None:
        turn = self._turns.pop(conversation_id, None)
        if turn is None or self._clock() - turn.created > self._ttl:
            return None
        return turn
```

- [ ] **Step 4: Run** → pass. **Step 5: Commit** `"Pending turns: single-use, 120 s store"` with the trailer.

---

### Task 8: Conversation entity — the full request flow

**Files:**
- Modify: `custom_components/hunch/conversation.py`
- Test: `tests/integration/test_conversation.py`

**Interfaces:**
- Consumes: `HunchRuntime` (Task 2), `HomeModelBuilder.build()` (Task 4), `Executor` (Task 5), `responder.*` (Task 6), `PendingStore/PendingConfirm/PendingClarify` (Task 7), engine results incl. `NeedsClarification.verb/params` (Task 1).
- Produces: `HunchConversationEntity._async_handle_message` implementing spec §7. Reply-judgment questions:
  ```python
  CONFIRM_QID = "reply_confirm"
  CLARIFY_QID = "reply_pick"
  ```

- [ ] **Step 1: Failing tests**

`tests/integration/test_conversation.py` (helpers first):

```python
from unittest.mock import AsyncMock, patch

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from hunch.questions import ChoiceA, NoulA
from hunch.round2 import NO_MATCH
from tests.integration.conftest import scripted

AGENT = "conversation.hunch"


async def _home(hass: HomeAssistant):
    """Küche with two lights and a window contact; Galerie with a door contact."""
    areas = ar.async_get(hass)
    kuche = areas.async_create("Küche")
    galerie = areas.async_create("Galerie")
    reg = er.async_get(hass)
    made = []
    for domain, uid, obj, name, area, state, attrs in (
        ("light", "1", "kuche_spots", "Spots", kuche.id, "on", {}),
        ("light", "2", "kuche_kucheninsel", "Kücheninsel", kuche.id, "off", {}),
        (
            "binary_sensor",
            "3",
            "kuche_fenster",
            "Fenster",
            kuche.id,
            "off",
            {"device_class": "window"},
        ),
        ("binary_sensor", "4", "galerie_tur", "Tür", galerie.id, "on", {"device_class": "door"}),
    ):
        e = reg.async_get_or_create(
            domain, "test", uid, suggested_object_id=obj, original_name=name
        )
        reg.async_update_entity(e.entity_id, area_id=area)
        hass.states.async_set(e.entity_id, state, attrs)
        async_expose_entity(hass, "conversation", e.entity_id, True)
        made.append(e.entity_id)
    return made


async def _say(hass, text, conversation_id=None, language="de"):
    return await conversation.async_converse(
        hass, text, conversation_id, Context(user_id="u"), language=language, agent_id=AGENT
    )


R1_TURN_OFF_KITCHEN = {
    "verb:turn_off": NoulA(0.95),
    "domain:light": NoulA(0.95),
    "area:kuche": NoulA(0.99),
    "flag:collective": NoulA(0.9),
    "verb_primary": ChoiceA("turn_off", 0.95, {}),
    "area_primary": ChoiceA("Küche", 0.99, {}),
}


async def test_resolved_executes_and_answers_in_german(hass: HomeAssistant, setup_hunch):
    ids = await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    result = await _say(hass, "Licht in der Küche aus")
    assert len(svc) == 1 and sorted(svc[0].data["entity_id"]) == sorted(ids[:2])
    assert svc[0].context.user_id == "u"
    speech = result.response.speech["plain"]["speech"]
    assert speech.startswith("Erledigt") and "ausgeschaltet" in speech
    assert result.continue_conversation is False


async def test_escalation_passes_the_unchanged_text_to_the_fallback(
    hass: HomeAssistant, setup_hunch
):
    await _home(hass)
    client, calls = scripted(
        {"flag:has_timing": NoulA(0.95), "verb:turn_off": NoulA(0.9), "domain:light": NoulA(0.9)}
    )
    await setup_hunch(client, calls, options={"fallback_agent": "conversation.other"})
    fake = AsyncMock(
        return_value=conversation.ConversationResult(
            response=__import__("homeassistant.helpers.intent", fromlist=["x"]).IntentResponse(
                language="de"
            ),
            conversation_id="c9",
        )
    )
    with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
        result = await _say(hass, "Licht in 10 Minuten aus", conversation_id="c9")
    assert fake.await_count == 1
    kwargs = fake.await_args.kwargs
    assert fake.await_args.args[1] == "Licht in 10 Minuten aus" and fake.await_args.args[2] == "c9"
    assert kwargs["agent_id"] == "conversation.other" and kwargs.get("extra_system_prompt") is None
    assert result.conversation_id == "c9"


async def test_confirmation_yes_executes_no_cancels_other_escalates_with_context(
    hass: HomeAssistant, setup_hunch
):
    ids = await _home(hass)
    # 2 lights but max_silent_targets=1 -> blast radius confirmation
    for reply, expect_calls, expect_escalate in (
        (ChoiceA("affirmative", 0.95, {}), 1, False),
        (ChoiceA("negative", 0.95, {}), 0, False),
        (ChoiceA("other", 0.6, {}), 0, True),
    ):
        client, calls = scripted(R1_TURN_OFF_KITCHEN, reply={"reply_confirm": reply})
        entry, _ = await setup_hunch(client, calls, options={"max_silent_targets": 1})
        svc = async_mock_service(hass, "homeassistant", "turn_off")
        first = await _say(hass, "Licht in der Küche aus", conversation_id="c1")
        assert (
            first.continue_conversation is True and "?" in first.response.speech["plain"]["speech"]
        )
        fake = AsyncMock(return_value=first)
        with patch("custom_components.hunch.conversation.conversation.async_converse", fake):
            second = await _say(hass, "hm, eigentlich nur die Spots", conversation_id="c1")
        assert len(svc) == expect_calls
        if expect_escalate:
            assert fake.await_count == 1
            assert "proposed" in fake.await_args.kwargs["extra_system_prompt"]
        else:
            assert fake.await_count == 0
            assert second.continue_conversation is False
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


async def test_clarification_pick_executes_that_device(hass: HomeAssistant, setup_hunch):
    ids = await _home(hass)
    r1 = {
        "verb:turn_on": NoulA(0.95),
        "domain:light": NoulA(0.95),
        "area:kuche": NoulA(0.99),
        "flag:names_specific": NoulA(0.9),
        "verb_primary": ChoiceA("turn_on", 0.95, {}),
        "area_primary": ChoiceA("Küche", 0.99, {}),
    }
    r2 = {
        "target:turn_on": ChoiceA("Spots", 0.45, {"Spots": 0.45, "Kücheninsel": 0.4}),
        "all_of:turn_on": NoulA(0.1),
    }
    client, calls = scripted(r1, r2, reply={"reply_pick": ChoiceA("Kücheninsel (Küche)", 0.9, {})})
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_on")
    first = await _say(hass, "Lampe in der Küche an", conversation_id="c2")
    assert (
        first.continue_conversation is True
        and "Kücheninsel (Küche)" in first.response.speech["plain"]["speech"]
    )
    await _say(hass, "die Insel", conversation_id="c2")
    assert len(svc) == 1 and svc[0].data["entity_id"] == ["light.kuche_kucheninsel"]


async def test_condition_not_met_executes_nothing(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    r1 = {
        **R1_TURN_OFF_KITCHEN,
        "flag:has_condition": NoulA(0.95),
        "condition_domain": ChoiceA("binary_sensor", 0.95, {}),
    }
    r2 = {
        "cond_subject": ChoiceA("Fenster (Küche)", 0.95, {}),
        "cond_state": ChoiceA("on", 0.9, {}),
    }
    client, calls = scripted(r1, r2)
    await setup_hunch(client, calls)
    svc = async_mock_service(hass, "homeassistant", "turn_off")
    result = await _say(hass, "Licht in der Küche aus wenn das Fenster offen ist")
    assert len(svc) == 0
    assert "nicht" in result.response.speech["plain"]["speech"]


async def test_query_answers_with_state_words(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    r1 = {
        "verb:query_state": NoulA(0.95),
        "domain:binary_sensor": NoulA(0.95),
        "area:galerie": NoulA(0.99),
        "flag:names_specific": NoulA(0.9),
        "verb_primary": ChoiceA("query_state", 0.95, {}),
        "area_primary": ChoiceA("Galerie", 0.99, {}),
    }
    r2 = {"target:query_state": ChoiceA("Tür", 0.95, {})}
    client, calls = scripted(r1, r2)
    await setup_hunch(client, calls)
    result = await _say(hass, "Ist die Tür in der Galerie offen?")
    assert result.response.speech["plain"]["speech"] == "Tür (Galerie): offen"


async def test_trace_is_attached_to_the_chat_log(hass: HomeAssistant, setup_hunch):
    await _home(hass)
    client, calls = scripted(R1_TURN_OFF_KITCHEN)
    entry, _ = await setup_hunch(client, calls)
    async_mock_service(hass, "homeassistant", "turn_off")
    await _say(hass, "Licht in der Küche aus")
    assert len(entry.runtime_data.traces) == 1
    assert entry.runtime_data.traces[0]["outcome"] == "Resolved"
    assert "entries" in entry.runtime_data.traces[0]["trace"]
```

Target labels in Round 2 come from the engine's `target_options` (device-level labels; duplicates area-qualified) — if the engine offers `"Spots"`/`"Kücheninsel"` without room suffix, the clarify labels Hunch offers to the user and Jev are built by Hunch as `"{name} ({room})"`; the test above assumes that. If the condition subject option label differs from `"Fenster (Küche)"`, print `calls[1][1]["cond_subject"].options` once and adjust.

- [ ] **Step 2: Run, expect failure** — `uv run --group ha pytest tests/integration/test_conversation.py -v` → FAIL (stub escalates everything).

- [ ] **Step 3: Implement `conversation.py`**

```python
"""Hunch conversation entity: decide, execute, ask, or hand off."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from hunch import (
    Action,
    Condition,
    DecisionBackendError,
    Entity,
    Escalate,
    HomeModel,
    NeedsClarification,
    NeedsConfirmation,
    Resolved,
    Trace,
)
from hunch.questions import ChoiceQ
from hunch.round2 import NO_MATCH
from hunch.vocabulary import Risk

from . import HunchConfigEntry
from .executor import Executor
from .pending import PendingClarify, PendingConfirm
from .responder import (
    condition_clause,
    describe_state,
    describe_targets,
    pending_context,
    render,
    resolve_language,
    verb_phrase,
)

_LOGGER = logging.getLogger(__name__)
CONFIRM_QID = "reply_confirm"
CLARIFY_QID = "reply_pick"
REPLY_CONF = 0.7


async def async_setup_entry(
    hass: HomeAssistant, entry: HunchConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HunchConversationEntity(entry)])


def _speech(language: str, text: str, *, error: bool = False) -> intent.IntentResponse:
    resp = intent.IntentResponse(language=language)
    if error:
        resp.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, text)
    else:
        resp.async_set_speech(text)
    return resp


class HunchConversationEntity(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: HunchConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    # ---- helpers -------------------------------------------------------------------------

    @property
    def _rt(self):
        return self._entry.runtime_data

    def _lang(self, user_input: conversation.ConversationInput) -> str:
        return resolve_language(self._rt.response_language, user_input.language)

    def _area_names(self, home: HomeModel) -> dict[str, str]:
        return {a.area_id: a.name for a in home.areas}

    def _label(self, e: Entity, areas: Mapping[str, str]) -> str:
        return f"{e.name} ({areas[e.area_id]})" if e.area_id in areas else e.name

    def _result(
        self,
        user_input,
        chat_log,
        text: str,
        trace: Trace | None,
        outcome: str,
        *,
        cont: bool = False,
        error: bool = False,
    ) -> conversation.ConversationResult:
        if trace is not None:
            payload = trace.to_dict()
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=self.entity_id, content=text, native=payload)
            )
            self._rt.traces.append(
                {"prompt": user_input.text, "outcome": outcome, "trace": payload}
            )
        return conversation.ConversationResult(
            response=_speech(user_input.language, text, error=error),
            conversation_id=chat_log.conversation_id,
            continue_conversation=cont,
        )

    async def _escalate(
        self,
        user_input,
        chat_log,
        trace: Trace | None,
        outcome: str,
        extra_system_prompt: str | None = None,
    ) -> conversation.ConversationResult:
        agent_id = self._rt.fallback_agent_id
        if trace is not None:
            self._rt.traces.append(
                {"prompt": user_input.text, "outcome": outcome, "trace": trace.to_dict()}
            )
        if agent_id == self.entity_id:
            return self._result(
                user_input,
                chat_log,
                render("fallback_unavailable", self._lang(user_input)),
                None,
                outcome,
                error=True,
            )
        try:
            return await conversation.async_converse(
                self.hass,
                user_input.text,
                chat_log.conversation_id,
                user_input.context,
                language=user_input.language,
                agent_id=agent_id,
                device_id=user_input.device_id,
                satellite_id=user_input.satellite_id,
                extra_system_prompt=extra_system_prompt,
            )
        except (ValueError, HomeAssistantError) as err:
            _LOGGER.warning("Fallback agent %s failed: %s", agent_id, err)
            return self._result(
                user_input,
                chat_log,
                render("fallback_unavailable", self._lang(user_input)),
                None,
                outcome,
                error=True,
            )

    def _describe_actions(
        self, actions: tuple[Action, ...], areas: Mapping[str, str], lang: str, *, done: bool
    ) -> str:
        parts = []
        for a in actions:
            phrase = verb_phrase(a.verb.name, lang, done=done)
            targets = describe_targets(a.targets, areas, lang)
            param = " ".join(
                f"{v:g}" if isinstance(v, float) else str(v) for v in a.params.values()
            )
            parts.append(f"{phrase} {targets}" + (f" → {param}" if param else ""))
        return "; ".join(parts)

    # ---- executing -----------------------------------------------------------------------

    async def _run(
        self,
        user_input,
        chat_log,
        home: HomeModel,
        actions: tuple[Action, ...],
        condition: Condition | None,
        trace: Trace | None,
        outcome: str,
    ) -> conversation.ConversationResult:
        lang = self._lang(user_input)
        areas = self._area_names(home)
        ex = Executor(self.hass)
        if condition is not None and ex.condition_holds(condition) is not True:
            text = render(
                "condition_not_met",
                lang,
                subject=self._label(condition.subject, areas),
                expected=condition.expected_state,
            )
            return self._result(user_input, chat_log, text, trace, outcome)
        if all(a.verb.is_query for a in actions):
            lines = []
            for a in actions:
                for r in ex.read_states(a.targets):
                    room = areas.get(r.area_id or "", None)
                    label = f"{r.name} ({room})" if room else r.name
                    lines.append(f"{label}: {describe_state(r, lang)}")
            return self._result(
                user_input, chat_log, render("query_answer", lang, lines=lines), trace, outcome
            )
        results = await ex.execute(actions, user_input.context)
        failed = [r.entity_id for r in results if not r.ok]
        if failed:
            by_id = {e.entity_id: e for a in actions for e in a.targets}
            text = render(
                "execution_failed",
                lang,
                failed=[self._label(by_id[i], areas) for i in failed if i in by_id],
            )
        else:
            text = render(
                "action_done",
                lang,
                phrase=verb_phrase(actions[0].verb.name, lang, done=True),
                targets=describe_targets(tuple(e for a in actions for e in a.targets), areas, lang),
            )
        return self._result(user_input, chat_log, text, trace, outcome)

    # ---- the turn ------------------------------------------------------------------------

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        rt = self._rt
        lang = self._lang(user_input)
        pending = rt.pending.take(chat_log.conversation_id)
        home = rt.builder.build()
        if pending is not None:
            return await self._handle_reply(user_input, chat_log, home, pending)

        result = await rt.engine.decide(home, user_input.text)
        areas = self._area_names(home)

        if isinstance(result, Resolved):
            return await self._run(
                user_input,
                chat_log,
                home,
                result.actions,
                result.condition,
                result.trace,
                "Resolved",
            )

        if isinstance(result, NeedsConfirmation):
            cond = ""
            if result.condition is not None:
                cond = condition_clause(
                    self._label(result.condition.subject, areas),
                    result.condition.expected_state,
                    lang,
                )
            first = result.actions[0]
            question = render(
                "confirm",
                lang,
                phrase=verb_phrase(first.verb.name, lang, done=False),
                targets=describe_targets(
                    tuple(e for a in result.actions for e in a.targets), areas, lang
                ),
                reason=result.reason,
                condition=cond,
            )
            rt.pending.put(
                chat_log.conversation_id,
                PendingConfirm(result.actions, result.condition, question, rt.pending.now()),
            )
            return self._result(
                user_input, chat_log, question, result.trace, "NeedsConfirmation", cont=True
            )

        if isinstance(result, NeedsClarification):
            if result.verb is None or not result.candidates:
                return await self._escalate(
                    user_input, chat_log, result.trace, "NeedsClarification"
                )
            candidates = (
                result.candidates[: rt.engine._config.clarify_max_candidates]
                if hasattr(rt.engine, "_config")
                else result.candidates[:5]
            )
            labels = []
            for e in candidates:
                label = self._label(e, areas)
                labels.append(label if label not in labels else f"{label} [{e.entity_id}]")
            question = render("clarify", lang, options=labels)
            rt.pending.put(
                chat_log.conversation_id,
                PendingClarify(
                    result.verb,
                    dict(result.params),
                    tuple(candidates),
                    tuple(labels),
                    question,
                    rt.pending.now(),
                ),
            )
            return self._result(
                user_input, chat_log, question, result.trace, "NeedsClarification", cont=True
            )

        assert isinstance(result, Escalate)
        return await self._escalate(user_input, chat_log, result.trace, f"Escalate:{result.reason}")

    async def _handle_reply(
        self, user_input, chat_log, home: HomeModel, pending: PendingConfirm | PendingClarify
    ):
        rt = self._rt
        lang = self._lang(user_input)
        areas = self._area_names(home)
        state = {"question": pending.question, "reply": user_input.text}
        try:
            if isinstance(pending, PendingConfirm):
                answers = await rt.client.ask(
                    state,
                    {
                        CONFIRM_QID: ChoiceQ(
                            "The assistant asked the user `question` and the user replied `reply`. Does the "
                            "reply agree to go ahead, decline, or say something else (a change, a different "
                            "request, a question)?",
                            ("affirmative", "negative", "other"),
                            {
                                "affirmative": "yes, go ahead, do it, ja, mach, passt",
                                "negative": "no, stop, don't, cancel, nein, lass",
                                "other": "anything that is not a plain yes or no: a modification, a new request, a question",
                            },
                        )
                    },
                )
                c = answers.choice(CONFIRM_QID)
                if c.choice == "affirmative" and c.confidence >= REPLY_CONF:
                    return await self._run(
                        user_input,
                        chat_log,
                        home,
                        pending.actions,
                        pending.condition,
                        None,
                        "Confirmed",
                    )
                if c.choice == "negative" and c.confidence >= REPLY_CONF:
                    return self._result(
                        user_input, chat_log, render("cancelled", lang), None, "Cancelled"
                    )
                desc = self._describe_actions(pending.actions, areas, "en", done=False)
                return await self._escalate(
                    user_input,
                    chat_log,
                    None,
                    "ConfirmOther",
                    pending_context(pending.question, desc),
                )

            answers = await rt.client.ask(
                state,
                {
                    CLARIFY_QID: ChoiceQ(
                        "The assistant asked `question`, listing options. Which option does the user's `reply` pick?",
                        pending.labels + (NO_MATCH,),
                    )
                },
            )
            c = answers.choice(CLARIFY_QID)
            if c.choice != NO_MATCH and c.confidence >= REPLY_CONF and c.choice in pending.labels:
                target = pending.candidates[pending.labels.index(c.choice)]
                if pending.verb.param is not None and not pending.params:
                    desc = f"{verb_phrase(pending.verb.name, 'en', done=False)} {self._label(target, areas)}"
                    return await self._escalate(
                        user_input,
                        chat_log,
                        None,
                        "ClarifyNeedsParam",
                        pending_context(pending.question, desc),
                    )
                action = Action(pending.verb, (target,), pending.params)
                if pending.verb.risk is Risk.CONFIRM:
                    question = render(
                        "confirm",
                        lang,
                        phrase=verb_phrase(pending.verb.name, lang, done=False),
                        targets=self._label(target, areas),
                        reason="risk:confirm",
                        condition=None,
                    )
                    rt.pending.put(
                        chat_log.conversation_id,
                        PendingConfirm((action,), None, question, rt.pending.now()),
                    )
                    return self._result(
                        user_input, chat_log, question, None, "NeedsConfirmation", cont=True
                    )
                return await self._run(
                    user_input, chat_log, home, (action,), None, None, "Clarified"
                )
            desc = f"{verb_phrase(pending.verb.name, 'en', done=False)} one of: {', '.join(pending.labels)}"
            return await self._escalate(
                user_input, chat_log, None, "ClarifyOther", pending_context(pending.question, desc)
            )
        except (DecisionBackendError, KeyError, TypeError) as err:
            _LOGGER.warning("Reply judgment failed: %s", err)
            desc = pending.question
            return await self._escalate(
                user_input,
                chat_log,
                None,
                "ReplyJudgmentFailed",
                pending_context(pending.question, desc),
            )
```

Notes for the implementer:
- `rt.engine._config` is private; add a public read-only property `config` to `Engine` **only if** one does not exist — that is an engine change outside the global constraint, so instead store `clarify_max_candidates` on `HunchRuntime` (Task 2's `build_engine_config` knows it: add field `clarify_max_candidates: int` to the runtime, default from `EngineConfig().clarify_max_candidates`). Replace the `hasattr` line accordingly.
- `Risk` import path: `from hunch import Risk` if exported (it is listed in `hunch/__init__.py`).
- When a **trace is None** (reply turns), `_result` skips the chat-log payload but the `traces` buffer still gets an entry: change `_result` to always append `{"prompt", "outcome", "trace": payload or None}`.
- The `expired` sentence: when `take` returned `None` but `rt.pending` held an expired turn, the spec says respond `expired` then continue. `take` cannot distinguish; add `PendingStore.peek_expired(conversation_id) -> bool` (Task 7 file) that reports and clears an expired turn, and call it before `take`; when true, prefix the eventual answer with `render("expired", lang) + " "`.

- [ ] **Step 4: Run** — `uv run --group ha pytest tests/integration -v` → all pass. Fix label mismatches by reading what the engine actually offered (`calls`). Ruff.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests
git commit -m "Conversation entity: decide, execute, confirm/clarify turns judged by Jev, escalate with context, traces

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Options flow

**Files:**
- Modify: `custom_components/hunch/config_flow.py` (`HunchOptionsFlow`), `strings.json`, `translations/en.json`, `translations/de.json`
- Test: `tests/integration/test_options_flow.py`

**Interfaces:**
- Consumes: `OPT_*` constants, `THRESHOLD_FIELDS` (Task 2).
- Produces: options saved flat: `fallback_agent`, `model`, `response_language`, `timeout_ms`, `max_silent_targets`, `device_round`, `max_rounds`, and each threshold as `threshold_<name>`; `build_engine_config` (Task 2) is updated to read `threshold_<name>` keys instead of a nested dict (simpler for the form). Update `OPT_THRESHOLDS` usage accordingly.

- [ ] **Step 1: Failing test**

```python
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.integration.conftest import scripted


async def test_options_reload_the_engine_and_reject_self_as_fallback(
    hass: HomeAssistant, setup_hunch
):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    bad = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "fallback_agent": "conversation.hunch",
            "model": "jev-1.13.0",
            "response_language": "de",
            "timeout_ms": 1500,
            "max_silent_targets": 5,
            "device_round": True,
            "max_rounds": 2,
            "threshold_auto_execute": 0.8,
        },
    )
    assert bad["type"] is FlowResultType.FORM and bad["errors"] == {
        "fallback_agent": "cannot_select_self"
    }
    from unittest.mock import patch

    with patch("custom_components.hunch.build_client", return_value=client):
        ok = await hass.config_entries.options.async_configure(
            bad["flow_id"],
            {
                "model": "jev-1.13.0",
                "response_language": "de",
                "timeout_ms": 1500,
                "max_silent_targets": 5,
                "device_round": True,
                "max_rounds": 2,
                "threshold_auto_execute": 0.8,
            },
        )
        await hass.async_block_till_done()
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    rt = entry.runtime_data
    assert rt.response_language == "de"
    cfg = rt.engine._config if hasattr(rt.engine, "_config") else None
    assert entry.options["max_rounds"] == 2 and entry.options["device_round"] is True
    # build_engine_config forces max_rounds to 3 with device_round on
    from custom_components.hunch import build_engine_config

    assert build_engine_config(entry.options).max_rounds == 3
    assert build_engine_config(entry.options).thresholds.auto_execute == 0.8
```

Remove the `cfg = ...` line (unused). 

- [ ] **Step 2: Run, expect failure.** **Step 3: Implement**

In `config_flow.py`, replace `HunchOptionsFlow`:

```python
from homeassistant.helpers.selector import (
    BooleanSelector,
    ConversationAgentSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from hunch import Thresholds
from .const import (
    DEFAULT_RESPONSE_LANGUAGE,
    DEFAULT_TIMEOUT_MS,
    OPT_DEVICE_ROUND,
    OPT_FALLBACK_AGENT,
    OPT_MAX_ROUNDS,
    OPT_MAX_SILENT_TARGETS,
    OPT_MODEL,
    OPT_RESPONSE_LANGUAGE,
    OPT_TIMEOUT_MS,
    THRESHOLD_FIELDS,
)


def _options_schema() -> vol.Schema:
    defaults = Thresholds()
    fields: dict[Any, Any] = {
        vol.Optional(OPT_FALLBACK_AGENT): ConversationAgentSelector(),
        vol.Optional(OPT_MODEL, default=DEFAULT_MODEL): TextSelector(),
        vol.Optional(OPT_RESPONSE_LANGUAGE, default=DEFAULT_RESPONSE_LANGUAGE): SelectSelector(
            SelectSelectorConfig(
                options=["auto", "en", "de"],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="response_language",
            )
        ),
        vol.Optional(OPT_TIMEOUT_MS, default=DEFAULT_TIMEOUT_MS): NumberSelector(
            NumberSelectorConfig(min=300, max=10000, step=100, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(OPT_MAX_SILENT_TARGETS, default=20): NumberSelector(
            NumberSelectorConfig(min=1, max=200, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(OPT_DEVICE_ROUND, default=False): BooleanSelector(),
        vol.Optional(OPT_MAX_ROUNDS, default=2): NumberSelector(
            NumberSelectorConfig(min=2, max=4, step=1, mode=NumberSelectorMode.BOX)
        ),
    }
    for name in THRESHOLD_FIELDS:
        fields[vol.Optional(f"threshold_{name}", default=getattr(defaults, name))] = NumberSelector(
            NumberSelectorConfig(min=0, max=1, step=0.05, mode=NumberSelectorMode.BOX)
        )
    return vol.Schema(fields)


class HunchOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            own = self._own_entity_id()
            if own and user_input.get(OPT_FALLBACK_AGENT) == own:
                errors[OPT_FALLBACK_AGENT] = "cannot_select_self"
            else:
                for key in (OPT_TIMEOUT_MS, OPT_MAX_SILENT_TARGETS, OPT_MAX_ROUNDS):
                    if key in user_input:
                        user_input[key] = int(user_input[key])
                return self.async_create_entry(data=user_input)
        schema = self.add_suggested_values_to_schema(
            _options_schema(), user_input or self.config_entry.options
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _own_entity_id(self) -> str | None:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(self.hass)
        return reg.async_get_entity_id("conversation", DOMAIN, self.config_entry.entry_id)
```

Update `build_engine_config` in `__init__.py` to read `threshold_<name>` keys:

```python
raw = {
    name: options[f"threshold_{name}"]
    for name in THRESHOLD_FIELDS
    if f"threshold_{name}" in options
}
th = dataclasses.replace(Thresholds(), **{k: float(v) for k, v in raw.items()})
```

and remove `OPT_THRESHOLDS` from `const.py`. Also add `clarify_max_candidates` to the runtime if Task 8 needed it.

Add to `strings.json` / `en.json` under `"options": {"step": {"init": {"title": "Hunch options", "data": {...one label per option and per threshold_<name>...}}}, "error": {"cannot_select_self": "Hunch cannot be its own fallback."}}` and `"selector": {"response_language": {"options": {"auto": "Same as the request", "en": "English", "de": "German"}}}`; German equivalents in `de.json` (`"cannot_select_self": "Hunch kann nicht sein eigener Fallback sein."`, `"auto": "Wie die Anfrage"`, `"de": "Deutsch"`, `"en": "Englisch"`). Labels for thresholds: use the field name plus a short gloss, e.g. `"threshold_auto_execute": "Auto-execute confidence (default 0.7)"`.

- [ ] **Step 4: Run** — options test + whole integration suite pass; ruff. **Step 5: Commit** `"Options flow: fallback agent, model, language, limits, thresholds; reload on save"` with trailer.

---

### Task 10: Diagnostics, README, docs

**Files:**
- Create: `custom_components/hunch/diagnostics.py`
- Modify: `README.md` (root; add an "Integration" section), `docs/superpowers/specs/2026-09-21-hunch-integration-design.md` (status line), memory file per the session's memory rules
- Test: `tests/integration/test_diagnostics.py`

- [ ] **Step 1: Failing test**

```python
from homeassistant.components.diagnostics import REDACTED  # noqa: F401
from homeassistant.core import HomeAssistant

from custom_components.hunch.diagnostics import async_get_config_entry_diagnostics
from tests.integration.conftest import scripted


async def test_diagnostics_have_counts_and_traces_but_no_key(hass: HomeAssistant, setup_hunch):
    client, calls = scripted({})
    entry, _ = await setup_hunch(client, calls)
    entry.runtime_data.traces.append(
        {"prompt": "x", "outcome": "Escalate:no_intent", "trace": {"entries": []}}
    )
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "test-key" not in str(diag)
    assert diag["home"].keys() == {"floors", "areas", "entities", "scenes"}
    assert diag["traces"][0]["prompt"] == "x"
```

- [ ] **Step 2: Implement**

```python
"""Diagnostics: options (no key), home size, recent traces."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HunchConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HunchConfigEntry
) -> dict[str, Any]:
    rt = entry.runtime_data
    home = rt.builder.build()
    return {
        "options": dict(entry.options),
        "home": {
            "floors": len(home.floors),
            "areas": len(home.areas),
            "entities": len(home.entities),
            "scenes": len(home.scenes),
        },
        "traces": list(rt.traces),
    }
```

`entry.data` (the key) is deliberately not included.

- [ ] **Step 3: Run** → pass. Then run **everything**: `uv run ruff format . && uv run ruff check . && uv run pytest -q && uv run --group ha pytest tests/integration -q`.

- [ ] **Step 4: Docs**

Root `README.md`: add a section "Home Assistant integration" — install via HACS (custom repository → this repo), setup (API key), options, what Hunch answers itself vs hands off, where to read traces (chat log / diagnostics download), the `uv run --group ha pytest tests/integration` command. Set the spec's status line to "implemented 2026-…, see plan". Update the memory file `hass-jev-framework-idea.md` (both copies) with: integration implemented, layout, how tests run, publish state of `hunch-engine`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Diagnostics; README for the integration; spec status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review (done while writing)

- Spec coverage: §4 layout → T2; §5 runtime → T2; §6 builder → T4; §7 flow → T8 (expired → T8 note + T7 `peek_expired`); §8 executor → T5; §9 responder → T6; §10 pending → T7; §11 flows → T3, T9; §12 diagnostics → T10; §13 safety → T5 (context), T8 (self-loop guard, exact stored actions), T10 (no key); §14 tests → each task; §16 sequence → task order. `hunch-engine` publish → T1 step 7 (asks Julian).
- Type consistency: `PendingClarify(verb, params, candidates, labels, question, created)` used identically in T7/T8; `Executor.execute(actions, context) -> list[TargetResult]` in T5/T8; `render(outcome, language, **slots)` slot names match between T6 tables and T8 calls (`phrase, targets, reason, condition, options, failed, lines, subject, expected`); `build_client(entry)` patched at `custom_components.hunch.build_client` in T2 conftest and T9.
- Known judgment calls left to the implementer, each with the fallback spelled out: `AddConfigEntryEntitiesCallback` vs `AddEntitiesCallback`; abort reason `already_configured` vs `single_instance_allowed`; `async_get_assistant_settings` sync vs async; `Entity` positional order in tests.
