# Model Wiring specifications

Model Wiring is a development kit for **any application that consumes LLMs** —
coding agents, analysis pipelines, chat products, or full agent harnesses. It
was born inside Atlas, but Atlas is now just the first consumer. The agent
harness research (OpenCode, Oh My Pi, Hermes, Cline, Goose, OpenHands, Aider)
was done to learn **which onboarding flow each harness uses**, so this kit can
ship those flows as reusable primitives with TUI/web tooling around them —
without becoming a harness itself.

These specs define subscription-grade provider onboarding: *"connect this app
to the account you already pay for, in one guided flow."*

## Index

| Spec | Scope | Status |
| --- | --- | --- |
| [Onboarding architecture](onboarding-architecture.md) | Route data, login drivers, orchestration, storage, surfaces, milestones | Draft |
| [API key, environment, local](flow-api-key-env-local.md) | Paste flows, env bundles, anonymous local endpoints | Draft |
| [Delegated import](flow-delegated-import.md) | Reusing logins that official CLIs already hold | Draft |
| [OpenAI / Codex](flow-openai-codex.md) | ChatGPT subscription OAuth, API key, Codex CLI import | Draft |
| [Anthropic / Claude](flow-anthropic-claude.md) | Claude subscription OAuth, API key, Claude Code import | Draft |
| [GitHub Copilot](flow-github-copilot.md) | Device flow plus Copilot token exchange | Draft |
| [Google / Gemini](flow-google-gemini.md) | API key, Google OAuth, cloud credential chain | Draft |

Statuses: Draft → Agreed → Implemented. A flow spec is Agreed when its
"Verify before implementation" items are pinned to sources.

## Ground rules shared by every spec

- Authentication ("who may call"), entitlement ("what access that identity
  carries"), and billing ("which account pays") are three axes, never one
  "API key" field.
- No silent crossover between billing kinds. A subscription login never falls
  back to metered API billing (or the reverse) without an explicit user action.
- Secrets live only in `SecretStore` backends. The profile database, catalog,
  events, logs, and every UI surface remain secret-free.
- Third-party endpoints, client IDs, and storage paths are **verified against
  pinned sources at implementation time**, never trusted from memory.

## Sources

- Research notebooks: `agent-console/apps/atlas/docs/research/agent-harnesses/`
  — especially `Authentication and Entitlements.md` and `Oh My Pi.md`
  (pinned to `can1357/oh-my-pi@8062746`).
- Adopted lessons: `packages/core/docs/RESEARCH-ADOPTION.md`.
- Current chassis: `packages/core/src/model_wiring/` (`contracts.py`,
  `auth.py`, `oauth.py`, `profiles.py`, `discovery.py`).
