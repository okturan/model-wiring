# Model Wiring

Model Wiring is the reusable provider/model/auth selection layer that an
AI application should not have to rebuild. It is intentionally **not** an agent
harness: it does not run prompts, tools, loops, or sessions.

```text
 human or application intent
          |
          v
 +---------------------------+
 | model-wiring        |
 | catalog + auth + selector |
 +---------------------------+
       | credential-free SelectionPlan
       v
 existing app / harness / SDK / worker
       |
       v
 provider API or delegated subscription client
```

The same core is usable as a Python library, a Unix JSON CLI, or a loopback JSON
service. [`model-wiring-surfaces`](../model-wiring-surfaces) is its sibling
package for CLI, ANSI TUI, and browser selection controls.

## What it owns

- a Models.dev-backed catalog with local provider/model overlays;
- provider, model, modality, capability, limit, cost, variant, effort, and tier
  metadata;
- a shipped, overlay-extensible provider popularity order for human discovery,
  with stable alphabetical fallback for custom and newly catalogued providers;
- deterministic selection with explicit ambiguity and incompatibility errors;
- credential profiles and billing provenance without putting secrets in public
  plans;
- environment credential-bundle discovery and fill-first, round-robin, or
  least-used credential pools;
- API-key, bearer-token, delegated-client, and OAuth credential plumbing;
- environment, in-memory, and optional OS-keyring secret stores;
- OAuth refresh coordination with a SQLite single-writer lease and atomic token
  replacement;
- stable, content-derived catalog and selection identifiers.

It does **not** own inference, fallback execution, retry loops, tool calling,
conversation storage, or billing. Those remain with the consuming application.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

# Fetch and cache the current catalog.
.venv/bin/model-wiring catalog sync

# Machine-readable discovery.
.venv/bin/model-wiring catalog search 'openai/luna' --json

# Resolve an auditable, secret-free selection plan.
.venv/bin/model-wiring select \
  --model openai/gpt-5.6-luna \
  --effort high \
  --variant fast \
  --require tool_call \
  --json
```

Applications can use the Python API directly:

```python
from model_wiring import Catalog, SelectionIntent, Selector

catalog = Catalog.from_cache_or_sync()
plan = Selector(catalog).select(
    SelectionIntent(
        model="openai/gpt-5.6-luna",
        effort="high",
        variant="fast",
        required_capabilities=("tool_call",),
    )
)

# plan.to_dict() contains profile references and billing provenance, never a key.
existing_harness.run(selection=plan)
```

`POPULAR_PROVIDER_IDS` and `provider_popularity_key()` are public discovery
primitives. Provider payloads expose the applied rank as non-secret
`metadata.popularity_rank`; an overlay may replace that rank for a particular
audience. Application recommendations remain a separate surface concern and do
not mutate the neutral catalogue order.

## Credentials and tokens

Credential metadata and credential material are deliberately separate:

```text
 CredentialProfile                         SecretStore
 id, provider, auth kind, billing kind     opaque secret material
 secret_ref -----------------------------> lookup only inside AuthBroker.lease()

 SelectionPlan                             CredentialLease
 profile id + provenance, no secret        short-lived, redacted repr, zeroed exit
```

The default file-backed profile registry is SQLite in WAL mode with `0600`
permissions. Secret material is never stored in that database. Install the
`keyring` extra for OS-backed storage or supply a custom `SecretStore`. OAuth
refresh logic accepts standards-compliant provider drivers; the built-in OAuth
client handles PKCE, device authorization, and refresh. Delegated profiles let
an app use a Codex/Claude/GitHub client without extracting or copying its
subscription token.

The selector never silently changes an API-billed profile into a subscription
profile (or the reverse). Billing route is an explicit part of the plan.

Models.dev environment declarations may describe one key or a multi-variable
credential bundle. `discover_environment_profiles()` only checks that every
declared variable exists; it never copies values into profile metadata. A
`CredentialPool` can then claim a profile with an atomic fill-first,
round-robin, or least-used policy before the application resolves its plan.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

See [the contract](docs/SPEC.md) and [research adoption notes](docs/RESEARCH-ADOPTION.md).

## License status

No license has been selected for this project yet. Models.dev is an external
MIT-licensed data source; see [THIRD_PARTY.md](THIRD_PARTY.md).
