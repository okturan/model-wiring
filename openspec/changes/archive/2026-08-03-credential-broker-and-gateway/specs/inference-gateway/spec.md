## ADDED Requirements

### Requirement: Provider-shaped loopback routes

The gateway SHALL expose routes shaped like the providers' own inference APIs, so that an application configured with the gateway's base URL and its existing provider SDK requires no other code change.

#### Scenario: An application swaps only its base URL
- **WHEN** a client issues a request to a gateway route using the request body its provider SDK would normally send
- **THEN** the gateway forwards it to that provider and returns the provider's response unchanged in shape

#### Scenario: An unknown route is requested
- **WHEN** a client requests a path the gateway does not expose
- **THEN** the gateway responds 404 without contacting any provider

### Requirement: The gateway binds loopback and requires a bearer token

The gateway SHALL bind only a loopback interface and SHALL require a bearer token on every request, including requests originating from the same machine.

#### Scenario: A request arrives without a token
- **WHEN** a local process calls a gateway route with no Authorization header, or a wrong token
- **THEN** the gateway responds 401 and does not contact any provider or read any credential

#### Scenario: A token is presented
- **WHEN** a client presents the token issued at startup
- **THEN** the request proceeds

### Requirement: Credentials are injected at egress

The gateway SHALL resolve a credential through the broker and attach it to the outbound provider request. The client SHALL NOT be required to hold, send, or ever observe the provider credential.

#### Scenario: A client sends no provider credential
- **WHEN** a client calls a gateway route carrying only the gateway bearer token
- **THEN** the outbound provider request carries the resolved provider credential, and the response returned to the client contains no credential material

#### Scenario: No credential resolves
- **WHEN** the precedence chain yields no usable credential for the requested provider
- **THEN** the gateway responds with an error naming what is missing and makes no provider request

### Requirement: Bodies are streamed and never retained

The gateway SHALL stream request and response bodies without buffering them in full, and SHALL NOT log, persist, or otherwise retain body content. It SHALL record only non-content metadata: profile identity, provider, status, byte counts, and duration.

#### Scenario: A streaming completion passes through
- **WHEN** a client requests a streamed completion
- **THEN** chunks are forwarded as they arrive rather than after the response completes

#### Scenario: Logging is inspected after a request
- **WHEN** a request containing distinctive prompt text completes
- **THEN** that text appears in no log record, metric, or stored artifact the gateway produced

### Requirement: Provider failures are returned verbatim

The gateway SHALL NOT add retry, fallback, or error translation. A provider's status code and error body SHALL reach the client unchanged so the application's own handling still applies.

#### Scenario: A provider returns a rate limit error
- **WHEN** the provider responds 429
- **THEN** the client receives 429 with the provider's body, and the gateway does not retry or switch credentials

### Requirement: The gateway executes one request per call

The gateway SHALL forward exactly one provider request per client request. It SHALL NOT run agent loops, execute tools, store conversations, or inject prompt content.

#### Scenario: A request would trigger a tool call
- **WHEN** a provider response indicates the model requested a tool
- **THEN** the gateway returns that response to the client and takes no further action
