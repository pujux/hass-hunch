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
