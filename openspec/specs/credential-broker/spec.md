# credential-broker Specification

## Purpose
TBD - created by archiving change credential-broker-and-gateway. Update Purpose after archive.
## Requirements
### Requirement: Single canonical refresh writer

The broker SHALL be the only component that writes refreshed credential material, and SHALL hold the profile's refresh lease for the duration of a refresh so that concurrent processes cannot refresh the same profile in parallel.

#### Scenario: Two processes refresh the same profile at once
- **WHEN** two brokers sharing one profile store both find a credential due for refresh
- **THEN** exactly one performs the refresh and writes the result, and the other waits, re-reads, and returns the refreshed material without issuing its own token request

#### Scenario: A refresh fails midway
- **WHEN** a refresh raises before new material is stored
- **THEN** the previously stored material is left intact and the lease is released

### Requirement: Refresh happens ahead of expiry

The broker SHALL refresh credentials before they expire, using a configurable skew, so that a caller leasing a credential does not encounter an expired token.

#### Scenario: A token is inside the refresh skew
- **WHEN** the broker evaluates a credential whose expiry is nearer than the skew
- **THEN** it refreshes the credential and stores the result before any caller leases it

#### Scenario: A token is nowhere near expiry
- **WHEN** the broker evaluates a credential expiring well beyond the skew
- **THEN** no token request is made

### Requirement: Credentials can be disabled at runtime

The broker SHALL support disabling and re-enabling a credential profile without deleting it, and a disabled profile SHALL NOT be leased, refreshed, or selected by the precedence chain.

#### Scenario: A profile is disabled while in use
- **WHEN** a caller disables a profile and then requests a credential for its provider
- **THEN** the disabled profile is not returned, and resolution continues to the next candidate in the precedence chain

#### Scenario: A disabled profile is re-enabled
- **WHEN** a previously disabled profile is re-enabled
- **THEN** it becomes eligible for leasing and refresh again, with its stored material unchanged

### Requirement: Clients receive redacted snapshots

Any broker state a client can observe SHALL exclude secret material. Snapshots SHALL carry profile identity, provider, auth kind, billing kind, enabled state, expiry, and last probe outcome, and SHALL NOT carry access tokens, refresh tokens, API keys, or credential bundle values.

#### Scenario: A client reads broker state
- **WHEN** a client requests the broker's view of stored credentials
- **THEN** the serialized result contains no secret values, verified by asserting the stored secrets do not appear anywhere in the payload

### Requirement: Documented credential precedence

Credential resolution SHALL follow this order and SHALL fail with a stated reason before any network call if no candidate qualifies: a runtime-supplied credential that is never persisted, then an explicitly selected profile, then the highest-priority enabled stored profile for the provider, then a discovered environment profile.

#### Scenario: A runtime credential outranks stored profiles
- **WHEN** a caller supplies a credential at call time and a stored profile also exists
- **THEN** the runtime credential is used and is not written to any store

#### Scenario: Nothing qualifies
- **WHEN** no runtime credential, selected profile, enabled stored profile, or environment profile exists for a provider
- **THEN** resolution fails naming which candidates were considered, and no request is sent

### Requirement: Billing kinds never cross silently

Resolution SHALL NOT substitute a profile of one billing kind for another. A subscription credential SHALL NOT be used where a metered API credential was requested, nor the reverse, unless the caller explicitly selects it.

#### Scenario: The requested billing kind is unavailable
- **WHEN** a caller requires a subscription credential and only a metered API profile exists
- **THEN** resolution fails rather than falling back, and the failure names the billing kind that was unavailable

