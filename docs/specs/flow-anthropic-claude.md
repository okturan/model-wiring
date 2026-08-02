# Flow: Anthropic and Claude

Status: Draft. First subscription OAuth target to implement end to end (M3),
because Anthropic is rank 0 in the popularity list and the most common
"connect my Claude subscription" ask.

## Distinct routes (never merged)

| Route | auth_kind | billing_kind | Entitlement | Refresh owner |
| --- | --- | --- | --- | --- |
| Claude subscription (Pro/Max) | oauth | subscription | Claude plan usage | app |
| Anthropic API key | api_key | api | usage-based, Console billing | none |
| Claude Code import | delegated / copy | subscription | whatever Claude Code logged in as | sdk or app |
| Bedrock / Vertex access | credential_bundle / delegated | api | cloud account | cloud_sdk |

`anthropic` is the direct provider; `amazon-bedrock` and `google-vertex` are
separate catalog entries that also serve Claude models under cloud billing —
kept distinct for the same billing-legibility reason as OpenAI.

## Preferred client-identity order

1. **delegated_import** of a Claude Code login when present.
2. **oauth_pkce** subscription login with loopback redirect, using the
   **public client id from Anthropic's own first-party tooling**, pinned by
   source.
3. **api_key paste** for Console/usage billing, always offered and clearly
   labelled.

## Entitlement

- Probe distinguishes subscription vs api and records a safe account label
  (e.g. organization/workspace name if the provider exposes one), never the
  key or token.
- Bedrock/Vertex go through the cloud credential chain (bundle or delegated
  cloud SDK), never a raw key the kit invents.

## Safety specifics

- Subscription and API routes never cross-fall-back.
- Copy import of a Claude Code token records provenance and is followed by a
  probe; reference/delegated mode is preferred when the SDK can serve its own
  login.

## Verify before implementation (do not code from memory)

- Anthropic OAuth authorization/token endpoints, scopes, and the public
  client id, from Anthropic's current first-party sources — via the
  `claude-api` skill and live docs, not memory.
- Claude Code credential storage per OS (file under `~/.claude` vs OS
  keychain) for the import route.
- Whether the subscription OAuth issues refresh tokens (drives whether
  `oauth.py` refresh applies or re-login is required).
- Bedrock/Vertex auth expectations at implementation time.
