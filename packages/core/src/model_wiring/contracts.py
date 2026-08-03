"""Serializable public contracts.

Catalog and selection records are safe to render or send over a local API.
CredentialMaterial is deliberately separate and never participates in their
serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JSON = dict[str, Any]
AUTH_KINDS = frozenset(
    {"api_key", "bearer", "credential_bundle", "oauth", "delegated", "anonymous"}
)
BILLING_KINDS = frozenset({"api", "subscription", "marketplace", "local", "unknown"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_copy(item) for item in value]
    return value


@dataclass(frozen=True)
class AuthMethod:
    kind: str
    billing_kinds: tuple[str, ...] = ("unknown",)
    label: str | None = None
    env: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in AUTH_KINDS:
            raise ValueError(f"unsupported auth kind: {self.kind}")
        invalid = set(self.billing_kinds) - BILLING_KINDS
        if invalid:
            raise ValueError(f"unsupported billing kinds: {sorted(invalid)}")

    def to_dict(self) -> JSON:
        return {
            "kind": self.kind,
            "billing_kinds": list(self.billing_kinds),
            "label": self.label,
            "env": list(self.env),
            "metadata": _json_copy(self.metadata),
        }


@dataclass(frozen=True)
class ModelVariant:
    id: str
    cost: Mapping[str, Any] = field(default_factory=dict)
    provider_options: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JSON:
        return {
            "id": self.id,
            "cost": _json_copy(self.cost),
            "provider_options": _json_copy(self.provider_options),
            "metadata": _json_copy(self.metadata),
        }


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider_id: str
    name: str
    family: str | None = None
    description: str | None = None
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    reasoning_options: tuple[str, ...] = ()
    modalities: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    limits: Mapping[str, int | float] = field(default_factory=dict)
    cost: Mapping[str, Any] = field(default_factory=dict)
    variants: Mapping[str, ModelVariant] = field(default_factory=dict)
    status: str | None = None
    release_date: str | None = None
    last_updated: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def qualified_id(self) -> str:
        return f"{self.provider_id}/{self.id}"

    def to_dict(self) -> JSON:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "qualified_id": self.qualified_id,
            "name": self.name,
            "family": self.family,
            "description": self.description,
            "capabilities": dict(sorted(self.capabilities.items())),
            "reasoning_options": list(self.reasoning_options),
            "modalities": {
                key: list(value) for key, value in sorted(self.modalities.items())
            },
            "limits": dict(sorted(self.limits.items())),
            "cost": _json_copy(self.cost),
            "variants": {
                key: value.to_dict() for key, value in sorted(self.variants.items())
            },
            "status": self.status,
            "release_date": self.release_date,
            "last_updated": self.last_updated,
            "metadata": _json_copy(self.metadata),
        }


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    adapter: str | None = None
    api_url: str | None = None
    doc_url: str | None = None
    env: tuple[str, ...] = ()
    auth_methods: tuple[AuthMethod, ...] = ()
    models: Mapping[str, ModelSpec] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_models: bool = True) -> JSON:
        result: JSON = {
            "id": self.id,
            "name": self.name,
            "adapter": self.adapter,
            "api_url": self.api_url,
            "doc_url": self.doc_url,
            "env": list(self.env),
            "auth_methods": [item.to_dict() for item in self.auth_methods],
            "model_count": len(self.models),
            "metadata": _json_copy(self.metadata),
        }
        if include_models:
            result["models"] = {
                key: value.to_dict() for key, value in sorted(self.models.items())
            }
        return result


@dataclass(frozen=True)
class CatalogSnapshot:
    providers: Mapping[str, ProviderSpec]
    source: str
    fetched_at: str
    digest: str
    aliases: Mapping[str, str] = field(default_factory=dict)
    roles: Mapping[str, str] = field(default_factory=dict)

    @property
    def model_count(self) -> int:
        return sum(len(provider.models) for provider in self.providers.values())

    def to_dict(self, *, include_models: bool = True) -> JSON:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at,
            "digest": self.digest,
            "provider_count": len(self.providers),
            "model_count": self.model_count,
            "aliases": dict(sorted(self.aliases.items())),
            "roles": dict(sorted(self.roles.items())),
            "providers": {
                key: value.to_dict(include_models=include_models)
                for key, value in sorted(self.providers.items())
            },
        }


@dataclass(frozen=True)
class CredentialProfile:
    id: str
    provider_id: str
    auth_kind: str
    billing_kind: str
    secret_ref: str | None = None
    secret_store: str | None = None
    account_label: str | None = None
    priority: int = 100
    enabled: bool = True
    scopes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.auth_kind not in AUTH_KINDS:
            raise ValueError(f"unsupported auth kind: {self.auth_kind}")
        if self.billing_kind not in BILLING_KINDS:
            raise ValueError(f"unsupported billing kind: {self.billing_kind}")
        if self.auth_kind not in {"anonymous", "delegated"} and not self.secret_ref:
            raise ValueError(f"{self.auth_kind} profile requires secret_ref")

    def to_dict(self) -> JSON:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "auth_kind": self.auth_kind,
            "billing_kind": self.billing_kind,
            "secret_ref": self.secret_ref,
            "secret_store": self.secret_store,
            "account_label": self.account_label,
            "priority": self.priority,
            "enabled": self.enabled,
            "scopes": list(self.scopes),
            "metadata": _json_copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CredentialProfile:
        return cls(
            id=str(value["id"]),
            provider_id=str(value["provider_id"]),
            auth_kind=str(value["auth_kind"]),
            billing_kind=str(value.get("billing_kind", "unknown")),
            secret_ref=value.get("secret_ref"),
            secret_store=value.get("secret_store"),
            account_label=value.get("account_label"),
            priority=int(value.get("priority", 100)),
            enabled=bool(value.get("enabled", True)),
            scopes=tuple(str(item) for item in value.get("scopes", ())),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class SelectionIntent:
    model: str | None = None
    provider: str | None = None
    query: str | None = None
    role: str | None = None
    variant: str | None = None
    effort: str | None = None
    tier: str | None = None
    credential_profile: str | None = None
    billing_kind: str | None = None
    required_capabilities: tuple[str, ...] = ()
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    minimum_limits: Mapping[str, int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.billing_kind and self.billing_kind not in BILLING_KINDS:
            raise ValueError(f"unsupported billing kind: {self.billing_kind}")

    def to_dict(self) -> JSON:
        return {
            "model": self.model,
            "provider": self.provider,
            "query": self.query,
            "role": self.role,
            "variant": self.variant,
            "effort": self.effort,
            "tier": self.tier,
            "credential_profile": self.credential_profile,
            "billing_kind": self.billing_kind,
            "required_capabilities": list(self.required_capabilities),
            "input_modalities": list(self.input_modalities),
            "output_modalities": list(self.output_modalities),
            "minimum_limits": dict(sorted(self.minimum_limits.items())),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SelectionIntent:
        return cls(
            model=value.get("model"),
            provider=value.get("provider"),
            query=value.get("query"),
            role=value.get("role"),
            variant=value.get("variant"),
            effort=value.get("effort"),
            tier=value.get("tier"),
            credential_profile=value.get("credential_profile"),
            billing_kind=value.get("billing_kind"),
            required_capabilities=tuple(
                str(item) for item in value.get("required_capabilities", ())
            ),
            input_modalities=tuple(
                str(item) for item in value.get("input_modalities", ())
            ),
            output_modalities=tuple(
                str(item) for item in value.get("output_modalities", ())
            ),
            minimum_limits={
                str(key): number
                for key, number in value.get("minimum_limits", {}).items()
            },
        )


@dataclass(frozen=True)
class SelectionPlan:
    id: str
    provider_id: str
    model_id: str
    model_name: str
    adapter: str | None
    variant: str | None
    effort: str | None
    tier: str | None
    role: str | None
    credential_profile: str | None
    auth_kind: str | None
    billing_kind: str | None
    capabilities: Mapping[str, bool]
    modalities: Mapping[str, tuple[str, ...]]
    limits: Mapping[str, int | float]
    catalog_digest: str
    reasons: tuple[str, ...] = ()
    provider_options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def qualified_model(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    def to_dict(self) -> JSON:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "qualified_model": self.qualified_model,
            "model_name": self.model_name,
            "adapter": self.adapter,
            "variant": self.variant,
            "effort": self.effort,
            "tier": self.tier,
            "role": self.role,
            "credential_profile": self.credential_profile,
            "auth_kind": self.auth_kind,
            "billing_kind": self.billing_kind,
            "capabilities": dict(sorted(self.capabilities.items())),
            "modalities": {
                key: list(value) for key, value in sorted(self.modalities.items())
            },
            "limits": dict(sorted(self.limits.items())),
            "catalog_digest": self.catalog_digest,
            "reasons": list(self.reasons),
            "provider_options": _json_copy(self.provider_options),
        }
