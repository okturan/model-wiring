## Context

Model Wiring is a development kit that other applications import, not a tool anyone runs standalone. Its promise to an integrating developer is: your app gains AI access through the credentials your user already has. Today it delivers everything up to and including a verified credential, then stops — the developer writes the provider call themselves.

The existing pieces this change builds on already work:

- `ProfileRegistry.refresh_lease()` acquires a `BEGIN IMMEDIATE` SQLite lease with an owner token and TTL. This is cross-process, not merely cross-thread, and is exactly the single-writer primitive a broker needs.
- `AuthBroker.lease()` yields a short-lived `CredentialLease` carrying either wiped-on-exit material or a delegate name, and already refreshes expired OAuth material through a per-provider driver.
- `CredentialPool` implements `fill_first`, `round_robin`, and `least_used` claiming.
- `CredentialProfile.enabled` is honoured by `AuthBroker.lease()`.
- Every `to_dict()` in the package is secret-free, so "redacted snapshot" is the existing house style rather than a new concept.

Two constraints frame every decision below. First, the kit is not an agent harness: no loops, tools, sessions, or prompt templates. Second, until now no component has ever seen user content — the gateway breaks that, and must do so on explicit terms.

## Goals / Non-Goals

**Goals:**
- An application changes a base URL and gets working inference through the user's own subscription.
- One canonical refresh writer across every process sharing a profile store; no duplicate refreshes, no lost-update races.
- Resolution order for credentials is a documented, testable contract.
- Whether a third-party client may use a given provider's subscription is per-provider data, verifiable and visible to the person signing in.

**Non-Goals:**
- Agent loops, tool execution, conversation storage, prompt templates, retries, fallback chains.
- Request or response transformation between provider dialects. The gateway forwards a request shaped for provider X to provider X.
- A remote or multi-tenant broker. This is a loopback service for one machine and one user.
- Shipping any first-party client ID.

## Decisions

### 1. The gateway proxies rather than adapting per provider

**Chosen:** expose loopback routes shaped like each provider's own API and forward them, injecting credentials at egress.

**Alternative rejected:** a `client_for(plan, lease)` adapter returning a configured provider SDK object. That requires the kit to depend on every provider SDK, track their releases, and re-expose their surfaces — and it only serves Python. A proxy is language-agnostic, so a TypeScript or Go application benefits identically, which matters for a kit whose consumers are other people's apps. It also means the application keeps using the official SDK it already knows.

### 2. Credentials are injected at egress; the client never holds them

The application authenticates to the gateway with a local bearer token and never sees the provider credential. This mirrors the pattern Anthropic's own vault uses for managed agents — placeholder in, real secret substituted after the request leaves — and it means a compromised client process leaks only a revocable local token.

### 3. Streaming is passthrough, and bodies are never retained

Request and response bodies are streamed chunk-by-chunk with no buffering, no logging, and no persistence. The gateway records only non-secret, non-content metadata: profile id, provider, status code, byte counts, duration. This is the explicit stance the proxy boundary demands; without it the kit would silently become the most sensitive component in a consumer's stack.

### 4. The broker owns refresh; leases stay the read path

Refresh moves out of the lease path into a broker that holds `refresh_lease()` for the duration, refreshes ahead of expiry rather than after it, and writes once. Clients continue to obtain credentials through `AuthBroker.lease()` and see redacted snapshots. This also removes the surprise M4 uncovered: leasing an expired token silently attempted a refresh, which made a read-only probe mutate state.

### 5. Precedence is an explicit ordered chain

```
runtime-supplied credential (never persisted)
  -> explicitly selected profile
  -> highest-priority stored profile for the provider
  -> discovered environment profile
  -> fail, naming the reason, before any network call
```

Adapted from the chain Oh My Pi documents, expressed in this kit's profile vocabulary. Crossing billing kinds is never a fallback: a subscription profile and a metered API profile are different economic paths, and moving between them is a user decision, not a retry.

### 6. `terms_posture` is per-route data, not global policy

Providers differ on whether a third-party client may use a subscription. Encoding one global stance would be wrong for most providers and silently wrong for at least one. Each route declares `first_party_only`, `third_party_permitted`, or `unverified`; the value gates which drivers are offered and is shown at connect time. `unverified` is the default, and it offers delegated import only — the conservative option is what you get by not deciding.

### 7. Bearer auth on `serve` is required, not optional

**BREAKING.** A loopback port that can spend someone's subscription is not safe merely because it binds `127.0.0.1`; any local process, including a browser page via DNS rebinding, can reach it. The token is generated on start, printed once, and required on every request. Existing callers must pass it.

## Risks / Trade-offs

- **The gateway sees prompts and completions.** → Streaming passthrough, no body logging, no persistence, metadata-only records. Stated in the spec as a requirement, not a convention, and covered by a test asserting bodies never reach logs.
- **A bearer token on loopback is still a local secret.** → Generated per start, never written to disk by default, owner-only if persisted. Compromise costs a revocable local token, not the provider credential.
- **Background refresh could stampede across processes.** → Every refresh path goes through `refresh_lease()`; a loser waits and re-reads rather than refreshing in parallel.
- **Proxying makes us liable for availability.** → The gateway adds no retry or fallback policy; a provider error is returned verbatim so the application's own handling still applies.
- **`terms_posture` values could go stale as providers change terms.** → It is overlay data with a documented source, correctable without a release; `unverified` degrades safely to delegated-only.
- **Breaking `serve` affects existing callers.** → Only Atlas consumes it locally, and it uses catalogue and selection endpoints; the change is a documented one-line addition.

## Migration Plan

Additive except for `serve` authentication. Ship broker and precedence first (no public surface change), then the gateway behind its own subcommand, then posture gating. Rollback is per-module: the gateway is a separate entry point and can be withheld without touching the broker.

## Open Questions

- Which providers get verified `terms_posture` values in the first pass, and from which published source for each.
- Whether the gateway should expose a provider-neutral route in addition to provider-shaped ones. Deferred: provider-shaped is what makes the base-URL swap work, and a neutral dialect is a translation layer this change explicitly excludes.
