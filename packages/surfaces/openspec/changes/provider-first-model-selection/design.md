## Context

`SelectionController` currently materializes one global `_models` list and one model cursor. A provider may constrain `browse()` or `search()`, but provider identity is not navigation state. The ANSI renderer therefore opens on a flat cross-provider list, repeats application support on every row, leaves its detail area empty until a model is explicitly chosen, and exposes model configuration shortcuts before the provider decision exists. The Web Component repeats the same conceptual shape with a provider `<select>` above a global result list.

The catalogue and secret-free selection contracts remain owned by `model-wiring`. This package owns reusable human interaction. Atlas supplies an application-specific `route_support` predicate and preferred Codex routes; only Atlas owns execution.

## Goals / Non-Goals

**Goals:**

- Make provider discovery the first human decision on every interactive surface.
- Aggregate model support, authentication, and profile evidence into provider-level readiness without weakening model-level validation.
- Keep the controller renderer-agnostic and preserve serializable, secret-free views.
- Give wide and narrow terminals deliberate layouts with the same state transitions.
- Let consuming applications promote providers/models while keeping the full catalogue inspectable.
- Keep unsupported routes impossible to resolve.

**Non-Goals:**

- Add inference adapters or make Atlas execute providers it does not support.
- Store credential material in the surfaces package.
- Canonicalize or deduplicate upstream marketplace/proxy model identifiers.
- Copy OMP's role assignment, fallback-chain, benchmarking, or provider-login implementations.
- Turn Atlas's result dashboard into a second configuration surface.

## Decisions

### 1. Providers become first-class controller state

Add a serializable `ProviderView` and provider navigation state to `SelectionView`: provider rows, provider cursor, active provider, focused pane, and whether the provider decision has been entered. A provider row includes total models, supported models, search matches, declared authentication methods, profile count, readiness state, support explanation, and cursor/preference flags.

The controller owns two cursors. Moving the provider cursor updates the active provider and provider-scoped model preview while clearing any model chosen under a different provider. Entering the provider moves focus to models. Returning to providers preserves the active provider but prevents an accidental model confirmation.

Alternative considered: retain the global model cursor and render synthetic provider headings. That preserves the root problem because headings are not selectable state and cannot own authentication, match counts, or navigation.

### 2. Readiness is derived, ordered, and fail-closed

Provider states are:

- `ready`: at least one application-supported model and either an enabled compatible profile or no declared authentication requirement;
- `connect`: at least one supported model but declared authentication has no enabled compatible profile;
- `catalog`: no model is supported by the consuming application's route predicate.

Rows sort by readiness, then application preference, then the catalogue's shared popularity rank, then stable provider name/id. The popularity list is a neutral discovery default rather than a claim derived from catalogue breadth; applications may promote their own executable route without rewriting it. During search, providers with matches precede zero-match providers without changing readiness truth. Unsupported providers remain browseable and expose their reason, but `resolve()` continues to reject their models.

Alternative considered: treat every catalogue provider as runnable because `model-wiring` can resolve an intent. Rejected because selection resolution is not an inference executor and Atlas must not claim capabilities it does not ship.

### 3. Search is provider-aware

Provider focus uses a global query to match provider identity and model identity/name, computes a match count per provider, and reorders visible providers by match presence. Model focus applies the query within the active provider. Clearing search restores the active provider's normal model ordering. Preferred models remain pinned only inside their provider.

The terminal is always in search-ready mode while browsing. Printable characters update the visible query immediately, Backspace edits it, and Escape clears a non-empty query before performing navigation or close behavior. Search is not entered through a slash-only submode; `/` is ordinary searchable text. Configuration shortcuts use uppercase letters after a model is chosen so lowercase provider/model queries are never intercepted.

This prevents proxy providers with many alphabetically early models from dominating the first screen while retaining complete catalogue search.

### 4. Highlighting previews; choosing configures

The model under the cursor supplies immediate detail—name, identifier, capabilities, limits, support state, and supported modes—without mutating the final `SelectionIntent`. Enter chooses that model and initializes its defaults/profile. A subsequent explicit Enter confirms only when the chosen model remains highlighted and the route is ready.

Alternative considered: preserve the empty detail pane until Enter. Rejected because it wastes space and makes scanning capability differences needlessly modal.

### 5. ANSI layout adapts without border-led composition

At wide widths the renderer shows provider and provider-scoped model columns separated by whitespace and headings, not a persistent vertical rule. Focus is communicated by color, pointer, and heading text. At narrow widths it renders one stage at a time: providers first, then models, then configuration/detail. Left/Right or Tab changes panes on wide screens; Enter drills forward; Left/Escape returns.

The footer is contextual. Provider focus shows movement, direct typing, choose, and close. Model focus adds back and choose. Mode/access shortcuts appear only after a model is chosen. Existing ANSI/control-character sanitization remains mandatory.

### 6. Browser behavior mirrors the hierarchy

The Web Component replaces its provider `<select>` with a keyboard-navigable provider collection, then fetches models with an explicit provider query. Provider rows show model counts and access readiness derivable from `/v1/providers` plus `/v1/profiles`; consuming applications may constrain availability through their service policy. Choosing a provider advances focus to its model results. Arrow-key and focus behavior remain accessible, and responsive CSS stacks stages without introducing left-edge status bars.

The Python controller remains the authoritative reference state machine. The framework-free JavaScript surface mirrors its states because it runs across an HTTP boundary; tests assert the shared vocabulary and transitions.

### 7. Compatibility is behavioral, not preservation of the broken opening screen

Existing exact-model resolution and `SelectionPlan` contracts remain unchanged. `browse(provider=...)`, `search(...)`, model configuration cycles, and application preference inputs remain available, but interactive construction now starts in provider focus. New view fields are additive where practical; obsolete global-scope presentation is removed from renderers.

## Risks / Trade-offs

- [Large provider lists can still be noisy] → Group by readiness, prioritize configured/runnable providers, expose counts, and make search operate at provider level.
- [Provider search may scan thousands of models] → Pre-index normalized provider/model text and compute counts in memory; the current catalogue size is small enough for deterministic local scans, with focused tests guarding behavior.
- [Two cursors introduce stale selection state] → Centralize provider activation and clear model/configuration state whenever the provider changes.
- [Web and Python implementations can drift] → Use the same state labels and scenario fixtures, and assert markup/event contracts in package tests.
- [A catalogue-only provider could appear connectable] → Derive support before authentication and give `catalog` precedence when supported model count is zero.
- [Terminal geometry varies] → Test plain rendering at wide and narrow dimensions and retain the real PTY newline/terminal-mode regression.

## Migration Plan

1. Add provider-first view types and controller transitions with failing regression tests.
2. Replace ANSI rendering and TUI key handling; retain exact plan resolution.
3. Migrate the Web Component to explicit provider drill-down.
4. Update package exports, documentation, and CLI reference behavior.
5. Exercise the editable package through Atlas and update Atlas-specific tests/copy where necessary.
6. Run package, Atlas, UI-build, and real-PTY verification. Re-propose any behavior exposed by those checks before applying the next iteration.

Rollback is local: revert the shared package/controller changes and Atlas consumer adjustments together. No stored selection schema or credential material is migrated.

## Open Questions

None blocking. Credential onboarding beyond representing existing profile/auth requirements remains a separate capability.
