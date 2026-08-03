# terms-posture Specification

## Purpose
TBD - created by archiving change credential-broker-and-gateway. Update Purpose after archive.
## Requirements
### Requirement: Access routes declare a terms posture

Every access route SHALL carry a `terms_posture` of `first_party_only`, `third_party_permitted`, or `unverified`, describing whether a third-party client may use that provider's subscription credential. Routes that do not declare one SHALL default to `unverified`.

#### Scenario: A route omits the field
- **WHEN** an access route is derived or declared without `terms_posture`
- **THEN** it reports `unverified`

#### Scenario: An overlay declares a posture
- **WHEN** an overlay sets a route's `terms_posture`
- **THEN** the declared value is reported in place of the default

### Requirement: Posture gates which login drivers are offered

Login driver availability SHALL follow the route's posture. A `first_party_only` route SHALL offer delegated import only and SHALL NOT offer a driver that authenticates with a client identity of our own. An `unverified` route SHALL be treated as `first_party_only` for this purpose. A `third_party_permitted` route MAY additionally offer browser and device sign-in.

#### Scenario: A first-party-only provider is connected
- **WHEN** a user begins login for a route marked `first_party_only`
- **THEN** only delegated import is offered, and requesting a browser or device flow for that route fails naming the posture

#### Scenario: An unverified provider is connected
- **WHEN** a user begins login for a route with no verified posture
- **THEN** it behaves as `first_party_only`, so the conservative path is what an undeclared route yields

#### Scenario: A permitting provider is connected
- **WHEN** a user begins login for a route marked `third_party_permitted` and an OAuth configuration is present
- **THEN** browser and device sign-in are offered alongside delegated import

### Requirement: No first-party client identity ships with the kit

The package SHALL NOT contain a client identifier issued to another organisation's own tooling. An OAuth-capable route SHALL take its client identity from overlay data supplied by the consuming application.

#### Scenario: The shipped package is inspected
- **WHEN** the distributed package is searched for provider OAuth client identifiers
- **THEN** none are present, and every OAuth route obtains its client identity from supplied configuration

#### Scenario: An application supplies its own client
- **WHEN** a consuming application declares an OAuth configuration including its own registered client identifier
- **THEN** the login drivers use it unchanged

### Requirement: Posture is shown before a user signs in

A route's posture SHALL be visible in the connect view of every surface, so the person authenticating can see the terms position before authorising.

#### Scenario: A user opens a provider's connect view
- **WHEN** a connect view is rendered for a route
- **THEN** it states the posture and, for `first_party_only` and `unverified`, states that only an existing sign-in from the provider's own tool will be used

