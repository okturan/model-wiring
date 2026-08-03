# Provider onboarding architecture

Status: Draft. Owns the shared machinery; per-provider details live in the
`flow-*.md` specs.

## Positioning

Model Wiring is a library plus optional surfaces, embeddable in anything that
consumes LLMs — including other people's agent harnesses. It therefore ships
**flows, not policy**: the kit knows how to connect a provider; the embedding
application decides when, and with which UI. Nothing here starts an agent
loop, executes tools, or proxies traffic.

## What exists today (the chassis)

| Capability | Where | State |
| --- | --- | --- |
| Auth vocabulary: `AUTH_KINDS` (api_key, bearer, credential_bundle, oauth, delegated, anonymous), `BILLING_KINDS` (api, subscription, marketplace, local, unknown) | `contracts.py` | Shipped |
| `AuthMethod(kind, billing_kinds, label, env, metadata)` on every `ProviderSpec` | `contracts.py` | Shipped, sparsely populated |
| `CredentialProfile` (auth_kind, billing_kind, secret_ref/store, account_label, priority, scopes) | `contracts.py` | Shipped |
| Non-secret profile registry (SQLite, single-writer `refresh_lease`) | `profiles.py` | Shipped |
| Secret stores: memory, environment (read-only), OS keyring; wiping `CredentialMaterial`; short-lived `CredentialLease` | `auth.py` | Shipped |
| `AuthBroker` with expiry-driven refresh through per-provider `RefreshDriver` | `auth.py` | Shipped |
| Generic OAuth 2.0: PKCE authorization-code, RFC 8628 device flow, refresh with refresh-token retention | `oauth.py` | Shipped |
| Environment bundle discovery → profiles | `discovery.py` | Shipped |
| Credential pools and rotation strategies | `pool.py` | Shipped |

## What is missing (the actual product gap)

1. **Shipped route data** — no provider in the catalog carries populated
   `auth_methods` describing its real flows.
2. **Login drivers** — `OAuthClient` is protocol plumbing; nothing orchestrates
   begin → prompt → complete for a named provider.
3. **App-facing login session API** — no serializable prompt objects a TUI,
   web page, or headless script can render.
4. **Loopback redirect helper** — PKCE needs a localhost callback listener;
   every consumer would have to write one today.
5. **CLI verbs** — no `model-wiring login / logout / auth status / probe`.
6. **Probes and entitlement labels** — nothing verifies that a stored
   credential still works or records which entitlement class it carries.
7. **Surface affordances** — the pickers can show readiness but offer no
   connect flow.

## Requirements

### R1 — Shipped auth routes

Populate `ProviderSpec.auth_methods` for the popular-provider set through the
same mechanism as popularity: a shipped table, overridable by catalog
overlays, extendable by consumers. `AuthMethod.metadata` carries the
non-secret flow parameters: driver id, endpoints, client identity policy,
documentation URL. The catalog answer to "how could I connect provider X?"
must be complete for every catalogued provider, even when the honest answer
is only "API key via env var" or "no known route".

### R2 — Login drivers and sessions

A `LoginDriver` per flow family (oauth_pkce, oauth_device, api_key_paste,
delegated_import, credential_bundle) with a serializable state machine:

```text
begin(provider, route) -> LoginSession
LoginSession.prompt is one of:
  OpenUrlPrompt(url, expected_redirect)          # PKCE
  UserCodePrompt(verification_uri, user_code)    # device flow
  SecretPrompt(fields, masked=True)              # API key / bundle
  ChoicePrompt(candidates)                       # delegated import
advance(session, input) -> LoginSession | LoginResult
LoginResult -> CredentialProfile + material stored via SecretStore
```

Prompts are plain data so any surface — ANSI TUI, web component, headless
CLI, or a host harness's own UI — can render them. Drivers never print, never
open browsers, and never block on stdin themselves.

### R3 — Loopback redirect helper

One stdlib implementation: binds `127.0.0.1` on an ephemeral port, accepts a
single state-checked callback, returns the authorization code, then shuts
down. No TLS, no external interfaces, no logging of query strings. Offered as
a convenience; consumers may substitute their own redirect handling.

### R4 — CLI verbs

`model-wiring login <provider> [--route ID] [--store NAME]`,
`model-wiring logout <profile>`, `model-wiring auth status [--json]`,
`model-wiring probe <profile>`. The CLI is the reference consumer of R2/R3
and doubles as the headless onboarding path.

### R5 — Probes and entitlement

`probe(profile) -> ProbeResult(state, entitlement_class, account_fingerprint)`
where state ∈ {ready, expired, unavailable, policy_denied}. Probes are
read-only, send no user content, and record results as non-secret profile
metadata (`last_probe_at`, `last_probe_state`). Account fingerprints are safe
identifiers only (e.g. a workspace slug), never tokens or full account IDs.

### R6 — Precedence and no-silent-crossover

Resolution order, adapted from the OMP-observed chain to profile terms:

```text
runtime-supplied credential (never persisted)
  -> explicitly selected profile
  -> highest-priority stored profile for the provider
  -> discovered environment profile
  -> fail with the reason, before any network call
```

Crossing billing kinds (subscription ↔ api) is never a fallback; it is a new
profile selection made by the user. Refresh failures must be distinguishable
from quota, policy, model, and network errors.

### R7 — Surface affordances

Provider readiness becomes three states: `ready` (usable profile),
`connectable` (routes exist, no profile), `catalog` (no known route). The
ANSI picker renders a connect view from R2 prompts; the web component emits a
`model-wiring-connect` intent event with the chosen provider/route and renders
prompts the host feeds back in. Controller API mirrors both.

### R8a — OAuth endpoints are data, never code (implemented)

`oauth_pkce` and `oauth_device` read the authorization server from the route's
`metadata.oauth`, so this repository ships **no** third-party endpoint or
client id it cannot verify. A consumer — or a future overlay, once the values
are pinned to first-party sources — supplies them:

```json
{
  "providers": {
    "example": {
      "access_routes": [
        {
          "id": "subscription",
          "kind": "oauth",
          "billing_kind": "subscription",
          "label": "Example subscription",
          "driver": "oauth_pkce",
          "metadata": {
            "oauth": {
              "client_id": "<public client id from the provider's own tooling>",
              "authorization_endpoint": "https://example/authorize",
              "token_endpoint": "https://example/token",
              "scopes": ["openid"],
              "device_authorization_endpoint": "https://example/device"
            }
          }
        }
      ]
    }
  }
}
```

A route missing any required field fails with a message naming what is absent,
rather than attempting a request against a guessed endpoint.

### R8 — Client identity policy

Each flow spec declares one of, in order of preference:

1. `delegated_import` — reuse a login the provider's own tool created.
2. `official_public_client` — a public (secretless) client ID that the
   provider documents or ships in its own public tooling; pinned by source.
3. `own_client` — a client we register where providers allow third-party apps.

The kit never embeds confidential client secrets. Every route's spec states
which account pays and which entitlement it yields, so the embedding app can
display it; terms-of-service posture is recorded per provider in its flow
spec and surfaced to the user rather than hidden.

## Storage and security invariants

- Secrets exist only inside `SecretStore` backends and wiped
  `CredentialMaterial`/`CredentialLease` lifetimes; `profiles.sqlite3` stays
  metadata-only.
- Refresh remains single-writer via `ProfileRegistry.refresh_lease`.
- No secret ever enters catalog data, events, prompts, logs, UI text, or
  spec examples.
- Delegated imports require an explicit user action; the kit never reads
  another application's credential files silently (see
  [flow-delegated-import](flow-delegated-import.md)).
- OAuth `state` is always checked; verifiers and device codes are wiped after
  use (already implemented in `oauth.py`).

## Milestones

| Milestone | Delivers | Requirements |
| --- | --- | --- |
| M1 Route visibility | Every provider shows its real connect routes in CLI/TUI/web | R1, R7 (display) |
| M2 Keys and imports | API-key paste, env promotion, delegated imports working end to end | R2 (paste/import), R4 |
| M3 Subscription OAuth | Browser (PKCE) and device sign-in drivers, loopback redirect | R2, R3, R8 |
| M4 Trust (implemented) | Probes, entitlement labels, account fingerprints in all surfaces | R5, R6 |

`Prober.probe()` reports the strongest claim the evidence supports. Local
evidence needs no provider call: a disabled profile, a missing secret, a token
past its expiry, and a delegated sign-in whose artifact is gone are all decided
offline. Expiry is read straight from the secret store rather than through a
lease, because leasing would attempt a refresh — which both hides the expiry
and makes a read-only check mutate state.

A stored credential that cannot be checked reports `unknown`, never `ready`.
`PROBE_DRIVERS` ships empty for the same reason the OAuth routes ship no
endpoints: a driver has to call a provider's own API, and this package ships
none it cannot verify. Consumers register drivers for the providers they use.

Outcomes are written back as non-secret profile metadata (`last_probe_state`,
`last_probe_at`, `entitlement_class`, `account_fingerprint`), which is what
lets every surface downgrade a provider that was configured but has since
stopped working.
| M5 Concurrency (deferred) | Single-writer broker daemon / gateway à la OMP, if multi-client demand appears | — |

M1 alone resolves the original complaint: no provider should ever present as
a dead end when the catalog knows how it could be connected.

## Test plan

- Driver contract suite run against every `LoginDriver` (prompt
  serializability, cancellation, wipe-on-abandon).
- `OAuthClient` flows against a fake transport (existing pattern) including
  device-flow pending/slow-down and refresh-token retention.
- Loopback helper: real socket, state mismatch, second-request rejection.
- Import drivers against fixture files, never real credential paths.
- Surfaces: connect-view snapshots in the ANSI tests; connect-intent event in
  web component checks.

## Open questions

- Encrypted file store for headless machines without a keyring (OMP encrypts
  broker snapshots with AES-256-GCM; our keyring extra may not exist in CI).
- Whether pools (`pool.py`) should rotate across accounts within one
  entitlement class automatically (OMP does) or only on explicit opt-in.
- Where probe results should live long-term: profile metadata now, dedicated
  columns once the shape settles.
