"""Deterministic provider/model/profile selection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256

from .catalog import Catalog, canonical_json
from .contracts import CredentialProfile, ModelSpec, SelectionIntent, SelectionPlan
from .errors import (
    AmbiguousSelection,
    IncompatibleSelection,
    ProfileError,
    SelectionNotFound,
)
from .profiles import ProfileRegistry


class Selector:
    def __init__(
        self,
        catalog: Catalog,
        profiles: ProfileRegistry | Sequence[CredentialProfile] | None = None,
    ) -> None:
        self.catalog = catalog
        self.profiles = profiles

    def select(self, intent: SelectionIntent) -> SelectionPlan:
        model, reasons = self._resolve_model(intent)
        provider = self.catalog.provider(model.provider_id)
        self._validate_requirements(intent, model)

        variant = intent.variant
        if variant and variant not in model.variants:
            available = ", ".join(sorted(model.variants)) or "none"
            raise IncompatibleSelection(
                f"{model.qualified_id} does not support variant {variant!r}; available: {available}"
            )
        if variant:
            reasons.append(f"validated model variant {variant}")

        effort = intent.effort
        if effort:
            if not model.capabilities.get("reasoning", False):
                raise IncompatibleSelection(
                    f"{model.qualified_id} does not declare reasoning support"
                )
            if model.reasoning_options and effort not in model.reasoning_options:
                raise IncompatibleSelection(
                    f"{model.qualified_id} does not support effort {effort!r}; "
                    f"available: {', '.join(model.reasoning_options)}"
                )
            reasons.append(f"validated reasoning effort {effort}")

        tier = intent.tier
        tier_options = _tier_options(provider.metadata, model.metadata)
        if tier:
            declared_tiers = _declared_tiers(provider.metadata, model.metadata)
            if tier not in declared_tiers:
                available = ", ".join(declared_tiers) or "none"
                raise IncompatibleSelection(
                    f"{model.qualified_id} does not declare tier {tier!r}; available: {available}"
                )
            reasons.append(f"validated provider tier {tier}")

        profile = self._resolve_profile(intent, provider.id)
        if profile:
            reasons.append(
                f"selected credential profile {profile.id} ({profile.billing_kind} billing)"
            )
        else:
            reasons.append("no credential profile requested")

        provider_options: dict[str, object] = {}
        if variant:
            provider_options.update(model.variants[variant].provider_options)
        if tier:
            provider_options.update(tier_options.get(tier, {}))

        identity = {
            "provider_id": provider.id,
            "model_id": model.id,
            "adapter": provider.adapter,
            "variant": variant,
            "effort": effort,
            "tier": tier,
            "role": intent.role,
            "credential_profile": profile.id if profile else None,
            "auth_kind": profile.auth_kind if profile else None,
            "billing_kind": profile.billing_kind if profile else intent.billing_kind,
            "catalog_digest": self.catalog.snapshot.digest,
            "provider_options": provider_options,
        }
        plan_id = (
            f"sel_{sha256(canonical_json(identity).encode('utf-8')).hexdigest()[:24]}"
        )
        return SelectionPlan(
            id=plan_id,
            provider_id=provider.id,
            model_id=model.id,
            model_name=model.name,
            adapter=provider.adapter,
            variant=variant,
            effort=effort,
            tier=tier,
            role=intent.role,
            credential_profile=profile.id if profile else None,
            auth_kind=profile.auth_kind if profile else None,
            billing_kind=profile.billing_kind if profile else intent.billing_kind,
            capabilities=model.capabilities,
            modalities=model.modalities,
            limits=model.limits,
            catalog_digest=self.catalog.snapshot.digest,
            reasons=tuple(reasons),
            provider_options=provider_options,
        )

    def _resolve_model(self, intent: SelectionIntent) -> tuple[ModelSpec, list[str]]:
        reasons: list[str] = []
        requested = intent.model
        if requested:
            model = self.catalog.model(requested, provider=intent.provider)
            reasons.append(f"resolved exact model {model.qualified_id}")
            return model, reasons
        if intent.role:
            target = self.catalog.snapshot.roles.get(intent.role)
            if not target:
                raise SelectionNotFound(
                    f"catalog has no model for role {intent.role!r}"
                )
            model = self.catalog.model(target, provider=intent.provider)
            reasons.append(f"resolved role {intent.role} to {model.qualified_id}")
            return model, reasons
        if intent.query:
            hits = self.catalog.search(intent.query, provider=intent.provider, limit=5)
            if not hits:
                raise SelectionNotFound(f"no model matches query {intent.query!r}")
            if len(hits) > 1:
                margin = hits[0].score - hits[1].score
                if hits[0].score < 0.78 or margin < 0.08:
                    candidates = tuple(hit.model.qualified_id for hit in hits)
                    raise AmbiguousSelection(
                        f"query {intent.query!r} is ambiguous; choose an exact provider/model",
                        candidates,
                    )
            model = hits[0].model
            reasons.append(
                f"resolved unambiguous search {intent.query!r} to {model.qualified_id}"
            )
            return model, reasons
        raise SelectionNotFound("selection requires model, role, or query")

    def _validate_requirements(self, intent: SelectionIntent, model: ModelSpec) -> None:
        missing = [
            capability
            for capability in intent.required_capabilities
            if not model.capabilities.get(capability, False)
        ]
        if missing:
            raise IncompatibleSelection(
                f"{model.qualified_id} lacks capabilities: {', '.join(sorted(missing))}"
            )
        for direction, required in (
            ("input", intent.input_modalities),
            ("output", intent.output_modalities),
        ):
            available = set(model.modalities.get(direction, ()))
            absent = set(required) - available
            if absent:
                raise IncompatibleSelection(
                    f"{model.qualified_id} lacks {direction} modalities: "
                    f"{', '.join(sorted(absent))}"
                )
        for name, minimum in intent.minimum_limits.items():
            actual = model.limits.get(name)
            if actual is None or actual < minimum:
                raise IncompatibleSelection(
                    f"{model.qualified_id} limit {name!r} is {actual!r}, below {minimum!r}"
                )

    def _resolve_profile(
        self, intent: SelectionIntent, provider_id: str
    ) -> CredentialProfile | None:
        if intent.credential_profile:
            profile = self._get_profile(intent.credential_profile)
            if profile.provider_id != provider_id:
                raise ProfileError(
                    f"profile {profile.id!r} belongs to {profile.provider_id!r}, not {provider_id!r}"
                )
            if not profile.enabled:
                raise ProfileError(f"credential profile is disabled: {profile.id}")
            if intent.billing_kind and profile.billing_kind != intent.billing_kind:
                raise ProfileError(
                    f"profile {profile.id!r} bills as {profile.billing_kind!r}, "
                    f"not requested {intent.billing_kind!r}"
                )
            self._validate_profile_method(profile)
            return profile

        candidates = list(self._list_profiles(provider_id))
        if intent.billing_kind:
            candidates = [
                profile
                for profile in candidates
                if profile.billing_kind == intent.billing_kind
            ]
            if not candidates:
                raise ProfileError(
                    f"no enabled {intent.billing_kind!r} credential profile for {provider_id}"
                )
        elif len({profile.billing_kind for profile in candidates}) > 1:
            kinds = ", ".join(sorted({profile.billing_kind for profile in candidates}))
            raise ProfileError(
                f"provider {provider_id!r} has multiple billing routes ({kinds}); choose one explicitly"
            )
        if not candidates:
            return None
        candidates.sort(key=lambda profile: (profile.priority, profile.id))
        profile = candidates[0]
        self._validate_profile_method(profile)
        return profile

    def _validate_profile_method(self, profile: CredentialProfile) -> None:
        provider = self.catalog.provider(profile.provider_id)
        compatible = [
            method
            for method in provider.auth_methods
            if method.kind == profile.auth_kind
            and (
                profile.billing_kind in method.billing_kinds
                or "unknown" in method.billing_kinds
            )
        ]
        if provider.auth_methods and not compatible:
            raise ProfileError(
                f"profile {profile.id!r} auth/billing route is not declared by provider "
                f"{provider.id!r}"
            )

    def _get_profile(self, profile_id: str) -> CredentialProfile:
        if self.profiles is None:
            raise ProfileError("no credential profile registry was supplied")
        if isinstance(self.profiles, ProfileRegistry):
            return self.profiles.get(profile_id)
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        raise ProfileError(f"unknown credential profile: {profile_id}")

    def _list_profiles(self, provider_id: str) -> Iterable[CredentialProfile]:
        if self.profiles is None:
            return ()
        if isinstance(self.profiles, ProfileRegistry):
            return self.profiles.list(provider_id=provider_id, enabled_only=True)
        return tuple(
            profile
            for profile in self.profiles
            if profile.provider_id == provider_id and profile.enabled
        )


def _declared_tiers(
    provider_metadata: Mapping[str, object], model_metadata: Mapping[str, object]
) -> tuple[str, ...]:
    values: list[str] = []
    for metadata in (provider_metadata, model_metadata):
        tiers = metadata.get("tiers")
        if isinstance(tiers, (list, tuple)):
            values.extend(str(item) for item in tiers)
    return tuple(dict.fromkeys(values))


def _tier_options(
    provider_metadata: Mapping[str, object], model_metadata: Mapping[str, object]
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for metadata in (provider_metadata, model_metadata):
        raw = metadata.get("tier_options")
        if isinstance(raw, Mapping):
            for tier, options in raw.items():
                if isinstance(options, Mapping):
                    result[str(tier)] = dict(options)
    return result
