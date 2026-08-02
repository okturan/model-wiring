"""Non-secret description of how a provider can actually be connected.

A catalogue entry answers "does this provider exist?". An access route answers
"what would I have to do to use it?" — which credential, which billing account,
and which documentation issues it. Routes never carry secret material, so they
are safe for every surface, log, and API payload.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .contracts import AUTH_KINDS, BILLING_KINDS, CredentialProfile, ProviderSpec

JSON = dict[str, Any]

# Routes that need no credential are "configured" the moment they are
# catalogued; everything else needs a profile before an application may run it.
CREDENTIAL_FREE_KINDS = frozenset({"anonymous"})

_DEFAULT_LABELS = {
    "api_key": "API key",
    "bearer": "Bearer token",
    "credential_bundle": "Credential bundle",
    "oauth": "Browser sign-in",
    "delegated": "Existing client login",
    "anonymous": "No credential required",
}


@dataclass(frozen=True)
class AccessRoute:
    """One concrete way to obtain access to a provider."""

    id: str
    kind: str
    billing_kind: str
    label: str
    env: tuple[str, ...] = ()
    doc_url: str | None = None
    driver: str | None = None
    instructions: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in AUTH_KINDS:
            raise ValueError(f"unsupported auth kind: {self.kind}")
        if self.billing_kind not in BILLING_KINDS:
            raise ValueError(f"unsupported billing kind: {self.billing_kind}")

    @property
    def needs_credential(self) -> bool:
        return self.kind not in CREDENTIAL_FREE_KINDS

    def to_dict(self) -> JSON:
        return {
            "id": self.id,
            "kind": self.kind,
            "billing_kind": self.billing_kind,
            "label": self.label,
            "env": list(self.env),
            "doc_url": self.doc_url,
            "driver": self.driver,
            "instructions": self.instructions,
            "needs_credential": self.needs_credential,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AccessRoute:
        kind = str(value.get("kind") or "api_key")
        return cls(
            id=str(value.get("id") or kind),
            kind=kind,
            billing_kind=str(value.get("billing_kind") or "unknown"),
            label=str(value.get("label") or _DEFAULT_LABELS.get(kind) or kind),
            env=tuple(str(item) for item in value.get("env", ()) if item),
            doc_url=_optional(value.get("doc_url")),
            driver=_optional(value.get("driver")),
            instructions=_optional(value.get("instructions")),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ProviderAccess:
    """Whether a provider can be connected, and whether it already is."""

    provider_id: str
    routes: tuple[AccessRoute, ...]
    profiles: tuple[CredentialProfile, ...]
    credential_state: str

    @property
    def connectable(self) -> bool:
        return bool(self.routes)

    @property
    def required_variables(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for route in self.routes:
            for name in route.env:
                seen.setdefault(name, None)
        return tuple(seen)

    def to_dict(self) -> JSON:
        return {
            "provider_id": self.provider_id,
            "routes": [route.to_dict() for route in self.routes],
            "profile_ids": [profile.id for profile in self.profiles],
            "credential_state": self.credential_state,
            "connectable": self.connectable,
            "required_variables": list(self.required_variables),
        }


def provider_access_routes(provider: ProviderSpec) -> tuple[AccessRoute, ...]:
    """Return every known way to connect ``provider``.

    An overlay may declare ``metadata.access_routes`` to describe routes the
    upstream catalogue cannot know about, including an empty list to state that
    no route is known.
    """

    declared = provider.metadata.get("access_routes")
    if isinstance(declared, Iterable) and not isinstance(declared, (str, bytes)):
        return tuple(
            AccessRoute.from_dict(item)
            for item in declared
            if isinstance(item, Mapping)
        )
    return _derived_routes(provider)


def provider_access(
    provider: ProviderSpec,
    profiles: Iterable[CredentialProfile] = (),
) -> ProviderAccess:
    """Describe how ``provider`` can be connected and whether it already is."""

    routes = provider_access_routes(provider)
    stored = tuple(profiles)
    if stored:
        state = "configured"
    elif not routes:
        state = "unavailable"
    elif all(not route.needs_credential for route in routes):
        state = "configured"
    else:
        state = "connectable"
    return ProviderAccess(
        provider_id=provider.id,
        routes=routes,
        profiles=stored,
        credential_state=state,
    )


def _derived_routes(provider: ProviderSpec) -> tuple[AccessRoute, ...]:
    routes: list[AccessRoute] = []
    for method in provider.auth_methods:
        billing = method.billing_kinds[0] if method.billing_kinds else "unknown"
        routes.append(
            AccessRoute(
                id=method.metadata.get("route_id") or method.kind,
                kind=method.kind,
                billing_kind=billing,
                label=method.label or _DEFAULT_LABELS.get(method.kind, method.kind),
                env=method.env,
                doc_url=provider.doc_url,
                driver=_default_driver(method.kind),
                metadata=method.metadata,
            )
        )
    if routes:
        return tuple(routes)
    return (
        AccessRoute(
            id="anonymous",
            kind="anonymous",
            billing_kind="local",
            label=_DEFAULT_LABELS["anonymous"],
            doc_url=provider.doc_url,
        ),
    )


def _default_driver(kind: str) -> str | None:
    return {
        "api_key": "api_key_paste",
        "bearer": "api_key_paste",
        "credential_bundle": "credential_bundle_paste",
        "oauth": "oauth_pkce",
        "delegated": "delegated_import",
    }.get(kind)


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
