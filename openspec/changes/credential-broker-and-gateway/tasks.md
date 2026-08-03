## 1. Terms posture

- [x] 1.1 Add `terms_posture` to `AccessRoute` with values `first_party_only`, `third_party_permitted`, `unverified`, defaulting to `unverified`, serialized by `to_dict` and readable from overlay `access_routes` data
- [x] 1.2 Write failing tests first: an undeclared route reports `unverified`; an overlay-declared posture is reported; the value survives a `to_dict`/`from_dict` round trip
- [x] 1.3 Gate driver availability in `LoginBroker`: `first_party_only` and `unverified` offer delegated import only; requesting `oauth_pkce` or `oauth_device` on such a route raises naming the posture; `third_party_permitted` offers all three
- [x] 1.4 Write failing tests first for each gating case, including that an unverified route behaves exactly as first-party-only
- [x] 1.5 Add a test asserting the built distribution contains no provider OAuth client identifier, so the no-embedded-identity rule is enforced mechanically rather than by discipline
- [x] 1.6 Surface posture in `ProviderView` and the ANSI connect view, and in the web picker's provider rows

## 2. Credential broker

- [x] 2.1 Create `model_wiring.broker` with a `CredentialBroker` wrapping `AuthBroker`, holding `ProfileRegistry.refresh_lease()` across each refresh
- [x] 2.2 Write failing tests first for concurrent refresh: two brokers over one store, exactly one token request issued, the loser re-reads the refreshed material
- [x] 2.3 Implement refresh-ahead-of-expiry with a configurable skew; test that a credential inside the skew refreshes and one outside it issues no request
- [x] 2.4 Implement `disable(profile_id)` and `enable(profile_id)`; test that a disabled profile is not leased, not refreshed, and skipped by resolution, and that re-enabling restores it with material intact
- [x] 2.5 Implement `snapshot()` returning redacted state; test that stored secret values appear nowhere in the serialized payload
- [x] 2.6 Test that a refresh raising mid-flight leaves prior material intact and releases the lease

## 3. Credential precedence

- [x] 3.1 Implement `resolve()` following the documented chain: runtime-supplied, explicitly selected, highest-priority enabled stored, discovered environment, then fail
- [x] 3.2 Write failing tests first for each rung, including that a runtime credential outranks stored profiles and is never written to a store
- [x] 3.3 Enforce that billing kinds never cross silently; test that requesting a subscription credential with only a metered profile present fails naming the unavailable billing kind
- [x] 3.4 Test that exhausting the chain fails before any network call and names which candidates were considered

## 4. Gateway transport

- [x] 4.1 Create `model_wiring.gateway` binding a loopback interface, with a bearer token generated at startup and required on every request
- [x] 4.2 Write failing tests first: a request with no token and a request with a wrong token both return 401 without reading a credential or contacting a provider; a correct token proceeds
- [x] 4.3 Implement provider-shaped route registration as data, so a provider's routes are declared rather than hardcoded per provider
- [x] 4.4 Implement egress credential injection via the broker; test that a client sending only the gateway token produces an outbound request carrying the provider credential, and that no credential appears in the client's response
- [x] 4.5 Implement streaming passthrough with no full-body buffering; test with a chunked fake provider that chunks are forwarded as they arrive rather than after completion
- [x] 4.6 Test that distinctive prompt text appears in no log record, metric, or stored artifact after a request completes
- [x] 4.7 Return provider status and body verbatim with no retry, fallback, or translation; test with a 429 that the client sees 429 and no second request is made
- [x] 4.8 Test that an unknown path returns 404 without contacting a provider, and that a tool-use response is returned as-is with no further action

## 5. Wiring and surfaces

- [x] 5.1 Add bearer authentication to the existing `serve` API (**BREAKING**), generating the token at startup and printing it once
- [x] 5.2 Add a `gateway` CLI subcommand that starts the gateway and prints its base URL and token
- [x] 5.3 Add a gateway-backed probe driver path so M4 can verify a credential through a real authenticated call
- [x] 5.4 Export the new public API from `model_wiring.__init__` and keep `__all__` sorted

## 6. Verification

- [x] 6.1 Run both suites, Ruff check and format, compileall, `node --check`, and the workspace lock check
- [x] 6.2 Re-verify the Atlas integration: 137 tests, `atlas/build.py`, UI tests and production build
- [x] 6.3 Update README with the gateway integration story and the breaking `serve` change
- [ ] 6.4 Confirm CI passes on both Linux and Windows
