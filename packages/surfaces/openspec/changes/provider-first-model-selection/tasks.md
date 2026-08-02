## 1. Lock the provider-first contract

- [x] 1.1 Add controller regression tests for provider-first initial state, readiness ordering, provider drill-down, provider-aware search counts, immediate model preview, and fail-closed resolution.
- [x] 1.2 Add ANSI/TUI regression tests for wide and narrow layouts, contextual controls, border-free composition, and provider/model navigation.
- [x] 1.3 Add Web Component contract tests for provider rows, provider-scoped requests, keyboard drill-down, access-state vocabulary, and removal of the provider dropdown.

## 2. Implement shared provider navigation

- [x] 2.1 Add `ProviderView` and provider/focus state to the public controller view contract and exports.
- [x] 2.2 Implement provider readiness aggregation, useful ordering, two-cursor navigation, activation/back transitions, and provider-aware search.
- [x] 2.3 Derive model preview from the cursor while preserving explicit model choice, configuration defaults, exact-plan resolution, and unsupported-route rejection.

## 3. Rebuild terminal surfaces

- [x] 3.1 Replace the flat model screen with wide provider/model composition and narrow sequential stages without persistent divider borders.
- [x] 3.2 Update TUI key handling for focus, drill-down, back, contextual search, and explicit choose/confirm behavior.
- [x] 3.3 Preserve control-character sanitization, cbreak terminal handling, and correct alternate-screen cleanup.

## 4. Rebuild the browser surface

- [x] 4.1 Replace the provider `<select>` with an accessible provider collection that communicates counts and access state.
- [x] 4.2 Fetch and render only active-provider models, support keyboard navigation/back behavior, and retain secret-free plan resolution.
- [x] 4.3 Replace border-led CSS composition with responsive provider/model stages and visible focus states.

## 5. Integrate and document

- [x] 5.1 Update package documentation, surface contract, CLI reference behavior, and exports for provider-first navigation.
- [x] 5.2 Exercise the editable surfaces package through Atlas, retaining OpenAI Codex/Luna promotion and Atlas's fail-closed executor policy.
- [x] 5.3 Update Atlas tests and onboarding copy where the former all-model scope or shortcuts are asserted.

## 6. Verify and iterate

- [x] 6.1 Run the shared package tests and inspect plain wide/narrow renders.
- [x] 6.2 Run Atlas's Python tests and UI tests/build against the editable package.
- [x] 6.3 Exercise the real Atlas picker in a PTY, inspect the rendered provider-first flow, and create a follow-up OpenSpec proposal if verification exposes a design defect.
- [x] 6.4 Validate the OpenSpec change and audit every requirement against current code and runtime evidence.

## 7. Apply interaction feedback

- [x] 7.1 Add failing regressions for popularity-ranked provider discovery and direct terminal typing, editing, clearing, and quitting.
- [x] 7.2 Ship shared provider popularity metadata and use it consistently in kit API/CLI, Python surfaces, and the Web Component.
- [x] 7.3 Replace slash-gated terminal search with direct typing and contextual Backspace/Escape behavior; update visible instructions and documentation.
- [x] 7.4 Run package, Atlas, real-PTY, web, OpenSpec, formatting, and production-build verification.
