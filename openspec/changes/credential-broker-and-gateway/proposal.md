## Why

Model Wiring stops one step short of its own promise. It can discover a provider, explain how to connect it, run the login, store the credential, and probe it — then hands the application a secret-free `SelectionPlan` and a lease and says "you write the API call." An app integrating the kit still has to implement provider-specific inference, so "your app now has AI access through the user's existing subscription" is not yet true out of the box.

Two other gaps follow from the same place. Credentials refresh lazily inside whichever process happens to lease first, which is wrong once several clients share one profile store; and there is no way to say, per provider, whether a third-party client may use a subscription at all — a judgement that differs by provider and today would have to be baked in silently.

## What Changes

- Add a **credential broker**: the single canonical writer for refresh tokens, with background refresh ahead of expiry, runtime credential disablement, and redacted snapshots for every client. Built on `ProfileRegistry.refresh_lease()`, which already provides cross-process single-writer semantics.
- Add a **local inference gateway**: bearer-protected loopback routes shaped like the providers' own APIs, so an application points its existing OpenAI or Anthropic SDK at a localhost base URL and needs no other change. The gateway resolves a credential, injects it at egress, and streams the response through without buffering.
- Add an explicit **credential precedence chain** so resolution order is a documented contract rather than an incidental consequence of profile priority.
- Add **`terms_posture` to access routes** (`first_party_only`, `third_party_permitted`, `unverified`), gating which login drivers are offered and shown to the person signing in before they connect.
- **BREAKING**: `serve` gains a required bearer token. A loopback service that can spend a user's subscription must not be reachable by any process on the machine.

Explicitly out of scope: agent loops, tool execution, conversation storage, prompt templates, and retry or fallback policy. The gateway forwards one request and returns one response.

No embedded first-party client IDs. Credentials come from delegated import of a provider CLI's existing sign-in, or from an OAuth client the consuming application registers itself. A borrowed client ID would put a revocable shared identity — and the resulting risk — onto the end users of every application built on this kit, who never saw the decision.

## Capabilities

### New Capabilities
- `credential-broker`: canonical refresh ownership, background refresh, disablement, redacted snapshots, and the documented precedence chain
- `inference-gateway`: authenticated loopback routes shaped like provider APIs, egress credential injection, streaming passthrough, and the content-handling guarantees that come with proxying user data
- `terms-posture`: per-route declaration of whether a third-party client may use a provider's subscription, and how that gates and is surfaced

### Modified Capabilities
<!-- No existing openspec/specs/ entries; this is the first change in a freshly initialised project. -->

## Impact

- **New modules**: `model_wiring.broker`, `model_wiring.gateway`; `terms_posture` on `model_wiring.access.AccessRoute`
- **Changed**: `api.py` gains bearer authentication (breaking for existing `serve` callers); `login.py` filters drivers by posture; `probe.py` gains a gateway-backed driver path
- **Surfaces**: connect views show the posture before a user signs in
- **Security**: the gateway is the first component that sees user prompts and completions in transit — it must never log or persist bodies
- **Consumers**: Atlas continues to use catalogue and selection only; the gateway is additive
