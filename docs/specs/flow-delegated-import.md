# Flow: delegated import of existing logins

Status: Draft. The safest subscription route: reuse a login the provider's
own tooling already created, instead of re-implementing its OAuth. This is
the kit's preferred route wherever an official CLI exists (research decision
in `Authentication and Entitlements.md`), while full OAuth drivers exist for
consumers who cannot assume an installed CLI.

## Model

- Driver: `delegated_import`. Prompt: `ChoicePrompt(candidates)` where each
  candidate is a discovered, non-secret description: tool name, account
  label if safely readable, path or keyring hint, freshness.
- Two import modes per candidate, chosen by the flow spec:
  - **reference** (preferred): the profile stores `auth_kind="delegated"`
    with `metadata.delegate` naming the owning tool; the owning SDK/CLI keeps
    token custody and refresh (`refresh_owner = sdk`). Used when the
    provider's SDK can be pointed at its own login (Codex pattern).
  - **copy**: token material is copied into a kit `SecretStore` once, with
    provenance metadata (`imported_from`, `imported_at`); refresh becomes the
    kit's job or re-import. Used when the artifact is a plain token the
    owning tool cannot serve to us at run time.

## Candidate table (verify every path before implementation)

| Tool | Expected artifact | Mode | Notes |
| --- | --- | --- | --- |
| Codex CLI | `~/.codex/auth.json` | reference | SDK owns refresh; Atlas already links exactly this file per turn |
| Claude Code | credentials file or OS keychain entry under `~/.claude` | copy or reference | storage differs per OS; verify against current Claude Code docs/source |
| gh CLI | `gh auth token` / `hosts.yml` | copy | plain OAuth token; also the Copilot exchange input, see flow-github-copilot |
| gcloud | application default credentials JSON | reference | google-auth library resolves ADC itself; treat as delegated |

## Consent and safety

- Import is always an explicit user action on a named candidate. The kit
  never reads another application's credential files during discovery —
  discovery reports existence and metadata only from paths, not contents,
  except where content is required to show a safe account label (then read,
  extract label, discard).
- Provenance is recorded on the profile; surfaces display "imported from
  Codex CLI" so the user can answer "which account pays".
- Imported tokens may carry tool-specific audiences or scopes: always probe
  (R5) after import and label failures `policy_denied` rather than guessing.
- Revocation guidance per tool belongs in the connect view (e.g. "revoke via
  the provider's authorized-apps page").

## Failure modes

- Candidate exists but is stale/expired → import allowed, probe marks
  `expired`, UI offers the full OAuth route instead.
- Owning tool absent → candidate simply not listed; the OAuth route remains.

## Verify before implementation

- Every artifact path and format above, against each tool's current source
  or docs, at the pinned versions the implementation targets.
- Whether Claude Code uses a file or the OS keychain per platform.
- Whether `gh auth token` output covers the scopes the Copilot exchange
  needs, or whether Copilot's own stored app token is required.
