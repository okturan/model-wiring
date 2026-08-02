# Model Wiring Surfaces

Reusable CLI, ANSI TUI, and browser controls for
[`model-wiring`](../model-wiring). The package does not execute a
model; it helps a human form the same secret-free `SelectionIntent` any app can
form programmatically.

```text
provider catalogue + public profiles
                ↓
      SelectionController
                ↓
       choose provider
                ↓
         choose model
                ↓
   configure mode and access
                ↓
        SelectionIntent
                ↓
       model-wiring
                ↓
        SelectionPlan event

Surfaces: composable CLI · full-screen ANSI TUI · Web Component
```

## Install and try

```bash
python3 -m venv .venv
.venv/bin/pip install -e ../model-wiring -e .

# Interactive ANSI selector.
.venv/bin/model-wiring-ui pick

# Deterministic, non-interactive rendering for another CLI.
.venv/bin/model-wiring-ui render --query luna --no-color

# Copy framework-free browser assets into an application.
.venv/bin/model-wiring-ui web-assets --output ./public/model-wiring
```

Python applications can embed the controller and renderer:

```python
from model_wiring import Catalog
from model_wiring_surfaces import SelectionController, render_screen

controller = SelectionController(Catalog.from_cache_or_sync())
print(render_screen(controller.view(), color=False))

controller.search("luna")  # provider match counts; no flat model dump
controller.activate_provider()  # enter the highlighted provider
controller.choose()  # choose its highlighted model
```

Applications with a narrower executor can pass `route_support(model)` and
`preferred_models`. The TUI opens on providers grouped as **Ready now**,
**Connect access**, and **Browse catalogue**. Choosing a provider reveals only
its models. Within each group, app recommendations come first, followed by the
shared popularity order and then stable provider name. Providers without an
application executor remain inspectable and fail closed if a user tries to
confirm one.

Use `↑`/`↓` to move, `Enter` or `→` to enter a provider, and `←` to return.
Start typing anywhere in provider/model browsing to search immediately;
`Backspace` edits and `Esc` clears the query before going back or closing.
Mode and access controls appear only after a model is chosen and use uppercase
`E`/`V`/`T`/`P`, so lowercase search text is never intercepted.

The browser primitive is a standards-based `<model-wiring-picker>` custom
element. Point its `endpoint` attribute at the core loopback API or at an
application-owned compatible backend:

```html
<script type="module" src="/model-wiring/model-wiring-picker.js"></script>
<model-wiring-picker
  endpoint="http://127.0.0.1:8765"
  recommended-provider="openai-codex"
  recommended-model="openai-codex/gpt-5.6-luna"
  supported-providers="openai-codex"
></model-wiring-picker>
```

It dispatches a `model-wiring-selection` event whose `detail` is the public
SelectionPlan. `supported-providers` is optional; when supplied, every other
provider is explicitly catalogue-only and cannot be resolved by the component.
Tokens never enter the component.

Serve the component and API from one origin when embedding it in an app. For the
included demo on port 8000, start the loopback API with the exact origin rather
than a wildcard:

```bash
model-wiring serve --allow-origin http://127.0.0.1:8000
python3 -m http.server 8000 --directory ./public/model-wiring
```

## Development

```bash
uv sync --locked
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
node --check src/model_wiring_surfaces/web/model-wiring-picker.js
```

No license has been selected for this project yet.
