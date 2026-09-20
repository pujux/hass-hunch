# Hunch — Home Assistant integration (sub-project 2) design

Date: 2026-09-21. Status: implemented 2026-09-21 on branch `integration` (plan: docs/superpowers/plans/2026-09-21-hunch-integration.md).

Parent spec: [`2026-09-19-hunch-design.md`](2026-09-19-hunch-design.md) (the engine, §4.2 sketched the
integration). Where this document and the parent disagree, this document wins for the
integration; the parent stays authoritative for the engine.

## 1. Summary

A Home Assistant custom integration, `custom_components/hunch`, that puts the `hunch` engine in
front of any configured conversation agent. Zero configuration beyond the TypeSafe API key.
It builds a `HomeModel` from HA's registries, asks the engine to decide, executes `Resolved`
actions as exact service calls with the caller's context, holds confirmation and clarification
turns, and hands everything else to a fallback agent with the unchanged text. Every turn
carries the engine trace into the chat log and diagnostics.

The engine library gets exactly **one additive change**: `NeedsClarification` carries the
`verb` and `params` it was clarifying (today it holds only the candidates), so the integration
can build the action from the user's pick without reading trace notes. Nothing else in the
engine changes.

## 2. Goals and non-goals

Goals

- Install from HACS, paste an API key, talk to Assist. Everything else defaulted.
- Execute exactly what the engine resolved: the same entity ids, no re-matching by name.
- Confirmation and clarification as real multi-turn exchanges, judged by Jev, never by string
  matching.
- Escalation that preserves the LLM agent's own multi-turn continuity.
- The full engine trace inspectable per turn (chat log) and in bulk (diagnostics).
- Responses in English and German, from Hunch's own templates.

Non-goals (v1)

- Relative changes ("um 2 Grad wärmer"): stay escalated; the LLM agent has current state.
- Declared capabilities / YAML (sub-project 3).
- Re-planning a confirmation reply that modifies the pending action ("not the kitchen"): handed
  to the fallback agent with the pending action as context.
- Multiple Hunch instances per HA. One config entry.
- Rendering with HA's built-in intent response templates. They belong to the default agent.

## 3. Decisions taken in brainstorming (do not re-ask)

| Topic | Decision | Why |
|---|---|---|
| Distribution | Engine published to PyPI as **`hunch-engine`** (import name stays `hunch`; `hunch` on PyPI is a squatted placeholder). Component pins `hunch-engine==<version>` in `manifest.json`. Component lives at `custom_components/hunch` in this repo with `hacs.json`. | Clean separation, versioned, HACS-installable. |
| Execution | **Direct service calls** with exact entity ids and the caller's `Context`. Not intents. | Built-in intents take `name`/`area`/`domain` slots and re-resolve targets by fuzzy name; they cannot take entity ids and would break exactly where Hunch already disambiguated (two "Temperatur" sensors in one room, "Tür" in two rooms). Intent handlers also do not render speech for third-party agents. Permission checks apply to service calls with a user context just as to intents. |
| Confirmation reply | Jev `Choice{affirmative, negative, other}`; *other* → fallback agent with the pending action described in `extra_system_prompt`. | Hunch never guesses on modifications; the LLM handles "no, not the kitchen" well. |
| Fallback agent | Any agent, chosen in the options flow; unset = HA's default agent; Hunch refuses to select itself. | Zero-config still answers everything. |
| Relative changes | Escalate (unchanged from the engine). | No engine work before the integration exists. |
| Config flow | API key only at setup; everything else in an options flow with engine defaults. | Zero-config. |
| Response text | Hunch's own en/de templates, verb phrases taken from the engine phrasebooks. | See execution row; no second vocabulary. |

## 4. Layout and packaging

```
custom_components/hunch/
  __init__.py          entry setup/unload/reload, runtime object
  manifest.json        domain hunch, requirements ["hunch-engine==X.Y.Z"],
                       dependencies ["conversation", "homeassistant"],
                       integration_type service, iot_class cloud_polling, config_flow true
  const.py             DOMAIN, option keys, defaults
  config_flow.py       ConfigFlow (API key) + OptionsFlow
  home_model.py        HomeModelBuilder (+ pure mapping function)
  conversation.py      HunchConversationEntity
  executor.py          verb -> service map, condition check
  responder.py         all user-facing sentences, en + de
  pending.py           pending confirmation/clarification per conversation id
  diagnostics.py       last 50 traces
  strings.json, translations/en.json, translations/de.json
hacs.json
tests/integration/     HA-harness tests (uv group "ha")
```

`packages/hunch/pyproject.toml` gets `name = "hunch-engine"`; the package directory and import
stay `hunch`. Release: build with `uv build`, publish with `uv publish` (token in `.env`, never
printed). The manifest pin is bumped by hand with each engine release.

Python 3.13 (HA 2026.x). Target HA release: the one `pytest-homeassistant-custom-component`
pins at implementation time (2026.9.3 as of this writing); `hacs.json` declares
`homeassistant: "2026.8.0"` as the floor.

At implementation time, HA 2026.9 raised its own floor to Python 3.14, so the workspace
(`pyproject.toml`) requires Python 3.14 as well. The `hunch-engine` distribution
(`packages/hunch/pyproject.toml`) keeps `requires-python = ">=3.13"` with no upper cap — it
has no HA dependency and stays usable from older interpreters; the 3.14 floor is a
workspace/integration-side requirement, not an engine one.

## 5. Runtime object

`HunchRuntime` (a dataclass stored in `entry.runtime_data`):

- `client: TypeSafeDecisionClient` — API key from the entry, model and timeout from options.
- `engine: Engine` — `EngineConfig`/`Thresholds` built from options; `Phrasebook` fixed to `EN`
  (measured better than DE for Jev even on German prompts; response language is separate).
- `builder: HomeModelBuilder`
- `pending: PendingStore`
- `traces: deque[dict]` (maxlen 50) for diagnostics.
- `fallback_agent_id: str | None`

Options change → `async_reload_entry`. Unload closes the client and removes registry listeners.

## 6. HomeModelBuilder

Two parts.

**Pure mapping** `home_from_registries(floors, areas, devices, entities, exposed_ids, states,
vocabulary) -> HomeModel`, mirroring `hunch.loaders.home_from_export` rule for rule:

- Floors: id, name, aliases, area ids of areas whose `floor_id` matches.
- Areas: id, name, aliases, floor id.
- Entities: **exposed only**; area = entity's area or, if none, its device's area; name =
  registry `name` → `original_name` → state `friendly_name` → device `name_by_user`/`name` →
  entity id; aliases from the registry; device id and device name; verbs =
  `verbs_for_domain(vocabulary, domain)`; state = current state string or `None`.
- `scene` and `script` entities go to `HomeModel.scenes`, not `entities`.

Parity test: fed with the dicts from an export JSON, the result equals `home_from_export` on the
same file.

**HA wrapper** `HomeModelBuilder(hass)`:

- Reads `area_registry`, `floor_registry`, `device_registry`, `entity_registry`, and the exposed
  set via `homeassistant.components.homeassistant.exposed_entities.async_get_assistant_settings(hass, "conversation")`
  (entries with `should_expose` true), i.e. exactly the set Assist uses.
- Caches the **static skeleton** (everything except `Entity.state`).
- Invalidates on `EVENT_AREA_REGISTRY_UPDATED`, `EVENT_FLOOR_REGISTRY_UPDATED`,
  `EVENT_ENTITY_REGISTRY_UPDATED`, `EVENT_DEVICE_REGISTRY_UPDATED`, and
  `async_listen_entity_updates(hass, "conversation", …)` — the same signals the default agent
  uses. Rebuild is lazy on next request.
- `build() -> HomeModel` stamps every skeleton entity with its live state in one pass
  (`dataclasses.replace`). No state caching; queries and conditions always see fresh state.

## 7. The conversation entity

`HunchConversationEntity(ConversationEntity)`, `supported_languages = "*"`, one entity per
config entry, `_attr_name` from the entry title.

`_async_handle_message(user_input, chat_log) -> ConversationResult`:

1. **Pending turn?** `pending.take(conversation_id)` returns and clears a live pending turn
   (single-use, 120 s TTL) or `None`. Live → step 6. Expired → respond `expired` and continue
   with step 2 on the same text.
2. **Decide.** `home = builder.build()`, `result = await engine.decide(home, text)`.
3. **`Resolved`.** If `result.condition`: read `hass.states.get(subject).state`; if it does not
   equal `expected_state` → respond `condition_not_met`, execute nothing. Else
   `executor.execute(actions, context)`; respond `action_done` (commands) or `query_answer`
   (query verb). Per-target failures → `execution_failed` naming the failed targets; the
   successful ones stay executed.
4. **`NeedsConfirmation`.** `pending.put(conversation_id, Confirm(actions, condition, question))`;
   respond with the `confirm` sentence, `continue_conversation=True`.
5. **`NeedsClarification`.** Candidates capped at `clarify_max_candidates` (5); `verb` and
   `params` come from the result (see §1). Store `Clarify(verb, params, candidates, question)`;
   respond with
   the `clarify` list, `continue_conversation=True`. `which_area` with nothing to list, or too
   many candidates → step 7.
6. **Reply to a pending turn** — one Jev call over state `{"question": …, "reply": …}`:
   - `Confirm`: `Choice{affirmative, negative, other}` with descriptions. `affirmative` with
     confidence ≥ 0.7 → execute the stored actions exactly (condition re-checked as in step 3).
     `negative` ≥ 0.7 → `cancelled`. Anything else → step 7 with
     `extra_system_prompt = responder.pending_context(pending)`.
   - `Clarify`: `Choice` over candidate labels + `none of these`. A pick ≥ 0.7 → one `Action`
     from the stored verb/params for that candidate, then the same gate as the engine's risk
     tiers: `CONFIRM` verb → new pending confirmation; else execute. `none of these` or a
     hesitant pick → step 7 with context.
   - Jev error on the reply judgment → step 7 with context.
7. **Escalate.** `conversation.async_converse(hass, text, conversation_id, context,
   language=user_input.language, agent_id=fallback, device_id=…, satellite_id=…,
   extra_system_prompt=…)`. `fallback` is the option or `None` (HA default). If the resolved
   agent is this entity → `fallback_unavailable`. `ValueError`/`HomeAssistantError` from the
   call → `fallback_unavailable`. The fallback's `ConversationResult` is returned as-is.
8. **Trace.** Every branch that Hunch itself answers appends
   `AssistantContent(agent_id=self.entity_id, content=<sentence>, native=trace.to_dict())`
   to the chat log and pushes `{"prompt", "outcome", "trace"}` to `runtime.traces`. Escalated
   turns push the trace to `runtime.traces` only (the fallback writes its own chat-log entry).

Timeouts: the engine's client timeout (option, default 1500 ms) covers each Jev round; an
outage becomes `Escalate("decision_backend_unavailable")` inside the engine and follows step 7.

## 8. Executor

`execute(actions, context) -> list[TargetResult]`. Targets are grouped by domain per action;
one `hass.services.async_call(domain, service, data, blocking=True, context=context)` per
group. `data` always carries `entity_id: [ids]`.

| verb | service | data |
|---|---|---|
| turn_on / turn_off | `homeassistant.turn_on` / `homeassistant.turn_off` | — |
| set_brightness | `light.turn_on` | `brightness_pct` |
| open / close | `cover.open_cover` / `cover.close_cover` | — |
| set_position | `cover.set_cover_position` | `position` |
| lock / unlock | `lock.lock` / `lock.unlock` | — |
| set_temperature | `climate.set_temperature` | `temperature` |
| set_volume | `media_player.volume_set` | `volume_level = n / 100` |
| media_play / media_pause | `media_player.media_play` / `media_player.media_pause` | — |
| arm / disarm | `alarm_control_panel.alarm_arm_away` / `alarm_control_panel.alarm_disarm` | — |
| activate | `scene.turn_on` / `script.turn_on` (by the target's domain) | — |
| query_state | no service; reads `hass.states.get(id)` per target | — |

`arm` means arm-away: the engine has no arming mode, and the verb is `CONFIRM` tier, so the
mode is spoken in the confirmation question before anything happens. A service call raising
`HomeAssistantError`/`ServiceNotFound`/`Unauthorized` marks that group's targets failed and
continues with the other groups.

Condition check: `hass.states.get(subject.entity_id).state == expected_state`. Exactly equality,
no operators, matching the two-slot model.

## 9. Responder

`responder.py`: `render(outcome, language, **slots) -> str`. Tables for `en` and `de`; any
other language falls back to `en`. Language = the option if pinned, else `user_input.language`
(two-letter prefix).

| outcome | slots | en example |
|---|---|---|
| `action_done` | verb phrase, n, kind, place | "Done: turned off 13 lights on the Untergeschoss." |
| `query_answer` | list of (name, room, state, unit) ≤ 5 | "Temperatur (Wohnzimmer): 23.6 °C" |
| `confirm` | verb phrase, targets (names ≤ 3 else count + place), reason, condition? | "Turn off 26 lights in the whole home? That is a lot at once." |
| `clarify` | candidates (name, room) | "Which one: Tür (Galerie), Tür (Schlafzimmer)?" |
| `cancelled` | — | "Okay, nothing changed." |
| `condition_not_met` | subject, expected | "Dachterrassentür is not open, so I left the blinds alone." |
| `expired` | — | "That question has expired; treating this as a new request." |
| `fallback_unavailable` | — | "I can't do that myself, and no other assistant is available." |
| `execution_failed` | failed names | "Done, except: Spots (Küche)." |
| `pending_context` (for `extra_system_prompt`, English only) | question, actions | "The assistant proposed: turn off 13 lights … The user was asked to confirm and instead replied with the following message." |

Verb phrases come from `hunch.phrasing.EN/DE` (`verb_phrasing`, `domain_labels`) so the
integration adds no vocabulary. Binary sensor and cover states are rendered as words
(`on/off` → open/closed, detected/clear by device class when present; else on/off). Numbers
use the HA locale's decimal separator; units from the state's `unit_of_measurement`.

## 10. Pending turns

`PendingStore`: `put(conversation_id, turn)`, `take(conversation_id) -> turn | None` (removes;
returns `None` if missing or older than 120 s). One pending turn per conversation id; a new
request replaces it. In-memory only; lost on restart by design.

`Confirm(actions, condition, question, created)`; `Clarify(verb, params, candidates, question,
created)`. `candidates` are `Entity`s; the label offered to Jev is `"{name} ({room})"`, unique
by construction (duplicates get the entity id appended).

## 11. Config and options flow

**ConfigFlow** (`user` step): `api_key` (password field). Validation: one `system_one` call
with a single Noul over `{"probe": true}` and the default model; `TypeSafeError` with status
401/403 → `invalid_auth`; 404/unknown model → `unknown_model`; timeout → `cannot_connect`.
`async_set_unique_id(DOMAIN)` + `_abort_if_unique_id_configured` → single instance. Title
"Hunch".

**OptionsFlow**, one form, all defaulted:

| option | selector | default |
|---|---|---|
| `fallback_agent` | `ConversationAgentSelector` | unset (HA default agent) |
| `model` | text | `jev-1.13.0` |
| `response_language` | select `auto`, `en`, `de` | `auto` |
| `timeout_ms` | number 300–10000 | 1500 |
| `max_silent_targets` | number 1–200 | 20 |
| `device_round` | boolean | false |
| `max_rounds` | number 2–4 | 2 (validated ≥ 3 when `device_round`) |
| advanced: the `Thresholds` fields | number 0–1 step 0.05 | engine defaults |

Saving with `fallback_agent` equal to this entry's own conversation entity → form error
`cannot_select_self`. Options changes reload the entry.

## 12. Diagnostics

`async_get_config_entry_diagnostics` returns `{"options": <redacted of api_key>, "home":
{"floors": n, "areas": n, "entities": n, "scenes": n}, "traces": list(runtime.traces)}`.
Traces contain prompts and entity names, which the user already sees in the chat log; no
secrets.

## 13. Safety (integration side)

- Only exposed entities are ever in the model, so nothing unexposed can be targeted.
- Service calls carry the caller's `Context`; HA's entity permission check applies. Unauthorized
  → `execution_failed`, not a crash.
- The pending turn executes **exactly** the stored actions; the reply text is never re-parsed
  into actions by Hunch.
- `extra_system_prompt` to the fallback contains only Hunch's own rendered description plus
  the user's question text; no entity ids, no API data.
- Hunch never escalates to itself (loop guard by entity id).

## 14. Testing

- **Pure units (default `pytest`, no HA import):** `home_from_registries` parity with
  `home_from_export` on `golden/homes/*.json` when present and on a small fixture always;
  executor map (every vocabulary verb maps, params converted); responder (every outcome × en/de
  renders, no `KeyError`); pending store TTL.
- **HA harness (`uv run --group ha pytest tests/integration`):**
  `pytest-homeassistant-custom-component` pinned to the targeted HA release. The runtime's
  client is a `FakeDecisionClient` with scripted answers. Cases: registry → model incl. device
  area inheritance and exposure change invalidation; each `Resolution` → recorded service calls
  + sentence; confirmation yes / no / other (stub fallback receives text + extra prompt);
  clarification pick / none; condition met / not met; escalation with unchanged text and same
  conversation id; self-fallback rejected; options reload rebuilds the engine; diagnostics
  shape; unload removes listeners.
- **Manual on Julian's HA:** the 41 German corpus prompts through Assist, traces read from the
  chat log. No automated test talks to a real HA or to Jev.

## 15. Open questions

1. `arm` mode: arm-away is a default, not a judgment. A Jev `Choice{home, away, night}` on the
   confirmation turn is the natural fix if it matters in practice.
2. Query answers with more than 5 targets never occur (the engine escalates collective
   queries); if the cap changes, the responder needs a summary form.
3. Chat-log `native` payload size: a trace is ~5–10 kB of JSON per turn; fine for the log, but
   revisit if HA persists chat logs to disk in a later release.
4. Response wording is Hunch's; if HA later exposes its intent response templates to third-party
   agents, switch `action_done`/`query_answer` to them.

## 16. Sequence

1. Engine: `NeedsClarification.verb/params`; rename distribution to `hunch-engine`; publish 0.1.0.
2. Component skeleton: manifest, const, runtime, config flow, empty conversation entity.
3. `home_from_registries` + parity test; `HomeModelBuilder` with invalidation.
4. Executor + responder (pure units).
5. Conversation entity: Resolved/Escalate paths.
6. Pending store, confirmation and clarification turns.
7. Diagnostics, options reload, translations.
8. HA-harness suite; manual run on Julian's HA.
