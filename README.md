# Model Wiring

Wire any custom AI workflow app to models, providers, credentials, billing
routes, and human selection surfaces.

Model Wiring is a kit of primitives, not another agent harness or gateway.
It gives an application everything between its own logic and the model
providers: a broad provider catalogue with overlay extensibility, credential
profiles and pools, auth flows, deterministic model selection with explicit
ambiguity errors, and ready-made human selection surfaces for the terminal
and the browser.

## Install and open it

```sh
# From a checkout, today:
pip install ./packages/core ./packages/surfaces .
model-wiring-pick
```

`model-wiring-pick` opens the provider picker with no subcommand to learn.
`model-wiring` is the JSON CLI (`access`, `login`, `select`, `serve`); the
picker is also available as `model-wiring-ui`.

Once the distributions are published, `pip install model-wiring` alone will
pull the whole kit — the repository root is a metapackage that depends on
both. It cannot resolve those siblings from a bare checkout because they are
not on an index yet, which is why the explicit paths are listed above.

## Packages

| Distribution | Path | Provides |
| --- | --- | --- |
| `model-wiring` | repository root | metapackage: installs everything, ships `model-wiring-pick` |
| `model-wiring-core` | `packages/core` | provider catalogue and overlays, access routes, credential profiles and pools, auth broker, login drivers, deterministic selection, CLI |
| `model-wiring-surfaces` | `packages/surfaces` | shared selection controller, ANSI terminal picker with direct type-to-search, self-contained Web Component picker |

Import names are unchanged: `model_wiring` and `model_wiring_surfaces`.

## Connecting a provider

```sh
model-wiring access show anthropic       # what would connect this provider?
model-wiring login anthropic             # describes what it needs
model-wiring access status               # what is configured already
```

Every catalogued provider answers the first question, including the ones this
application cannot execute — catalogue visibility and execution support are
separate axes. See [`docs/specs/`](docs/specs/README.md).

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
focus; see [`docs/specs/`](docs/specs/README.md).

## License

Apache-2.0; see [LICENSE](LICENSE). Catalogue data comes from the MIT-licensed
Models.dev project; see [THIRD_PARTY.md](packages/core/THIRD_PARTY.md).
