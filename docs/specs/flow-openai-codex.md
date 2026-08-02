# Flow: OpenAI and Codex

Status: Draft. OpenAI is the canonical example of one model family reachable
through several entitlements, so it drives the no-silent-crossover rule.

## Distinct routes (never merged)

| Route | auth_kind | billing_kind | Entitlement | Refresh owner |
| --- | --- | --- | --- | --- |
| ChatGPT/Codex subscription | oauth (or delegated) | subscription | ChatGPT plan credits | sdk (delegated) or app (oauth) |
| OpenAI Platform API key | api_key | api | usage-based, Platform billing | none |
| Codex CLI import | delegated | subscription | whatever the CLI logged in as | sdk |
| OpenAI-compatible local/gateway | anonymous / api_key | local / api | endpoint-defined | none |

Provider ids follow the catalog and OMP's convention: `openai` = Platform
API key; `openai-codex` = ChatGPT/Codex subscription. Keeping them as
separate provider entries is what makes the billing distinction legible.

## Preferred client-identity order

1. **delegated_import** of the Codex CLI login (`~/.codex/auth.json`) — matches
   what Atlas already does and lets the Codex SDK own refresh. Best route when
   Codex is installed.
2. **oauth_pkce** browser login using the **public client id OpenAI ships in
   its own Codex tooling** — pinned by source, secretless, loopback redirect.
   For consumers without the CLI.
3. **oauth_device** where a browser cannot be opened on the same machine.

The Platform API-key route is always offered alongside, explicitly labelled as
usage billing.

## Entitlement and speed

- Probe records `entitlement_class` = subscription vs usage_api. The kit does
  **not** infer this from the model slug.
- "Fast mode" is a ChatGPT-credit feature on subscription auth; with an API
  key, API pricing applies. The kit records the observed tier as capability,
  never as a pricing promise. No cost projection ships without an authoritative
  pricing record (research explicitly rejected the unsourced "Luna discount"
  and "Sam Altman decree" claims).

## Safety specifics

- Subscription tokens stay delegated to the Codex SDK/login where possible;
  the kit avoids taking custody of ChatGPT refresh tokens unless the user
  explicitly chooses the copy import.
- A failed API key never falls back to the subscription account, and vice
  versa.

## Verify before implementation (do not code from memory)

- OpenAI/Codex authorization + token endpoints, scopes, and the exact public
  client id, from OpenAI's current first-party Codex sources — the research
  note's links (`learn.chatgpt.com/docs/auth`, `.../codex-sdk`) are the
  starting point, re-checked at implementation time.
- `~/.codex/auth.json` shape and whether the SDK exposes a supported "use my
  existing login" entry point versus file linking.
- Device-flow availability for the chosen client id.
