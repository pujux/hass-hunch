# hass-hunch

Hunch turns a spoken or typed Home Assistant request into typed actions by
asking [Jev](https://typesafe.ai), TypeSafe AI's typed-decision model, many
narrow parallel questions. Code calculates, Jev judges.

This repository is a uv workspace with three planned sub-projects (see
`docs/superpowers/specs/2026-09-19-hunch-design.md`):

1. `packages/hunch` — the pure-Python engine library (no Home Assistant
   dependency).
2. `hass-hunch` — the Home Assistant custom integration.
3. Declared capabilities — a config layer on the integration.

## Development

```bash
uv sync
uv run pytest -q
```

`spikes/` holds throwaway scripts used to validate assumptions against the
real Jev API before building the corresponding engine component.

## Home Assistant integration

> **Follow-ups:** a sentence that leaves out what the previous one said ("und im Esszimmer", "aus", "was genau steht drauf") is completed from the last turn, for five minutes per conversation.

`custom_components/hunch` puts the engine in front of any conversation agent already
configured in your Home Assistant instance. It answers what it's confident about itself
and hands everything else off unchanged.

### Install

Add this repository to HACS as a custom repository (Integration category), then install
"Hunch" and restart Home Assistant.

### Setup

Settings → Devices & services → Add integration → Hunch. The only thing the config flow
asks for is your TypeSafe API key (a single Jev call validates it before the entry is
created).

### Options

Everything else lives in the options flow, all optional with sane defaults:

- **Fallback conversation agent** — where unhandled requests go (default: HA's default agent).
- **Model** — the pinned Jev model.
- **Response language** — `auto` (match the request), `en`, or `de`.
- **Timeout** — per-request timeout to TypeSafe, in ms.
- **Max silent targets** — how many devices a single request may touch before it asks first.
- **Device round / max rounds** — whether a third clarifying round may run, and the round budget.
- **Thresholds** — the engine's confidence cutoffs (auto-execute, confirm, clarify, etc.).

### What Hunch answers itself vs. hands off

Hunch resolves requests it's confident about into exact service calls against your exposed
entities, asks a yes/no/other confirmation before anything destructive, and asks a
clarifying question when a request is ambiguous between a few candidates. Anything it isn't
confident about — including relative changes and set-summary queries — is escalated with
the original, unchanged text to the fallback agent you configured.

### Traces

Every turn's engine trace (round-by-round reasoning, chosen outcome) is attached to the
conversation's chat log entry and also appears under the entry's **Diagnostics** download
(Settings → Devices & services → Hunch → the three-dot menu → Download diagnostics), which
also reports your current options and home size but never your API key.

### Developer commands

```bash
uv run pytest -q                                          # engine + unit tests
uv run --group ha --no-group dev pytest tests/integration # HA integration suite
```

Python 3.14 is required: Home Assistant 2026.9 requires it, and this repo targets that
release.

### Releasing the engine

```bash
uv build --package hunch-engine && uv run python tools/check_dist.py
set -a && source .env && set +a && uv publish dist/*
```

Build and check immediately before publishing; then bump the pin in `custom_components/hunch/manifest.json`, push, and `gh release create vX.Y.Z --target main`.
