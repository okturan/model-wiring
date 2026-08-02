## Why

The current selector exposes thousands of models as one alphabetized list, so provider readiness, authentication, and execution support are reduced to repeated row badges instead of guiding the user's first decision. Atlas demonstrates the failure sharply: only one provider and three models are runnable, yet the opening screen defaults to 5,952 mostly unusable model rows.

## What Changes

- Make provider selection the first explicit state in the shared selection controller, with provider-level counts, search matches, authentication state, and application route readiness.
- Show models only within the active provider; make the highlighted model inspectable immediately and reserve confirmation for an explicit second action.
- Present ready providers first, followed by providers that can be connected and then catalogue-only providers, without implying that catalogue visibility grants execution support.
- Order providers within those actionable groups by a shared popularity rank before falling back to name, while preserving application-owned recommendations.
- Replace the flat ANSI model dump with responsive provider-first navigation: a two-pane provider/model browser on wide terminals and sequential provider/model screens on narrow terminals.
- Replace the browser component's provider dropdown plus global results with the same provider-first hierarchy and keyboard-accessible drill-down.
- Keep selection, authentication, billing provenance, and resolution fail-closed: unsupported providers remain inspectable but cannot produce a selection plan.
- Remove border-led panel composition from the selector surfaces, make direct typing the terminal search input, and reduce first-step keyboard instructions to actions relevant to the focused state.
- Preserve application-owned recommendations so Atlas can promote OpenAI Codex and GPT-5.6 Luna without hiding the rest of the catalogue.

## Capabilities

### New Capabilities

- `provider-first-selection`: Provider discovery, provider-scoped model browsing, readiness/authentication presentation, responsive interaction, and fail-closed route confirmation shared across TUI and web surfaces.

### Modified Capabilities

None. This repository has no existing OpenSpec capability specifications.

## Impact

- Shared public view and controller contracts in `model_provider_surfaces.controller`.
- ANSI rendering and terminal input behavior in `ansi.py` and `tui.py`.
- Framework-free web component markup, behavior, and styling.
- Existing CLI rendering behavior and package exports.
- Atlas's imported selector, promoted Codex defaults, and terminal onboarding flow.
- Controller, renderer, web-contract, integration, and real-PTY regression tests.
