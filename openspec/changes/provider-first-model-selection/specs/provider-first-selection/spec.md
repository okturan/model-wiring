## ADDED Requirements

### Requirement: Provider is the first interactive decision
The interactive selector SHALL open with provider focus and SHALL NOT expose a global cross-provider model list as the primary browsing surface.

#### Scenario: Opening a mixed catalogue
- **WHEN** a catalogue contains multiple providers and models
- **THEN** the initial view presents provider rows and the model candidates are scoped to the highlighted provider

#### Scenario: Entering a provider
- **WHEN** the user confirms a highlighted provider
- **THEN** focus advances to models belonging only to that provider

### Requirement: Provider rows communicate actionable readiness
The selector SHALL derive a provider readiness state from application model support, declared authentication, and enabled compatible profiles, and SHALL distinguish `ready`, `connect`, and `catalog` states.

#### Scenario: Runnable authenticated provider
- **WHEN** a provider has a supported model and an enabled compatible profile
- **THEN** the provider is marked `ready` with its runnable and total model counts

#### Scenario: Provider needs credentials
- **WHEN** a provider has a supported model, declares authentication, and has no enabled compatible profile
- **THEN** the provider is marked `connect` and its authentication requirement is shown

#### Scenario: Application has no executor
- **WHEN** no model under a provider passes the application's route-support predicate
- **THEN** the provider is marked `catalog`, remains inspectable, and is not represented as connectable or runnable

### Requirement: Useful providers precede catalogue inventory
The selector SHALL order ready providers before connectable providers and catalogue-only providers, then application-owned preferred providers, then shared popularity rank, and finally stable provider name within each readiness group.

#### Scenario: Atlas opens its catalogue
- **WHEN** OpenAI Codex is the only ready provider and is application-preferred
- **THEN** it is the first provider rather than the first alphabetically named proxy provider

#### Scenario: No application preference applies
- **WHEN** several providers have the same readiness and no application preference
- **THEN** providers in the shipped popularity list precede unranked providers and preserve popularity order

#### Scenario: Provider has no popularity rank
- **WHEN** a custom or newly catalogued provider is not in the shipped popularity list
- **THEN** it remains discoverable after ranked peers and is ordered stably by provider name and identifier

### Requirement: Search preserves provider hierarchy
The selector SHALL search provider identity and provider-owned model identity/name, SHALL expose per-provider match counts, and SHALL keep resulting models scoped to one provider.

#### Scenario: Model name matches across providers
- **WHEN** a query matches models offered by several providers
- **THEN** matching providers expose their own match counts and selecting one shows only its matching models

#### Scenario: Search is cleared
- **WHEN** the user clears the query
- **THEN** normal provider ordering and the active provider's full model list are restored

### Requirement: Highlighted models are inspectable before selection
The selector SHALL derive preview details from the highlighted provider-scoped model without treating that model as a confirmed selection.

#### Scenario: Moving through provider models
- **WHEN** the model cursor moves to a different model
- **THEN** its name, identifier, capabilities, limits, modes, and route support are shown immediately while the selection intent remains unchanged

#### Scenario: Choosing and confirming a model
- **WHEN** the user chooses a highlighted model and then explicitly confirms a ready route
- **THEN** the resulting plan refers to that exact provider/model and configured access route

### Requirement: Unsupported execution remains fail-closed
The selector MUST reject resolution for every model rejected by the consuming application's route-support predicate, regardless of catalogue visibility or authentication state.

#### Scenario: Inspecting a catalogue-only model
- **WHEN** a user browses and chooses a model marked catalogue-only
- **THEN** route confirmation remains unavailable and resolution returns the application support reason

### Requirement: Terminal interaction is responsive and contextual
The ANSI/TUI surface SHALL support provider and model navigation at wide and narrow terminal sizes, SHALL preserve immediate character input without terminal line drift, and SHALL display only controls relevant to the focused state.

#### Scenario: Wide terminal
- **WHEN** the terminal is wide enough for simultaneous provider and model content
- **THEN** both collections are rendered with whitespace and headings rather than a persistent vertical divider

#### Scenario: Narrow terminal
- **WHEN** the terminal cannot present both collections legibly
- **THEN** the surface presents provider and model stages sequentially with a discoverable back action

#### Scenario: Initial footer
- **WHEN** provider discovery has focus and no model is chosen
- **THEN** mode, tier, variant, and access shortcuts are omitted

#### Scenario: User starts typing
- **WHEN** provider or model browsing has focus and the user types printable text
- **THEN** the visible query updates and results filter immediately without a slash activation key

#### Scenario: User edits or clears search
- **WHEN** a query is active and the user presses Backspace or Escape
- **THEN** Backspace removes the final query character and Escape clears the query before navigating back or closing

### Requirement: Browser surface follows the same drill-down
The Web Component SHALL present a keyboard-accessible provider collection before provider-scoped model results and SHALL derive access readiness without handling token material.

#### Scenario: Browser provider selection
- **WHEN** a user activates a provider row
- **THEN** the component fetches models with that provider explicitly set and moves focus to the provider's results

#### Scenario: Browser keyboard navigation
- **WHEN** focus is within provider or model results and the user presses an arrow key
- **THEN** focus moves within that collection without leaving the selection flow

### Requirement: Application recommendations do not hide catalogue scope
The selector SHALL allow a consuming application to promote providers and models while preserving complete provider counts, model counts, and catalogue inspection.

#### Scenario: Preferred Codex route
- **WHEN** Atlas supplies OpenAI Codex and GPT-5.6 Luna as preferred choices
- **THEN** those choices appear first in their respective provider-scoped views and all other providers remain discoverable
