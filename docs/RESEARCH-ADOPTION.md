# Harness research adoption map

This project adopts the provider-selection lessons, not the agent runtimes, from
the Atlas harness field guide.

| Source lesson | Kit decision |
| --- | --- |
| Oh My Pi selects `provider/model`, thinking level, tier, roles, and credentials independently | First-class model, effort, variant/tier, role, and credential fields |
| OMP distinguishes runtime keys, configured keys, OAuth, environment keys, and resolvers | Explicit profile/store precedence with provenance |
| OMP exposes the same controls through TUI, RPC, and SDK | One serializable selection contract shared by library, CLI, HTTP, and sibling surfaces |
| Hermes supports API keys, portal/subscription auth, imported clients, pools, and auxiliary roles | Auth kinds, billing kinds, delegated routes, profile priority, and roles are data rather than harness code |
| OpenCode derives a broad provider/model registry from Models.dev | Models.dev is the default catalog source, augmented by local overlays |
| OpenCode uses one backend contract for TUI, browser, desktop, and SDK | Surface package consumes the core view/command schema instead of reimplementing selection |

Deliberately excluded: agent loops, tool execution, session formats, prompt
templates, MCP lifecycle, and harness-specific fallback execution.
