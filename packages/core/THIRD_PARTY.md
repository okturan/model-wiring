# Third-party data and interoperability

## Models.dev

The default catalog source is the public Models.dev JSON API:

- website: <https://models.dev/>
- source: <https://github.com/anomalyco/models.dev>
- API: <https://models.dev/api.json>
- upstream license: MIT

Model Wiring fetches this data at runtime and records source URL,
retrieval time, and a content digest in every snapshot. It does not vendor the
full upstream catalog.

## Oh My Pi

Some provider endpoints in `data/default-overlays.json` were located with the
help of Oh My Pi's provider registry:

- source: <https://github.com/can1357/oh-my-pi>
- reviewed at: `80627462b4e91f46795ba87f3678174bd3c0b907`
- upstream license: MIT

Specifically, its `packages/ai/src/registry/oauth/openai-codex.ts` documents the
ChatGPT/Codex authorization, token, and device endpoints, and records that
OpenAI pins the redirect URI to a fixed loopback port — which is why an access
route may declare `oauth.redirect_uri` and the listener binds exactly that.

Every endpoint taken from it was re-verified against the live host before being
written here. **No client identifier was copied.** A client id is issued to one
organisation for its own tooling; borrowing one would make every application
built on this kit share a revocable identity and push the resulting risk onto
end users who never chose it. OAuth routes therefore ship without a
`client_id`, and a consuming application supplies its own.

## Hermes Agent

The API base URLs in `data/default-overlays.json` for the providers Models.dev
catalogs without one were cross-checked against Hermes Agent's provider
registry (`hermes_cli/auth.py`):

- source: <https://github.com/NousResearch/hermes-agent>
- upstream license: MIT

As with Oh My Pi, nothing was taken on trust. Every base URL was requested,
every path was confirmed to answer with an authentication error rather than a
404, and every credential header was confirmed to reach the provider's own auth
layer before being written down. Providers whose endpoint depends on a region,
project, or deployment — Bedrock, Azure, and Vertex among them — are
deliberately absent, because there is no single correct URL to ship.

Provider names, product names, and trademarks belong to their respective
owners. A catalog entry describes interoperability; it does not imply
endorsement or an active commercial relationship.
