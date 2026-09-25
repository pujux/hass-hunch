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

> **Conditions:** "wenn es unter 20 Grad hat" is checked against the live sensor value before acting, and the answer names the value when nothing happens. Conditions Hunch cannot express ("wenn es dunkel wird", "sobald …") are handed to the fallback agent.
>
> **Assist pipeline setting:** leave "Prefer handling commands locally" **off**. With it on, HA's built-in agent handles simple sentences before Hunch sees them, so Hunch has no memory of them and follow-ups cannot work; fragments then reach Hunch without context and are handed off.
>
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
entities — including starting, cancelling and reading kitchen timers, and "für 15 Minuten" /
"in 15 Minuten" timed device actions — asks a yes/no/other confirmation before anything
destructive, and asks a clarifying question when a request is ambiguous between a few
candidates. Anything it isn't confident about — including relative changes, set-summary
queries, clock times and dates ("um 18 Uhr", "morgen früh"), sequences ("erst …, dann …"), and
conditions it can't express ("wenn es dunkel wird") — is escalated with the original,
unchanged text to the fallback agent you configured.

### Timers

Hunch runs its own timers: no `timer.*` helper entity and no voice satellite with a timer
handler required. "Timer 8 Minuten", "Stell einen Timer für die Nudeln auf 8 Minuten", "Wie
lange läuft der Timer für die Nudeln noch?" and "Timer abbrechen" all work out of the box, as
do timed device actions — "Wandlampe an für 15 Minuten" (turns it on now, undoes it after; a
lamp that was already on is left on afterwards, since nothing changed) and "Wandlampe in 15
Minuten aus" (turns it off later). Timers are persisted in a Home Assistant
`Store` and survive a restart; one that expired while Home Assistant was down fires as soon as
it starts back up.

When a timer or a scheduled action is due, Hunch fires a `hunch_timer_finished` event so you
can wire up your own automation (which speaker announces it, an LED, a notification — Hunch
itself never picks a speaker):

| field | meaning |
|---|---|
| `timer_id` | unique id of the timer |
| `kind` | `timer` (kitchen timer) / `revert` (the undo half of a "für" action) / `delayed` (the "in" action itself) |
| `label` | what the timer is for ("Nudeln"), or `None` when unnamed |
| `description` | for `revert`/`delayed`: what happens, rendered at creation ("Wandlampe (Vorzimmer) ausschalten") |
| `duration_seconds` | the timer's original duration |
| `duration_text` | the duration ready to speak, in the request's language ("10 Sekunden", "1 Stunde 20 Minuten") |
| `name` | what Hunch calls the timer: "Timer für Nudeln", the action clause of a `revert`/`delayed`, or "10-Sekunden-Timer" |
| `due_at` | when it was due, ISO 8601 |
| `overdue` | `true` if it fired late, on Home Assistant startup after a restart |
| `skipped` | `true` if a `revert`/`delayed` action was more than an hour overdue and was **not** carried out (a blind must not open hours late at night); the event still fires so you know it was skipped |
| `language` | the language the request was made in |
| `conversation_id`, `device_id`, `satellite_id`, `area_id`, `user_id` | who/what asked, and where |
| `executed`, `failed` | entity ids a `revert`/`delayed` action succeeded or failed on |

Optionally, set a **Timer script** in the options flow to have Hunch also call
`script.turn_on` on it (with the same fields passed as `variables`) — handy for making it
speak the result. The script runs for kitchen timers only (`kind: timer`); the undo half of a
"für 10 Minuten" and a delayed "in 10 Minuten" fire the event but are not announced. For
example, a script that announces a finished timer through a media player's TTS:

```yaml
alias: Timer finished
sequence:
  - action: tts.speak
    target:
      entity_id: tts.piper
    data:
      media_player_entity_id: media_player.kueche_lautsprecher
      message: "{{ name }} ist fertig."
```

Without a timer script set, only the event fires — write a plain automation on
`hunch_timer_finished` instead if you'd rather not use a script.

Clock times and dates, sequences, and conditions Hunch can't express still hand off, as above;
so do durations with no number Hunch can look up ("ein paar Minuten"), and a "für" on a verb
with no sensible inverse. `lock` is one of those on purpose: "Tür für 10 Minuten absperren"
would unlock the door itself, unattended, later — that hands off instead of ever being done
automatically. (`unlock`, whose inverse is plain `lock`, is unaffected: "für 10 Minuten
aufsperren" locks again afterwards.)

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
