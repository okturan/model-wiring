# Flow: API keys, environment bundles, local endpoints

Status: Draft. The unglamorous majority: most catalogued providers are
connectable only this way, and the kit must make that a first-class guided
flow rather than a config-file chore.

## Routes

### api_key (paste)

- Driver: `api_key_paste`. Prompt: `SecretPrompt(fields=["api_key"], masked)`.
- Storage: keyring by default (`secret_ref = "api-key:<provider>:<label>"`),
  memory store for tests; never written to disk in plain text by the kit.
- Profile: `auth_kind="api_key"`, `billing_kind="api"` unless the provider's
  `AuthMethod` declares otherwise (e.g. marketplace).
- Optional validation probe immediately after paste (R5); failure keeps the
  profile but marks `last_probe_state`.

### credential_bundle (multi-variable)

Providers whose `env` tuple names several variables (cloud SDK styles) use
the same paste driver with multiple masked fields, stored as one bundle.

### environment (already shipped)

`discover_environment_profiles` builds presence-only profiles
(`secret_store="environment"`, priority 500). Additions:

- Surfaces label these "from environment" and never display values.
- **Promote to stored**: explicit user action copies the current value into a
  real store-backed profile so it survives environment changes. Promotion is
  the only moment the kit reads the variable's value.

### anonymous (local endpoints)

Ollama, LM Studio, llama.cpp servers, and other self-hosted endpoints are
valid `auth_kind="anonymous"`, `billing_kind="local"` profiles — "no
credential" is a working state, not a setup failure (Aider/OpenCode/Goose
lesson). Profile metadata carries `base_url`. Readiness probe is a cheap
endpoint check defined per adapter, never a completion call.

## Surfaces

The connect view for an api_key route shows: what the key unlocks, which
account pays (billing kind), where the provider issues keys
(`ProviderSpec.doc_url`), and the masked input. Env-discovered profiles show
their variable names and the promote action.

## Failure modes

- Paste of an obviously malformed key (provider-declared prefix/length in
  `AuthMethod.metadata`, when known): warn before storing, never block.
- Store unavailable (no keyring): offer environment instructions instead of
  failing silently; see architecture open question on an encrypted file store.

## Verify before implementation

- Key format hints per provider from provider documentation (`doc_url`), not
  guessed.
- Local endpoint probe paths per adapter (e.g. Ollama's version/tags routes)
  against each project's current docs.
