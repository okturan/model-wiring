# Shared surface contract

All surfaces render `SelectionView` and emit `SelectionIntent`.

```text
 catalog + public profiles
           |
           v
 SelectionController
   state: query, cursor, selected provider/model, variant, effort, tier,
          billing route, profile, requirements
   commands: search, move, choose, change field, resolve
           |
           +--> SelectionView (render-safe, no secrets)
           `--> SelectionPlan (after resolve)
```

Surface implementations may choose layout and interaction conventions but may
not add selection semantics. Ambiguity, capability checks, billing-route safety,
and plan identity remain core responsibilities.

The ANSI TUI uses stdin/stdout and no third-party terminal dependency. The web
component uses the loopback JSON API and no framework. An application can reuse
only the controller or individual renderer functions without adopting either
interactive shell.
