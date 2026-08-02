# Model Provider Surfaces

Reusable CLI, ANSI TUI, and browser controls for
[`model-provider-kit`](../model-provider-kit). The package does not execute a
model; it helps a human form the same secret-free `SelectionIntent` any app can
form programmatically.

```text
              +---------------- shared SelectionController ---------------+
              |                         |                                  |
              v                         v                                  v
       composable CLI rows      full-screen ANSI TUI             Web Component
              |                         |                                  |
              +---------------- SelectionIntent ---------------------------+
                                        |
                                model-provider-kit
                                        |
                                SelectionPlan event
```

## Install and try

```bash
python3 -m venv .venv
.venv/bin/pip install -e ../model-provider-kit -e .

# Interactive ANSI selector.
.venv/bin/model-provider-ui pick

# Deterministic, non-interactive rendering for another CLI.
.venv/bin/model-provider-ui render --query luna --no-color

# Copy framework-free browser assets into an application.
.venv/bin/model-provider-ui web-assets --output ./public/model-provider
```

Python applications can embed the controller and renderer:

```python
from model_provider import Catalog
from model_provider_surfaces import SelectionController, render_screen

controller = SelectionController(Catalog.from_cache_or_sync())
controller.search("luna")
print(render_screen(controller.view(), color=False))
```

The browser primitive is a standards-based `<model-provider-picker>` custom
element. Point its `endpoint` attribute at the core loopback API or at an
application-owned compatible backend:

```html
<script type="module" src="/model-provider/model-provider-picker.js"></script>
<model-provider-picker endpoint="http://127.0.0.1:8765"></model-provider-picker>
```

It dispatches a `model-provider-selection` event whose `detail` is the public
SelectionPlan. Tokens never enter the component.

Serve the component and API from one origin when embedding it in an app. For the
included demo on port 8000, start the loopback API with the exact origin rather
than a wildcard:

```bash
model-provider serve --allow-origin http://127.0.0.1:8000
python3 -m http.server 8000 --directory ./public/model-provider
```

## Development

```bash
PYTHONPATH=../model-provider-kit/src:src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

No license has been selected for this project yet.
