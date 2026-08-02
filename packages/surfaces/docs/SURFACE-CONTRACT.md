# Shared surface contract

All surfaces render `SelectionView` and emit `SelectionIntent`.

```text
catalog + public profiles
          ↓
SelectionController
  provider state: readiness, search matches, provider cursor, active provider
          ↓ activate_provider / focus_providers
  model state: provider-scoped candidates, model cursor, immediate preview
          ↓ choose
  route state: variant, effort, tier, authenticated access route
          ↓ resolve
SelectionPlan
```

Surface implementations may choose layout and interaction conventions but may
not add selection semantics. Ambiguity, capability checks, access-route safety,
and plan identity remain core responsibilities.

An embedding application may provide a `route_support(model)` policy. Returning
`None` marks the route runnable; returning a human explanation keeps the model
discoverable as catalog-only and prevents resolution. This keeps catalog
coverage separate from the executors an application actually owns.

`ProviderView.state` is derived in this order:

1. `catalog` when no provider model passes `route_support`;
2. `connect` when supported models exist but declared authentication has no
   enabled compatible profile;
3. `ready` when supported models and a usable access route are present, or the
   provider requires no authentication.

The view never treats a highlighted model as chosen. `SelectionView.preview`
describes the current provider-scoped cursor immediately; `selected_model`
changes only through `choose()`. Switching provider clears a selected model
owned by the previous provider.

Provider focus searches provider identity plus owned model identity/name and
reports `match_count` on each provider. Model focus searches only within the
active provider. Readiness groups are ordered by application preference, shared
`popularity_rank`, then stable provider name/id. Preferred providers/models
affect ordering but never hide catalogue totals.

The ANSI TUI treats printable browsing input as the search field directly;
there is no search activation mode. Backspace edits the query, while Escape
clears a non-empty query before it navigates or closes. Single-letter
configuration shortcuts are uppercase and only active after a model is chosen.

The ANSI TUI uses stdin/stdout and no third-party terminal dependency. The web
component uses the loopback JSON API and no framework. An application can reuse
only the controller or individual renderer functions without adopting either
interactive shell.

Wide ANSI layouts show provider and model columns without a persistent divider.
Narrow layouts show one stage at a time. The Web Component mirrors the same
provider → model → route progression and accepts an optional
`supported-providers` allowlist for application-owned execution policy.
