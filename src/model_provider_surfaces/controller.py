"""Shared, renderer-agnostic human selection state."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from model_provider import (
    Catalog,
    CredentialProfile,
    ModelSpec,
    ProfileRegistry,
    SelectionIntent,
    SelectionPlan,
    Selector,
)


@dataclass(frozen=True)
class CandidateView:
    id: str
    provider: str
    name: str
    family: str | None
    score: float | None
    badges: tuple[str, ...]
    selected: bool
    cursor: bool


@dataclass(frozen=True)
class SelectionView:
    catalog_digest: str
    provider_count: int
    model_count: int
    query: str
    candidates: tuple[CandidateView, ...]
    cursor: int
    selected_model: str | None
    selected_name: str | None
    selected_provider: str | None
    capabilities: tuple[str, ...]
    context_limit: int | float | None
    variants: tuple[str, ...]
    variant: str | None
    efforts: tuple[str, ...]
    effort: str | None
    tiers: tuple[str, ...]
    tier: str | None
    billing_kinds: tuple[str, ...]
    billing_kind: str | None
    profiles: tuple[CredentialProfile, ...]
    credential_profile: str | None
    auth_required: bool
    route_ready: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_digest": self.catalog_digest,
            "provider_count": self.provider_count,
            "model_count": self.model_count,
            "query": self.query,
            "candidates": [candidate.__dict__ for candidate in self.candidates],
            "cursor": self.cursor,
            "selected_model": self.selected_model,
            "selected_name": self.selected_name,
            "selected_provider": self.selected_provider,
            "capabilities": list(self.capabilities),
            "context_limit": self.context_limit,
            "variants": list(self.variants),
            "variant": self.variant,
            "efforts": list(self.efforts),
            "effort": self.effort,
            "tiers": list(self.tiers),
            "tier": self.tier,
            "billing_kinds": list(self.billing_kinds),
            "billing_kind": self.billing_kind,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "credential_profile": self.credential_profile,
            "auth_required": self.auth_required,
            "route_ready": self.route_ready,
            "error": self.error,
        }


class SelectionController:
    """State machine shared by CLI, TUI, and testable custom surfaces."""

    def __init__(
        self,
        catalog: Catalog,
        profiles: ProfileRegistry | Sequence[CredentialProfile] | None = None,
        *,
        limit: int = 12,
    ) -> None:
        self.catalog = catalog
        self.profiles = profiles
        self.limit = max(1, limit)
        self.query = ""
        self._models: list[tuple[ModelSpec, float | None]] = []
        self.cursor = 0
        self.selected: ModelSpec | None = None
        self.variant: str | None = None
        self.effort: str | None = None
        self.tier: str | None = None
        self.billing_kind: str | None = None
        self.credential_profile: str | None = None
        self.error: str | None = None
        self.browse()

    def browse(self, *, provider: str | None = None) -> None:
        provider_id = self.catalog.provider(provider).id if provider else None
        models = [
            model
            for candidate_provider in self.catalog.snapshot.providers.values()
            if provider_id is None or candidate_provider.id == provider_id
            for model in candidate_provider.models.values()
        ]
        models.sort(key=lambda model: (model.provider_id, model.id))
        self._models = [(model, None) for model in models[: self.limit]]
        self.cursor = 0
        self.error = None

    def search(self, query: str, *, provider: str | None = None) -> None:
        self.query = query
        if not query.strip():
            self.browse(provider=provider)
            return
        self._models = [
            (hit.model, hit.score)
            for hit in self.catalog.search(query, provider=provider, limit=self.limit)
        ]
        self.cursor = 0
        self.error = None if self._models else f"No models match {query!r}"

    def move(self, delta: int) -> None:
        if self._models:
            self.cursor = (self.cursor + delta) % len(self._models)

    def choose(self, index: int | None = None) -> ModelSpec:
        if not self._models:
            raise ValueError("there is no highlighted model to choose")
        if index is not None:
            if index < 0 or index >= len(self._models):
                raise IndexError(index)
            self.cursor = index
        self.selected = self._models[self.cursor][0]
        # Cost/latency-affecting modes remain provider defaults until the human
        # deliberately cycles them.
        self.variant = None
        self.effort = None
        self.tier = None
        compatible_profiles = self._profiles_for(self.selected.provider_id)
        kinds = tuple(
            dict.fromkeys(profile.billing_kind for profile in compatible_profiles)
        )
        self.billing_kind = kinds[0] if len(kinds) == 1 else None
        narrowed = [
            profile
            for profile in compatible_profiles
            if self.billing_kind is None or profile.billing_kind == self.billing_kind
        ]
        self.credential_profile = narrowed[0].id if len(narrowed) == 1 else None
        self.error = None
        return self.selected

    def cycle_variant(self) -> None:
        self.variant = _cycle(self.variant, self._options("variant"))

    def cycle_effort(self) -> None:
        self.effort = _cycle(self.effort, self._options("effort"))

    def cycle_tier(self) -> None:
        self.tier = _cycle(self.tier, self._options("tier"))

    def cycle_billing(self) -> None:
        self.billing_kind = _cycle(self.billing_kind, self._options("billing"))
        compatible = [
            profile
            for profile in self._current_profiles()
            if self.billing_kind is None or profile.billing_kind == self.billing_kind
        ]
        self.credential_profile = compatible[0].id if len(compatible) == 1 else None

    def cycle_profile(self) -> None:
        candidates = [
            profile.id
            for profile in self._current_profiles()
            if self.billing_kind is None or profile.billing_kind == self.billing_kind
        ]
        self.credential_profile = _cycle(self.credential_profile, tuple(candidates))

    def intent(self) -> SelectionIntent:
        if self.selected is None:
            raise ValueError("choose a model before resolving")
        return SelectionIntent(
            model=self.selected.qualified_id,
            variant=self.variant,
            effort=self.effort,
            tier=self.tier,
            billing_kind=self.billing_kind,
            credential_profile=self.credential_profile,
        )

    def resolve(self) -> SelectionPlan:
        try:
            plan = Selector(self.catalog, self.profiles).select(self.intent())
        except Exception as exc:
            self.error = str(exc)
            raise
        self.error = None
        return plan

    def view(self) -> SelectionView:
        selected = self.selected
        profiles = tuple(self._current_profiles())
        provider = self.catalog.provider(selected.provider_id) if selected else None
        auth_required = bool(provider and provider.auth_methods)
        route_ready = bool(
            selected
            and (not auth_required or self.credential_profile)
            and self.error is None
        )
        return SelectionView(
            catalog_digest=self.catalog.snapshot.digest,
            provider_count=len(self.catalog.snapshot.providers),
            model_count=self.catalog.snapshot.model_count,
            query=self.query,
            candidates=tuple(
                CandidateView(
                    id=model.qualified_id,
                    provider=model.provider_id,
                    name=model.name,
                    family=model.family,
                    score=score,
                    badges=_badges(model),
                    selected=selected is not None
                    and model.qualified_id == selected.qualified_id,
                    cursor=index == self.cursor,
                )
                for index, (model, score) in enumerate(self._models)
            ),
            cursor=self.cursor,
            selected_model=selected.qualified_id if selected else None,
            selected_name=selected.name if selected else None,
            selected_provider=selected.provider_id if selected else None,
            capabilities=tuple(
                sorted(
                    key
                    for key, value in (
                        selected.capabilities if selected else {}
                    ).items()
                    if value
                )
            ),
            context_limit=selected.limits.get("context") if selected else None,
            variants=self._options("variant"),
            variant=self.variant,
            efforts=self._options("effort"),
            effort=self.effort,
            tiers=self._options("tier"),
            tier=self.tier,
            billing_kinds=self._options("billing"),
            billing_kind=self.billing_kind,
            profiles=profiles,
            credential_profile=self.credential_profile,
            auth_required=auth_required,
            route_ready=route_ready,
            error=self.error,
        )

    def _options(self, kind: str) -> tuple[str, ...]:
        if self.selected is None:
            return ()
        if kind == "variant":
            return tuple(sorted(self.selected.variants))
        if kind == "effort":
            return self.selected.reasoning_options
        if kind == "tier":
            return self._tiers(self.selected)
        if kind == "billing":
            return tuple(
                dict.fromkeys(
                    profile.billing_kind for profile in self._current_profiles()
                )
            )
        raise ValueError(kind)

    def _tiers(self, model: ModelSpec) -> tuple[str, ...]:
        provider = self.catalog.provider(model.provider_id)
        result: list[str] = []
        for metadata in (provider.metadata, model.metadata):
            values = metadata.get("tiers")
            if isinstance(values, (list, tuple)):
                result.extend(str(value) for value in values)
        return tuple(dict.fromkeys(result))

    def _current_profiles(self) -> Iterable[CredentialProfile]:
        if self.selected is None:
            return ()
        return self._profiles_for(self.selected.provider_id)

    def _profiles_for(self, provider_id: str) -> tuple[CredentialProfile, ...]:
        if self.profiles is None:
            return ()
        if isinstance(self.profiles, ProfileRegistry):
            return self.profiles.list(provider_id=provider_id, enabled_only=True)
        return tuple(
            sorted(
                (
                    profile
                    for profile in self.profiles
                    if profile.provider_id == provider_id and profile.enabled
                ),
                key=lambda profile: (profile.priority, profile.id),
            )
        )


def _badges(model: ModelSpec) -> tuple[str, ...]:
    labels: list[str] = []
    if model.capabilities.get("reasoning"):
        labels.append("reasoning")
    if model.capabilities.get("tool_call"):
        labels.append("tools")
    if "image" in model.modalities.get("input", ()):
        labels.append("vision")
    if model.capabilities.get("open_weights"):
        labels.append("open")
    return tuple(labels)


def _cycle(current: str | None, options: tuple[str, ...]) -> str | None:
    if not options:
        return None
    if current not in options:
        return options[0]
    return options[(options.index(current) + 1) % len(options)]
