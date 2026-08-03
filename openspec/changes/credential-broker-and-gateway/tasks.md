## 1. Terms posture

- [ ] 1.1 Add `terms_posture` to `AccessRoute` with values `first_party_only`, `third_party_permitted`, `unverified`, defaulting to `unverified`, serialized by `to_dict` and readable from overlay `access_routes` data
- [ ] 1.2 Write failing tests first: an undeclared route reports `unverified`; an overlay-declared posture is reported; the value survives a `to_dict`/`from_dict` round trip
- [ ] 1.3 Gate driver availability in `LoginBroker`: `first_party_only` and `unverified` offer delegated import only; requesting `oauth_pkce` or `oauth_device` on such a route raises naming the posture; `third_party_permitted` offers all three
- [ ] 1.4 Write failing tests first for each gating case, including that an unverified route behaves exactly as first-party-only
- [ ] 1.5 Add a test asserting the built distribution contains no provider OAuth client identifier, so the no-embedded-identity rule is enforced mechanically rather than by discipline
- [ ] 1.6 Surface posture in `ProviderView` and the ANSI connect view, and in the web picker's provider rows

## 2. Credential broker

- [ ] 2.1 Create `model_wiring.broker` with a `CredentialBroker` wrapping `AuthBroker`, holding `ProfileRegistry.refresh_lease()` across each refresh
- [ ] 2.2 Write failing tests first for concurrent refresh: two brokers over one store, exactly one token request issued, the loser re-reads the refreshed material
- [ ] 2.3 Implement refresh-ahead-of-expiry with a configurable skew; test that a credential inside the skew refreshes and one outside it issues no request
- [ ] 2.4 Implement `disable(profile_id)` and `enable(profile_id)`; test that a disabled profile is not leased, not refreshed, and skipped by resolution, and that re-enabling restores it with material intact
- [ ] 2.5 Implement `snapshot()` returning redacted state; test that stored secret values appear nowhere in the serialized payload
- [ ] 2.6 Test that a refresh raising mid-flight leaves prior material intact and releases the lease

## 3. Credential precedence

- [ ] 3.1 Implement `resolve()` following the documented chain: runtime-supplied, explicitly selected, highest-priority enabled stored, discovered environment, then fail
- [ ] 3.2 Write failing tests first for each rung, including that a runtime credential outranks stored profiles and is never written to a store
- [ ] 3.3 Enforce that billing kinds never cross silently; test that requesting a subscription credential with only a metered profile present fails naming the unavailable billing kind
- [ ] 3.4 Test that exhausting the chain fails before any network call and names which candidates were considered

## 4. Gateway transport

- [ ] 4.1 Create `model_wiring.gateway` binding a loopback interface, with a bearer token generated at startup and required on every request
- [ ] 4.2 Write failing tests first: a request with no token and a request with a wrong token both return 401 without reading a credential or contacting a provider; a correct token proceeds
- [ ] 4.3 Implement provider-shaped route registration as data, so a provider's routes are declared rather than hardcoded per provider
- [ ] 4.4 Implement egress credential injection via the broker; test that a client sending only the gateway token produces an outbound request carrying the provider credential, and that no credential appears in the client's response
- [ ] 4.5 Implement streaming passthrough with no full-body buffering; test with a chunked fake provider that chunks are forwarded as they arrive rather than after completion
- [ ] 4.6 Test that distinctive prompt text appears in no log record, metric, or stored artifact after a request completes
- [ ] 4.7 Return provider status and body verbatim with no retry, fallback, or translation; test with a 429 that the client sees 429 and no second request is made
- [ ] 4.8 Test that an unknown path returns 404 without contacting a provider, and that a tool-use response is returned as-is with no further action

## 5. Wiring and surfaces

- [ ] 5.1 Add bearer authentication to the existing `serve` API (**BREAKING**), generating the token at startup and printing it once
- [ ] 5.2 Add a `gateway` CLI subcommand that starts the gateway and prints its base URL and token
- [ ] 5.3 Add a gateway-backed probe driver path so M4 can verify a credential through a real authenticated call
- [ ] 5.4 Export the new public API from `model_wiring.__init__` and keep `__all__` sorted

## 6. Verification

- [ ] 6.1 Run both suites, Ruff check and format, compileall, `node --check`, and the workspace lock check
- [ ] 6.2 Re-verify the Atlas integration: 137 tests, `atlas/build.py`, UI tests and production build
- [ ] 6.3 Update README with the gateway integration story and the breaking `serve` change
- [ ] 6.4 Confirm CI passes on both Linux and Windows
