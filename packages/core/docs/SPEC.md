# Model Wiring contract

## Boundary

The kit answers: **which provider route, model, variant, reasoning effort,
billing route, and credential profile satisfy this intent?**

It does not answer: what prompt should run, which tools may execute, how an
agent loops, how retries/fallbacks are performed, or where conversations live.

## Core records

### CatalogSnapshot

- `providers`: stable provider IDs and their models;
- `source`, `fetched_at`, `digest`: freshness and provenance;
- provider data: display name, adapter hint, API/docs URLs, auth environment
  hints and declared authentication methods;
- model data: modalities, capabilities, reasoning options, variants, limits,
  prices, lifecycle status, and upstream metadata.

### CredentialProfile

- stable profile ID and provider ID;
- authentication kind: `api_key`, `bearer`, `credential_bundle`, `oauth`,
  `delegated`, or `anonymous`;
- billing kind: `api`, `subscription`, `marketplace`, `local`, or `unknown`;
- secret reference, account label, priority, scopes, enabled state, and
  non-secret metadata.

CredentialProfile never contains the credential value.

### SelectionIntent

- exact `provider/model`, a provider plus model, or an unambiguous search query;
- optional role, variant, effort, tier, credential profile, and billing kind;
- required capabilities, input/output modalities, and minimum limits.

### SelectionPlan

- resolved provider/model and adapter hint;
- validated variant, effort, tier, capabilities, modalities, and limits;
- resolved credential profile ID, auth kind, and billing kind;
- catalog digest and deterministic plan ID;
- human-readable decision reasons.

SelectionPlan never contains credential material.

## Resolution rules

1. Exact identifiers win. A bare model ID must resolve to exactly one provider.
2. Search is allowed for discovery but ambiguous matches are returned to the
   caller, never silently guessed.
3. Required capability, modality, and limit constraints are hard gates.
4. Unsupported effort, variant, or tier is an error.
5. An explicitly requested credential profile is never replaced.
6. Otherwise choose enabled provider profiles by exact billing kind, then
   priority, then stable profile ID. Cross-billing fallback is forbidden.
7. The plan ID is derived from canonical public plan content, never position.

## Catalog composition

```text
 Models.dev JSON -----------+
                            |
 cached snapshot -----------+--> normalized catalog --> stable digest
                            |
 local overlay manifests ---+
```

Overlays may add providers, clone a model catalog for a delegated route, patch
model metadata, add aliases, or declare roles. They may not contain secrets.

## Auth boundary

`AuthBroker.lease(profile_id)` is the only core operation that materializes a
secret. It returns a context-managed `CredentialLease` with a redacted string
representation. OAuth refresh uses a cross-process SQLite lease so one writer
refreshes and atomically replaces rotated material while peers wait and reread.

Backends:

- memory: tests and short-lived embedding;
- environment: read-only references to existing environment variables;
- keyring: optional OS credential-store adapter;
- delegated: a named SDK/CLI route; no token extraction;
- custom: application-defined `SecretStore` protocol.

No plaintext file secret backend is provided.

Credential pools are an explicit pre-selection step. Fill-first, round-robin,
and least-used claims are recorded atomically in the profile database; the
chosen profile ID is then placed in SelectionIntent so the resulting plan stays
fully auditable.

## Process contracts

- CLI output is JSON when `--json` is supplied; stdout remains data and stderr
  remains diagnostics.
- Loopback HTTP uses the same serialization records and binds to `127.0.0.1` by
  default.
- Browser cross-origin access is off by default and requires one exact
  `--allow-origin`; the API never emits a wildcard implicitly.
- Secrets are never returned from catalog, profile, selection, or HTTP routes.
- Credential material is never emitted by the CLI unless a future command has
  an explicit, separately audited execution boundary.
