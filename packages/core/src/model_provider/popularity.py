"""Stable, consumer-overridable provider discovery order."""

from __future__ import annotations

from .contracts import ProviderSpec

# This is a navigation heuristic, not telemetry or an execution allowlist.
# Applications can promote their own routes separately, while catalog overlays
# can override ``metadata.popularity_rank`` for a different audience.
POPULAR_PROVIDER_IDS: tuple[str, ...] = (
    "anthropic",
    "github-copilot",
    "openai",
    "google",
    "openrouter",
    "vercel",
    "openai-codex",
    "amazon-bedrock",
    "azure",
    "google-vertex",
    "xai",
    "deepseek",
    "mistral",
    "groq",
    "cerebras",
    "togetherai",
    "fireworks-ai",
    "nvidia",
    "huggingface",
    "ollama-cloud",
    "moonshotai",
    "kimi-for-coding",
    "minimax",
    "cohere",
    "perplexity",
    "alibaba",
    "zai",
    "cloudflare-workers-ai",
)

_POPULARITY_RANK = {
    provider_id: rank for rank, provider_id in enumerate(POPULAR_PROVIDER_IDS)
}


def provider_popularity_rank(provider_id: str) -> int | None:
    """Return the shipped discovery rank, or ``None`` for an unranked provider."""

    return _POPULARITY_RANK.get(provider_id)


def provider_popularity_key(provider: ProviderSpec) -> tuple[int, int, str, str]:
    """Sort ranked providers first and retain a stable alphabetical fallback."""

    declared = provider.metadata.get("popularity_rank")
    rank = (
        declared
        if isinstance(declared, int)
        and not isinstance(declared, bool)
        and declared >= 0
        else provider_popularity_rank(provider.id)
    )
    return (
        0 if rank is not None else 1,
        rank if rank is not None else len(POPULAR_PROVIDER_IDS),
        provider.name.casefold(),
        provider.id,
    )
