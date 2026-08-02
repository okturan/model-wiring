"""Non-secret discovery of configured provider credential routes."""

from __future__ import annotations

import os
from collections.abc import Mapping

from .catalog import Catalog
from .contracts import CredentialProfile


def discover_environment_profiles(
    catalog: Catalog,
    *,
    environ: Mapping[str, str] | None = None,
    priority: int = 500,
) -> tuple[CredentialProfile, ...]:
    """Return profile metadata for complete provider environment bundles.

    Values are only checked for presence. They are never copied into the profile
    or returned from this function.
    """

    source = os.environ if environ is None else environ
    profiles: list[CredentialProfile] = []
    for provider in sorted(
        catalog.snapshot.providers.values(), key=lambda item: item.id
    ):
        if not provider.env or not all(source.get(name) for name in provider.env):
            continue
        auth_kind = "api_key" if len(provider.env) == 1 else "credential_bundle"
        profiles.append(
            CredentialProfile(
                id=f"env:{provider.id}",
                provider_id=provider.id,
                auth_kind=auth_kind,
                billing_kind="api",
                secret_ref=",".join(provider.env),
                secret_store="environment",
                account_label="environment",
                priority=priority,
                metadata={"variables": list(provider.env), "discovered": True},
            )
        )
    return tuple(profiles)
