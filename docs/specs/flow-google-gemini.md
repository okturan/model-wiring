# Flow: Google and Gemini

Status: Draft. Represents the cloud-credential-chain pattern (rank 3 group,
with `google-vertex` separate). Google is where "one secret" is often wrong:
API key, user OAuth, and ambient cloud identity are all valid and distinct.

## Distinct routes (never merged)

| Route | auth_kind | billing_kind | Entitlement | Refresh owner |
| --- | --- | --- | --- | --- |
| Gemini API key (AI Studio) | api_key | api | usage-based | none |
| Google account OAuth | oauth | api or subscription | account-defined | app |
| Vertex AI via cloud credentials | credential_bundle / delegated | api | GCP project billing | cloud_sdk |

`google` = direct Gemini API; `google-vertex` = Vertex, kept separate for
billing legibility.

## Preferred client-identity order

1. **api_key paste** for AI Studio keys — simplest, usage billing, clearly
   labelled.
2. **cloud credential chain** for Vertex: reuse Application Default
   Credentials via the google-auth library (delegated, `refresh_owner =
   cloud_sdk`) rather than the kit minting tokens. `gcloud` ADC import is the
   delegated candidate.
3. **oauth_pkce** user login only where a documented public client id and
   scope set exist for direct Gemini access; otherwise omit rather than invent.

## Entitlement

- Vertex readiness depends on project + region + enabled API, not just a
  credential; probe reports `unavailable` vs `policy_denied` distinctly and
  records the project fingerprint (safe id), never the credential.

## Safety specifics

- The kit never fabricates a Google OAuth client; if no first-party public
  client is confirmable, that route is not shipped.
- Cloud identity stays owned by google-auth/ADC; the kit references it.

## Verify before implementation (do not code from memory)

- Whether a documented public OAuth client id exists for direct Gemini user
  login, from Google's current sources — if not, ship only api_key + Vertex.
- ADC resolution and the minimal Vertex readiness probe (project/region/model
  availability) via the google-auth libraries at implementation time.
- AI Studio key format hints, if any, from current docs.
