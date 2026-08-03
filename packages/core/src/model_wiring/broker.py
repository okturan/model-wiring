"""Canonical refresh ownership and the documented credential precedence chain.

Refresh is the only operation that writes credential material, so it happens in
exactly one place: here, inside `ProfileRegistry.refresh_lease()` and ahead of
expiry rather than after it. Clients keep reading through `AuthBroker.lease()`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from .auth import AuthBroker, CredentialLease, CredentialMaterial, SecretStore
from .catalog import Catalog
from .contracts import CredentialProfile
from .discovery import discover_environment_profiles
from .errors import CredentialError, CredentialUnavailable, RefreshError
from .profiles import ProfileRegistry

JSON = dict[str, Any]

# Wide enough that a caller leasing a credential never races its expiry, narrow
# enough that a process start does not spend a refresh token for no reason.
DEFAULT_REFRESH_SKEW_SECONDS = 300.0

# The chain in precedence order. Every rung is tried in turn and the first that
# qualifies wins; exhausting them is a failure, never a silent substitution.
RESOLUTION_SOURCES = ("runtime", "selected", "stored", "environment")

RUNTIME_SOURCE = "runtime"


@dataclass(frozen=True)
class CredentialSnapshot:
    """Observable broker state: identity and lifecycle, never material."""

    profile_id: str
    provider_id: str
    auth_kind: str
    billing_kind: str
    enabled: bool
    expires_at: float | None = None
    last_probe_state: str | None = None
    last_probe_at: float | None = None

    def to_dict(self) -> JSON:
        return {
            "profile_id": self.profile_id,
            "provider_id": self.provider_id,
            "auth_kind": self.auth_kind,
            "billing_kind": self.billing_kind,
            "enabled": self.enabled,
            "expires_at": self.expires_at,
            "last_probe_state": self.last_probe_state,
            "last_probe_at": self.last_probe_at,
        }


class RuntimeCredential:
    """Credential material supplied at call time and never persisted.

    It outranks every stored profile, so an application already holding a secret
    can spend it without this kit writing it anywhere.
    """

    __slots__ = ("_material", "profile")

    def __init__(
        self,
        provider_id: str,
        secrets: Mapping[str, str | bytes | bytearray],
        *,
        auth_kind: str = "api_key",
        billing_kind: str = "unknown",
        expires_at: float | None = None,
        label: str | None = None,
    ) -> None:
        self.profile = CredentialProfile(
            id=f"{RUNTIME_SOURCE}:{provider_id}",
            provider_id=provider_id,
            auth_kind=auth_kind,
            billing_kind=billing_kind,
            secret_ref=RUNTIME_SOURCE,
            secret_store=RUNTIME_SOURCE,
            account_label=label,
            priority=0,
            metadata={"source": RUNTIME_SOURCE, "persisted": False},
        )
        self._material = CredentialMaterial(
            secrets, expires_at=expires_at, metadata={"source": RUNTIME_SOURCE}
        )

    def __repr__(self) -> str:
        return (
            f"RuntimeCredential(provider_id={self.provider_id!r}, "
            f"auth_kind={self.profile.auth_kind!r}, value=<redacted>)"
        )

    @property
    def provider_id(self) -> str:
        return self.profile.provider_id

    def material(self) -> CredentialMaterial:
        """A clone a lease may wipe without spending the caller's original."""

        return self._material.clone()

    def wipe(self) -> None:
        self._material.wipe()


@dataclass(frozen=True)
class CredentialResolution:
    """Which rung of the precedence chain supplied a credential, and why."""

    provider_id: str
    source: str
    profile: CredentialProfile
    considered: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source not in RESOLUTION_SOURCES:
            raise ValueError(f"unsupported resolution source: {self.source}")

    @property
    def persisted(self) -> bool:
        return self.source != RUNTIME_SOURCE

    def to_dict(self) -> JSON:
        return {
            "provider_id": self.provider_id,
            "source": self.source,
            "profile_id": self.profile.id,
            "profile": self.profile.to_dict(),
            "persisted": self.persisted,
            "considered": list(self.considered),
        }


class CredentialBroker:
    """The single canonical writer for refreshed credential material.

    Every refresh runs inside the profile's `refresh_lease()`, so two processes
    sharing one profile store cannot refresh in parallel: the loser waits,
    re-reads, and finds the material already fresh.
    """

    def __init__(
        self,
        auth: AuthBroker,
        *,
        catalog: Catalog | None = None,
        environ: Mapping[str, str] | None = None,
        refresh_skew_seconds: float = DEFAULT_REFRESH_SKEW_SECONDS,
        lease_ttl_seconds: float = 30.0,
        lease_wait_seconds: float = 15.0,
    ) -> None:
        self.auth = auth
        self.catalog = catalog
        self.environ = environ
        self.refresh_skew_seconds = float(refresh_skew_seconds)
        self.lease_ttl_seconds = float(lease_ttl_seconds)
        self.lease_wait_seconds = float(lease_wait_seconds)

    @property
    def profiles(self) -> ProfileRegistry:
        return self.auth.profiles

    def resolve(
        self,
        provider_id: str,
        *,
        profile_id: str | None = None,
        billing_kind: str | None = None,
        runtime: RuntimeCredential | None = None,
    ) -> CredentialResolution:
        """Return the first candidate the precedence chain accepts.

        Runtime-supplied, then explicitly selected, then the highest-priority
        enabled stored profile, then a discovered environment profile. No secret
        is read and no request is sent while deciding.
        """

        considered: list[str] = []
        if runtime is not None:
            if runtime.provider_id != provider_id:
                raise CredentialError(
                    f"runtime credential is for provider {runtime.provider_id}, "
                    f"not {provider_id}"
                )
            # Supplying material at call time is itself an explicit choice, so
            # its billing kind is the caller's decision rather than a fallback.
            return self._resolution(
                provider_id, RUNTIME_SOURCE, runtime.profile, considered
            )
        considered.append("runtime: none supplied")

        if profile_id:
            profile = self.profiles.get(profile_id)
            if profile.provider_id != provider_id:
                raise CredentialError(
                    f"credential profile {profile_id} belongs to provider "
                    f"{profile.provider_id}, not {provider_id}"
                )
            if not profile.enabled:
                raise CredentialError(f"credential profile is disabled: {profile_id}")
            return self._resolution(provider_id, "selected", profile, considered)
        considered.append("selected: none requested")

        stored = self.profiles.list(provider_id=provider_id)
        for profile in stored:
            skipped = _skip_reason(profile, billing_kind)
            if skipped is None:
                return self._resolution(provider_id, "stored", profile, considered)
            considered.append(f"stored {profile.id}: {skipped}")
        if not stored:
            considered.append(f"stored: no profile for {provider_id}")

        for profile in self._environment_profiles(provider_id, considered):
            skipped = _skip_reason(profile, billing_kind)
            if skipped is None:
                return self._resolution(provider_id, "environment", profile, considered)
            considered.append(f"environment {profile.id}: {skipped}")

        wanted = f" with billing kind {billing_kind}" if billing_kind else ""
        raise CredentialUnavailable(
            f"no credential available for provider {provider_id}{wanted}; "
            f"considered {'; '.join(considered)}",
            tuple(considered),
        )

    @contextmanager
    def lease(
        self,
        provider_id: str,
        *,
        profile_id: str | None = None,
        billing_kind: str | None = None,
        runtime: RuntimeCredential | None = None,
    ) -> Iterator[CredentialLease]:
        """Resolve a credential and yield it, refreshing ahead of expiry first."""

        resolution = self.resolve(
            provider_id,
            profile_id=profile_id,
            billing_kind=billing_kind,
            runtime=runtime,
        )
        profile = resolution.profile
        if runtime is not None and resolution.source == RUNTIME_SOURCE:
            material = runtime.material()
        elif resolution.source == "environment":
            # A discovered profile has no registry row, so it is read from its
            # store directly rather than through AuthBroker.lease().
            material = self._store(profile).get(self._reference(profile))
        else:
            self.refresh(profile.id)
            with self.auth.lease(profile.id) as stored:
                yield stored
            return
        with CredentialLease(profile, material=material) as unstored:
            yield unstored

    def refresh(self, profile_id: str) -> bool:
        """Refresh under the profile's lease when expiry is inside the skew.

        Returns whether this broker was the writer.
        """

        profile = self._enabled_profile(profile_id)
        if profile.auth_kind != "oauth":
            return False
        store = self._store(profile)
        reference = self._reference(profile)
        if not self._due(store, reference):
            return False
        try:
            driver = self.auth.refresh_drivers[profile.provider_id]
        except KeyError as exc:
            raise RefreshError(
                f"no OAuth refresh driver registered for provider {profile.provider_id}"
            ) from exc
        with self.profiles.refresh_lease(
            profile_id,
            ttl_seconds=self.lease_ttl_seconds,
            wait_seconds=self.lease_wait_seconds,
        ):
            # Whoever held the lease before us may already have written.
            if not self._due(store, reference):
                return False
            current = store.get(reference)
            try:
                refreshed = driver.refresh(profile, current)
            except Exception as exc:
                raise RefreshError(
                    f"credential refresh failed for profile {profile_id}"
                ) from exc
            finally:
                current.wipe()
            try:
                store.put(reference, refreshed)
            finally:
                refreshed.wipe()
        return True

    def refresh_due(self) -> tuple[str, ...]:
        """Refresh every enabled profile inside the skew; return the ids written.

        A failure stops the sweep; a caller needing isolation between profiles
        refreshes them one at a time.
        """

        return tuple(
            profile.id
            for profile in self.profiles.list(enabled_only=True)
            if self.refresh(profile.id)
        )

    def disable(self, profile_id: str) -> CredentialProfile:
        """Take a profile out of service without deleting its material."""

        return self._set_enabled(profile_id, False)

    def enable(self, profile_id: str) -> CredentialProfile:
        return self._set_enabled(profile_id, True)

    def snapshot(
        self, *, provider_id: str | None = None
    ) -> tuple[CredentialSnapshot, ...]:
        """Return redacted state for every stored profile."""

        return tuple(
            self._snapshot(profile)
            for profile in self.profiles.list(provider_id=provider_id)
        )

    @staticmethod
    def _resolution(
        provider_id: str,
        source: str,
        profile: CredentialProfile,
        considered: list[str],
    ) -> CredentialResolution:
        return CredentialResolution(
            provider_id=provider_id,
            source=source,
            profile=profile,
            considered=(*considered, f"{source} {profile.id}: accepted"),
        )

    def _environment_profiles(
        self, provider_id: str, considered: list[str]
    ) -> tuple[CredentialProfile, ...]:
        if self.catalog is None:
            considered.append("environment: no catalogue to discover variables from")
            return ()
        discovered = tuple(
            profile
            for profile in discover_environment_profiles(
                self.catalog, environ=self.environ
            )
            if profile.provider_id == provider_id
        )
        if not discovered:
            considered.append(
                f"environment: no complete variable set for {provider_id}"
            )
        return discovered

    def _set_enabled(self, profile_id: str, enabled: bool) -> CredentialProfile:
        updated = replace(self.profiles.get(profile_id), enabled=enabled)
        self.profiles.upsert(updated)
        return updated

    def _snapshot(self, profile: CredentialProfile) -> CredentialSnapshot:
        return CredentialSnapshot(
            profile_id=profile.id,
            provider_id=profile.provider_id,
            auth_kind=profile.auth_kind,
            billing_kind=profile.billing_kind,
            enabled=profile.enabled,
            expires_at=self._expires_at(profile),
            last_probe_state=_optional_text(profile.metadata.get("last_probe_state")),
            last_probe_at=_optional_number(profile.metadata.get("last_probe_at")),
        )

    def _expires_at(self, profile: CredentialProfile) -> float | None:
        if not profile.secret_ref:
            return None
        try:
            material = self._store(profile).get(profile.secret_ref)
        except CredentialError:
            return None
        try:
            return material.expires_at
        finally:
            material.wipe()

    def _enabled_profile(self, profile_id: str) -> CredentialProfile:
        profile = self.profiles.get(profile_id)
        if not profile.enabled:
            raise CredentialError(f"credential profile is disabled: {profile_id}")
        return profile

    def _store(self, profile: CredentialProfile) -> SecretStore:
        name = profile.secret_store or "keyring"
        try:
            return self.auth.stores[name]
        except KeyError as exc:
            raise CredentialError(f"secret store is not registered: {name}") from exc

    @staticmethod
    def _reference(profile: CredentialProfile) -> str:
        if not profile.secret_ref:
            raise CredentialError(
                f"credential profile has no secret reference: {profile.id}"
            )
        return profile.secret_ref

    def _due(self, store: SecretStore, reference: str) -> bool:
        material = store.get(reference)
        try:
            return material.expired(skew_seconds=self.refresh_skew_seconds)
        finally:
            material.wipe()


def _skip_reason(profile: CredentialProfile, billing_kind: str | None) -> str | None:
    """Why this candidate cannot be used, or None when it can."""

    if not profile.enabled:
        return "disabled"
    if billing_kind and profile.billing_kind != billing_kind:
        # Subscription and metered access are different economic paths; moving
        # between them is a user decision, never a fallback.
        return f"billing kind {profile.billing_kind}, not {billing_kind}"
    return None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_number(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
