# Model Wiring

Wire any custom AI workflow app to models, providers, credentials, billing
routes, and human selection surfaces.

Model Wiring is a kit of primitives, not another agent harness or gateway.
It gives an application everything between its own logic and the model
providers: a broad provider catalogue with overlay extensibility, credential
profiles and pools, auth flows, deterministic model selection with explicit
ambiguity errors, and ready-made human selection surfaces for the terminal
and the browser.

## Packages

| Package | Path | Provides |
| --- | --- | --- |
| `model-wiring` | `packages/core` | provider catalogue and overlays, credential profiles and pools, auth broker, deterministic selection, popularity-ordered discovery, CLI |
| `model-wiring-surfaces` | `packages/surfaces` | shared selection controller, ANSI terminal picker with direct type-to-search, self-contained Web Component picker |

## Development

The repository is a [uv](https://docs.astral.sh/uv/) workspace. From the
root:

```sh
uv sync
.venv/bin/python -m unittest discover -s packages/core/tests -q
.venv/bin/python -m unittest discover -s packages/surfaces/tests -q
```

## Status

Pre-release (0.1.0). The first integration is Atlas, a local session
console that uses the catalogue, selection controller, and terminal picker.
Provider onboarding depth (per-provider auth flow execution) is the current
focus.
