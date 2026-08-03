"""Shared, renderer-agnostic provider-first selection state."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from model_wiring import (
    AccessRoute,
    Catalog,
    CredentialProfile,
    ModelSpec,
    ProfileRegistry,
    ProviderAccess,
    ProviderSpec,
    SelectionIntent,
    SelectionPlan,
    Selector,
    provider_access,
    provider_popularity_key,
)


@dataclass(frozen=True)
class ProviderView:
    id: str
    name: str
    model_count: int
    runnable_model_count: int
    match_count: int
    auth_methods: tuple[str, ...]
    access_routes: tuple[AccessRoute, ...]
    required_variables: tuple[str, ...]
    credential_state: str
    probe_state: str | None
    entitlement_class: str | None
    profile_count: int
    state: str
    state_label: str
    support_reason: str | None
    preferred: bool
    active: bool
    cursor: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model_count": self.model_count,
            "runnable_model_count": self.runnable_model_count,
            "match_count": self.match_count,
            "auth_methods": list(self.auth_methods),
            "access_routes": [route.to_dict() for route in self.access_routes],
            "required_variables": list(self.required_variables),
            "credential_state": self.credential_state,
            "probe_state": self.probe_state,
            "entitlement_class": self.entitlement_class,
            "profile_count": self.profile_count,
            "state": self.state,
            "state_label": self.state_label,
            "support_reason": self.support_reason,
            "preferred": self.preferred,
            "active": self.active,
            "cursor": self.cursor,
        }


@dataclass(frozen=True)
class CandidateView:
    id: str
    provider: str
    name: str
    family: str | None
    score: float | None
    badges: tuple[str, ...]
    route_supported: bool
    route_support_reason: str | None
    selected: bool
    cursor: bool


@dataclass(frozen=True)
class ModelPreview:
    id: str
    provider: str
    name: str
    family: str | None
    capabilities: tuple[str, ...]
    context_limit: int | float | None
    variants: tuple[str, ...]
    efforts: tuple[str, ...]
    tiers: tuple[str, ...]
    route_supported: bool
    route_support_reason: str | None
    credential_state: str = "configured"
    required_variables: tuple[str, ...] = ()
    probe_state: str | None = None

    @property
    def usable_now(self) -> bool:
        """Execution support and a credential are independent requirements."""

        return self.route_supported and self.credential_state == "configured"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "name": self.name,
            "family": self.family,
            "capabilities": list(self.capabilities),
            "context_limit": self.context_limit,
            "variants": list(self.variants),
            "efforts": list(self.efforts),
            "tiers": list(self.tiers),
            "route_supported": self.route_supported,
            "route_support_reason": self.route_support_reason,
            "credential_state": self.credential_state,
            "required_variables": list(self.required_variables),
            "probe_state": self.probe_state,
            "usable_now": self.usable_now,
        }


@dataclass(frozen=True)
class SelectionView:
    catalog_digest: str
    provider_count: int
    model_count: int
    runnable_provider_count: int
    runnable_model_count: int
    providers: tuple[ProviderView, ...]
    provider_cursor: int
    active_provider: str | None
    active_provider_name: str | None
    focus: str
    provider_chosen: bool
    scope: str
    query: str
    candidates: tuple[CandidateView, ...]
    cursor: int
    preview: ModelPreview | None
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
    billing_kind: str | None
    auth_kind: str | None
    access_label: str | None
    profiles: tuple[CredentialProfile, ...]
    credential_profile: str | None
    auth_required: bool
    route_supported: bool
    route_support_reason: str | None
    route_ready: bool
    error: str | None = None

    @property
    def preview_model(self) -> str | None:
        return self.preview.id if self.preview else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_digest": self.catalog_digest,
            "provider_count": self.provider_count,
            "model_count": self.model_count,
            "runnable_provider_count": self.runnable_provider_count,
            "runnable_model_count": self.runnable_model_count,
            "providers": [provider.to_dict() for provider in self.providers],
            "provider_cursor": self.provider_cursor,
            "active_provider": self.active_provider,
            "active_provider_name": self.active_provider_name,
            "focus": self.focus,
            "provider_chosen": self.provider_chosen,
            "scope": self.scope,
            "query": self.query,
            "candidates": [candidate.__dict__ for candidate in self.candidates],
            "cursor": self.cursor,
            "preview": self.preview.to_dict() if self.preview else None,
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
            "billing_kind": self.billing_kind,
            "auth_kind": self.auth_kind,
            "access_label": self.access_label,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "credential_profile": self.credential_profile,
            "auth_required": self.auth_required,
            "route_supported": self.route_supported,
            "route_support_reason": self.route_support_reason,
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
        default_variant: str | None = None,
        default_effort: str | None = None,
        default_tier: str | None = None,
        default_profile: str | None = None,
        route_support: Callable[[ModelSpec], str | None] | None = None,
        preferred_models: Sequence[str] = (),
        preferred_providers: Sequence[str] = (),
    ) -> None:
        self.catalog = catalog
        self.profiles = profiles
        self.limit = max(1, limit)
        self.query = ""
        self._models: list[tuple[ModelSpec, float | None]] = []
        self._provider_ids: list[str] = []
        self._provider_matches: dict[str, int] = {}
        self.provider_cursor = 0
        self.cursor = 0
        self.active_provider: str | None = None
        self.focus = "providers"
        self.provider_chosen = False
        self.selected: ModelSpec | None = None
        self.variant: str | None = None
        self.effort: str | None = None
        self.tier: str | None = None
        self.billing_kind: str | None = None
        self.credential_profile: str | None = None
        self.error: str | None = None
        self.scope = "providers"
        self._runnable_only = False
        self._route_support = route_support
        self._preferred_models = {
            qualified_id: index for index, qualified_id in enumerate(preferred_models)
        }
        provider_order = list(dict.fromkeys(preferred_providers))
        for qualified_id in preferred_models:
            provider_id, separator, _ = qualified_id.partition("/")
            if separator and provider_id not in provider_order:
                provider_order.append(provider_id)
        self._preferred_providers = {
            provider_id: index for index, provider_id in enumerate(provider_order)
        }
        self._catalog_models = tuple(
            model
            for provider in self.catalog.snapshot.providers.values()
            for model in provider.models.values()
        )
        self._defaults = {
            "variant": default_variant,
            "effort": default_effort,
            "tier": default_tier,
            "profile": default_profile,
        }
        self.browse()

    def browse(
        self,
        *,
        provider: str | None = None,
        runnable_only: bool = False,
    ) -> None:
        self.query = ""
        self.error = None
        self._runnable_only = runnable_only
        self.scope = "runnable" if runnable_only else "providers"
        self._refresh_providers(prefer_first=True)
        if provider is not None:
            provider_id = self.catalog.provider(provider).id
            self._set_active_provider(provider_id)
            self.provider_chosen = True
            self.focus = "models"
            self.scope = provider_id
        else:
            self.provider_chosen = False
            self.focus = "providers"
        self._refresh_models()

    def search(self, query: str, *, provider: str | None = None) -> None:
        next_query = query.strip()
        if next_query != self.query and self.selected is not None:
            self._clear_selection()
        self.query = next_query
        self.error = None
        if provider is not None:
            provider_id = self.catalog.provider(provider).id
            self._runnable_only = False
            self._refresh_providers()
            self._set_active_provider(provider_id)
            self.provider_chosen = True
            self.focus = "models"
            self.scope = provider_id if not self.query else "search"
            self._refresh_models()
            if self.query and not self._models:
                self.error = f"No models in {provider_id!r} match {self.query!r}"
            return

        self.scope = (
            "search"
            if self.query
            else ("runnable" if self._runnable_only else "providers")
        )
        self._refresh_providers(prefer_first=bool(self.query))
        self._refresh_models()
        if self.query and not any(self._provider_matches.values()):
            self.error = f"No providers or models match {self.query!r}"
        elif self.query and self.focus == "models" and not self._models:
            self.error = f"No models in {self.active_provider!r} match {self.query!r}"

    def move(self, delta: int) -> None:
        if self.focus == "providers":
            self.move_provider(delta)
        else:
            self.move_model(delta)

    def move_provider(self, delta: int) -> None:
        if not self._provider_ids:
            return
        self.provider_cursor = (self.provider_cursor + delta) % len(self._provider_ids)
        self._set_active_provider(self._provider_ids[self.provider_cursor])
        self.error = None

    def move_model(self, delta: int) -> None:
        if self._models:
            self.cursor = (self.cursor + delta) % len(self._models)
        self.error = None

    def activate_provider(self, index: int | None = None) -> str:
        if not self._provider_ids:
            raise ValueError("there is no highlighted provider to choose")
        if index is not None:
            if index < 0 or index >= len(self._provider_ids):
                raise IndexError(index)
            self.provider_cursor = index
            self._set_active_provider(self._provider_ids[index])
        self.provider_chosen = True
        self.focus = "models"
        self.scope = self.active_provider or "providers"
        self.error = None if self._models else "This provider has no matching models"
        return self.active_provider or ""

    def choose_provider(self, index: int | None = None) -> str:
        return self.activate_provider(index)

    def focus_providers(self) -> None:
        self.focus = "providers"
        self.provider_chosen = False
        self.scope = (
            "search"
            if self.query
            else ("runnable" if self._runnable_only else "providers")
        )
        self.error = None

    def focus_models(self) -> None:
        self.activate_provider()

    def choose(self, index: int | None = None) -> ModelSpec:
        if not self._models:
            raise ValueError("there is no highlighted model to choose")
        if index is not None:
            if index < 0 or index >= len(self._models):
                raise IndexError(index)
            self.cursor = index
        self.selected = self._models[self.cursor][0]
        self.variant = self._supported_default("variant")
        self.effort = self._supported_default("effort")
        self.tier = self._supported_default("tier")
        compatible_profiles = self._profiles_for(self.selected.provider_id)
        preferred_profile = self._defaults["profile"]
        if preferred_profile and any(
            profile.id == preferred_profile for profile in compatible_profiles
        ):
            self.credential_profile = preferred_profile
        else:
            self.credential_profile = (
                compatible_profiles[0].id if len(compatible_profiles) == 1 else None
            )
        self._derive_access_route()
        self.error = None
        return self.selected

    def cycle_variant(self) -> None:
        self.variant = _cycle(self.variant, self._options("variant"))

    def cycle_effort(self) -> None:
        self.effort = _cycle(self.effort, self._options("effort"))

    def cycle_tier(self) -> None:
        self.tier = _cycle(self.tier, self._options("tier"))

    def cycle_profile(self) -> None:
        candidates = [profile.id for profile in self._current_profiles()]
        self.credential_profile = _cycle(self.credential_profile, tuple(candidates))
        self._derive_access_route()

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
        if self.selected is not None:
            reason = self._support_reason(self.selected)
            if reason is not None:
                self.error = reason
                raise ValueError(reason)
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
        selected_profile = next(
            (profile for profile in profiles if profile.id == self.credential_profile),
            None,
        )
        selected_provider = (
            self.catalog.provider(selected.provider_id) if selected else None
        )
        active_provider = (
            self.catalog.provider(self.active_provider)
            if self.active_provider is not None
            else None
        )
        route_support_reason = self._support_reason(selected) if selected else None
        route_supported = selected is not None and route_support_reason is None
        auth_required = bool(
            selected_provider and self._requires_auth(selected_provider)
        )
        route_ready = bool(
            route_supported
            and (not auth_required or self.credential_profile)
            and self.error is None
        )
        supported_models = tuple(
            model
            for model in self._catalog_models
            if self._support_reason(model) is None
        )
        preview_model = self._models[self.cursor][0] if self._models else None
        preview = self._preview(preview_model) if preview_model else None
        return SelectionView(
            catalog_digest=self.catalog.snapshot.digest,
            provider_count=len(self.catalog.snapshot.providers),
            model_count=self.catalog.snapshot.model_count,
            runnable_provider_count=len(
                {model.provider_id for model in supported_models}
            ),
            runnable_model_count=len(supported_models),
            providers=tuple(
                self._provider_view(provider_id, index)
                for index, provider_id in enumerate(self._provider_ids)
            ),
            provider_cursor=self.provider_cursor,
            active_provider=self.active_provider,
            active_provider_name=active_provider.name if active_provider else None,
            focus=self.focus,
            provider_chosen=self.provider_chosen,
            scope=self.scope,
            query=self.query,
            candidates=tuple(
                CandidateView(
                    id=model.qualified_id,
                    provider=model.provider_id,
                    name=model.name,
                    family=model.family,
                    score=score,
                    badges=_badges(model),
                    route_supported=self._support_reason(model) is None,
                    route_support_reason=self._support_reason(model),
                    selected=selected is not None
                    and model.qualified_id == selected.qualified_id,
                    cursor=index == self.cursor,
                )
                for index, (model, score) in enumerate(self._models)
            ),
            cursor=self.cursor,
            preview=preview,
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
            billing_kind=self.billing_kind,
            auth_kind=selected_profile.auth_kind if selected_profile else None,
            access_label=(
                selected_profile.account_label or selected_profile.id
                if selected_profile
                else None
            ),
            profiles=profiles,
            credential_profile=self.credential_profile,
            auth_required=auth_required,
            route_supported=route_supported,
            route_support_reason=route_support_reason,
            route_ready=route_ready,
            error=self.error,
        )

    def _refresh_providers(self, *, prefer_first: bool = False) -> None:
        previous = self.active_provider
        providers = list(self.catalog.snapshot.providers.values())
        self._provider_matches = {
            provider.id: len(self._matching_models(provider, self.query))
            for provider in providers
        }
        if self._runnable_only:
            providers = [
                provider
                for provider in providers
                if self._provider_state(provider)[0] != "catalog"
            ]
        providers.sort(key=self._provider_key)
        self._provider_ids = [provider.id for provider in providers]
        if not self._provider_ids:
            self.active_provider = None
            self.provider_cursor = 0
            self._models = []
            self.cursor = 0
            return
        if previous in self._provider_ids and not (
            prefer_first
            and self.query
            and self._provider_matches.get(previous or "", 0) == 0
        ):
            self.active_provider = previous
            self.provider_cursor = self._provider_ids.index(previous)
        else:
            self.active_provider = self._provider_ids[0]
            self.provider_cursor = 0

    def _set_active_provider(self, provider_id: str) -> None:
        self.catalog.provider(provider_id)
        changed = self.active_provider != provider_id
        self.active_provider = provider_id
        if provider_id in self._provider_ids:
            self.provider_cursor = self._provider_ids.index(provider_id)
        if (
            changed
            and self.selected is not None
            and self.selected.provider_id != provider_id
        ):
            self._clear_selection()
        self._refresh_models(reset_cursor=changed)

    def _refresh_models(self, *, reset_cursor: bool = False) -> None:
        previous = (
            self._models[self.cursor][0].qualified_id
            if self._models and self.cursor < len(self._models)
            else None
        )
        if self.active_provider is None:
            self._models = []
            self.cursor = 0
            return
        provider = self.catalog.provider(self.active_provider)
        models = list(self._matching_models(provider, self.query))
        if self._runnable_only:
            models = [model for model in models if self._support_reason(model) is None]
        models.sort(key=self._browse_key)
        self._models = [(model, None) for model in models[: self.limit]]
        ids = [model.qualified_id for model, _ in self._models]
        if not reset_cursor and previous in ids:
            self.cursor = ids.index(previous)
        else:
            self.cursor = 0

    def _matching_models(
        self, provider: ProviderSpec, query: str
    ) -> tuple[ModelSpec, ...]:
        models = tuple(provider.models.values())
        needle = query.strip().casefold()
        if not needle:
            return models
        provider_text = f"{provider.id} {provider.name}".casefold()
        if needle in provider_text:
            return models
        return tuple(
            model
            for model in models
            if needle
            in " ".join(
                value
                for value in (
                    model.id,
                    model.qualified_id,
                    model.name,
                    model.family or "",
                    model.description or "",
                )
                if value
            ).casefold()
        )

    def _provider_key(
        self, provider: ProviderSpec
    ) -> tuple[int, int, int, int, int, str, str]:
        state, _, _, _ = self._provider_state(provider)
        match_rank = 0 if not self.query or self._provider_matches[provider.id] else 1
        state_rank = {"ready": 0, "connect": 1, "catalog": 2}[state]
        preferred = self._preferred_providers.get(provider.id)
        popularity_group, popularity_rank, provider_name, provider_id = (
            provider_popularity_key(provider)
        )
        return (
            match_rank,
            state_rank,
            preferred if preferred is not None else len(self._preferred_providers),
            popularity_group,
            popularity_rank,
            provider_name,
            provider_id,
        )

    def _provider_view(self, provider_id: str, index: int) -> ProviderView:
        provider = self.catalog.provider(provider_id)
        state, supported, profiles, reason = self._provider_state(provider)
        access = provider_access(provider, profiles)
        return ProviderView(
            id=provider.id,
            name=provider.name,
            model_count=len(provider.models),
            runnable_model_count=len(supported),
            match_count=self._provider_matches.get(provider.id, len(provider.models)),
            auth_methods=tuple(dict.fromkeys(route.kind for route in access.routes)),
            access_routes=access.routes,
            required_variables=access.required_variables,
            credential_state=access.credential_state,
            probe_state=_last_probe_state(profiles),
            entitlement_class=_entitlement_class(profiles),
            profile_count=len(profiles),
            state=state,
            state_label={
                "ready": "ready now",
                "connect": "connect access",
                "catalog": "catalogue only",
            }[state],
            support_reason=reason,
            preferred=provider.id in self._preferred_providers,
            active=provider.id == self.active_provider,
            cursor=index == self.provider_cursor,
        )

    def _provider_state(
        self, provider: ProviderSpec
    ) -> tuple[
        str,
        tuple[ModelSpec, ...],
        tuple[CredentialProfile, ...],
        str | None,
    ]:
        supported = tuple(
            model
            for model in provider.models.values()
            if self._support_reason(model) is None
        )
        profiles = self._profiles_for(provider.id)
        if not supported:
            reasons = tuple(
                dict.fromkeys(
                    reason
                    for model in provider.models.values()
                    if (reason := self._support_reason(model)) is not None
                )
            )
            reason = (
                reasons[0] if reasons else "No models are catalogued for this provider."
            )
            return "catalog", supported, profiles, reason
        probe_state = _last_probe_state(profiles)
        if probe_state in FAILED_PROBE_STATES:
            # A credential that was checked and failed is not a credential.
            return "connect", supported, profiles, PROBE_REASONS[probe_state]
        access = provider_access(provider, profiles)
        if access.credential_state == "unavailable":
            return (
                "catalog",
                supported,
                profiles,
                "No way to connect this provider is known.",
            )
        if access.credential_state == "connectable":
            return "connect", supported, profiles, _connect_reason(access)
        return "ready", supported, profiles, None

    @staticmethod
    def _requires_auth(provider: ProviderSpec) -> bool:
        return any(route.needs_credential for route in provider_access(provider).routes)

    def _preview(self, model: ModelSpec) -> ModelPreview:
        provider = self.catalog.provider(model.provider_id)
        profiles = self._profiles_for(model.provider_id)
        access = provider_access(provider, profiles)
        probe_state = _last_probe_state(profiles)
        # A profile that exists but failed its probe is not a usable credential,
        # so the preview must not present the model as ready.
        credential_state = (
            "connectable"
            if probe_state in FAILED_PROBE_STATES
            else access.credential_state
        )
        return ModelPreview(
            id=model.qualified_id,
            provider=model.provider_id,
            name=model.name,
            family=model.family,
            capabilities=tuple(
                sorted(key for key, value in model.capabilities.items() if value)
            ),
            context_limit=model.limits.get("context"),
            variants=tuple(sorted(model.variants)),
            efforts=model.reasoning_options,
            tiers=self._tiers(model),
            route_supported=self._support_reason(model) is None,
            route_support_reason=self._support_reason(model),
            credential_state=credential_state,
            required_variables=access.required_variables,
            probe_state=probe_state,
        )

    def _clear_selection(self) -> None:
        self.selected = None
        self.variant = None
        self.effort = None
        self.tier = None
        self.billing_kind = None
        self.credential_profile = None

    def _options(self, kind: str) -> tuple[str, ...]:
        if self.selected is None:
            return ()
        if kind == "variant":
            return tuple(sorted(self.selected.variants))
        if kind == "effort":
            return self.selected.reasoning_options
        if kind == "tier":
            return self._tiers(self.selected)
        raise ValueError(kind)

    def _supported_default(self, kind: str) -> str | None:
        requested = self._defaults[kind]
        return requested if requested in self._options(kind) else None

    def _derive_access_route(self) -> None:
        selected_profile = next(
            (
                profile
                for profile in self._current_profiles()
                if profile.id == self.credential_profile
            ),
            None,
        )
        self.billing_kind = (
            selected_profile.billing_kind if selected_profile is not None else None
        )

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

    def _support_reason(self, model: ModelSpec | None) -> str | None:
        if model is None or self._route_support is None:
            return None
        return self._route_support(model)

    def _browse_key(self, model: ModelSpec) -> tuple[int, int, str]:
        preferred = self._preferred_models.get(model.qualified_id)
        return (
            0 if preferred is not None else 1,
            preferred if preferred is not None else len(self._preferred_models),
            model.id,
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


def _connect_reason(access: ProviderAccess) -> str:
    """Say what the user must supply, not merely that something is missing."""

    variables = access.required_variables
    if variables:
        return f"Needs {', '.join(variables)} or a stored credential."
    labels = tuple(dict.fromkeys(route.label for route in access.routes))
    if labels:
        return f"Needs access via {' or '.join(labels).lower()}."
    return "Authentication is required."


# A credential that has been probed and failed must not read as usable.
FAILED_PROBE_STATES = frozenset({"expired", "unavailable", "policy_denied"})

PROBE_REASONS = {
    "expired": "The stored credential has expired; sign in again.",
    "policy_denied": "The account is authenticated but access was denied.",
    "unavailable": "The stored credential could not be used.",
}


def _last_probe_state(profiles: Sequence[CredentialProfile]) -> str | None:
    states = [
        str(profile.metadata["last_probe_state"])
        for profile in profiles
        if profile.metadata.get("last_probe_state")
    ]
    if not states:
        return None
    # Report the best outcome any profile achieved; one dead key among several
    # working ones does not make the provider unusable.
    for preferred in ("ready", "unknown", "expired", "policy_denied", "unavailable"):
        if preferred in states:
            return preferred
    return states[0]


def _entitlement_class(profiles: Sequence[CredentialProfile]) -> str | None:
    for profile in profiles:
        value = profile.metadata.get("entitlement_class")
        if value:
            return str(value)
    return None
