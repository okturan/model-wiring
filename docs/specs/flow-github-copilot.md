# Flow: GitHub Copilot

Status: Draft. The clearest device-flow + token-exchange example, and rank 1
in the popularity list. Copilot is a two-step: authenticate the GitHub user,
then exchange for a short-lived Copilot API token.

## Route

| Route | auth_kind | billing_kind | Entitlement | Refresh owner |
| --- | --- | --- | --- | --- |
| Copilot subscription | oauth (device) + exchange | subscription | Copilot plan | app |

## Shape

1. **GitHub device flow** (RFC 8628, already supported by `OAuthClient`) using
   the **public client id GitHub documents for device login**, pinned by
   source. Prompt: `UserCodePrompt(verification_uri, user_code)`.
2. **Copilot token exchange**: trade the GitHub OAuth token for a Copilot
   session token via GitHub's Copilot token endpoint. The exchanged token is
   short-lived; the GitHub token is the durable secret and the exchange re-runs
   on expiry.

This needs a small `RefreshDriver` beyond generic OAuth: refresh here means
"re-run the exchange with the stored GitHub token", not a standard
`grant_type=refresh_token` call. Model it as a Copilot-specific driver that
wraps the stored GitHub credential.

## Alternative import

- **delegated_import** from `gh auth token` when the GitHub CLI is present and
  its token carries the scopes the exchange needs (verify — it may not).

## Entitlement

- Probe verifies Copilot entitlement, not just GitHub login: a valid GitHub
  user without a Copilot plan is `policy_denied`, a genuinely useful signal to
  surface distinctly from `expired`.

## Verify before implementation (do not code from memory)

- GitHub device authorization + token endpoints and the public client id used
  for Copilot/device login, from GitHub's current docs.
- The Copilot token-exchange endpoint, request shape, and token lifetime.
- Which GitHub token scopes the exchange requires, and whether `gh auth token`
  satisfies them.
- Terms-of-service posture for third-party Copilot access; record it in the
  connect view so the user decides with eyes open.
