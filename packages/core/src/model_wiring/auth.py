"""Secret stores and the short-lived credential lease boundary."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, Self

from .contracts import CredentialProfile
from .errors import CredentialError, RefreshError, SecretNotFound
from .profiles import ProfileRegistry


class CredentialMaterial:
    """Mutable secret bytes that can be best-effort wiped after use.

    Python cannot guarantee removal of every temporary copy made by an SDK, but
    this object avoids long-lived immutable strings and clears its own buffers.
    """

    __slots__ = ("_secrets", "_wiped", "expires_at", "metadata")

    def __init__(
        self,
        secrets: Mapping[str, str | bytes | bytearray],
        *,
        expires_at: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._secrets = {
            str(key): bytearray(
                value.encode("utf-8") if isinstance(value, str) else value
            )
            for key, value in secrets.items()
        }
        self.expires_at = expires_at
        self.metadata = dict(metadata or {})
        self._wiped = False

    def __repr__(self) -> str:
        return (
            f"CredentialMaterial(keys={sorted(self._secrets)}, "
            f"expires_at={self.expires_at!r}, value=<redacted>)"
        )

    def reveal(self, key: str | None = None) -> str:
        if self._wiped:
            raise CredentialError("credential material has been wiped")
        selected = key
        if selected is None:
            selected = next(
                (
                    candidate
                    for candidate in (
                        "api_key",
                        "access_token",
                        "bearer_token",
                        "token",
                    )
                    if candidate in self._secrets
                ),
                None,
            )
        if selected is None or selected not in self._secrets:
            raise CredentialError(f"credential has no secret field {selected!r}")
        return self._secrets[selected].decode("utf-8")

    def has(self, key: str) -> bool:
        return not self._wiped and key in self._secrets

    def expired(self, *, skew_seconds: float = 60.0) -> bool:
        return (
            self.expires_at is not None
            and self.expires_at <= time.time() + skew_seconds
        )

    def clone(self) -> CredentialMaterial:
        if self._wiped:
            raise CredentialError("credential material has been wiped")
        return CredentialMaterial(
            {key: bytes(value) for key, value in self._secrets.items()},
            expires_at=self.expires_at,
            metadata=self.metadata,
        )

    def private_dict(self) -> dict[str, Any]:
        if self._wiped:
            raise CredentialError("credential material has been wiped")
        return {
            "secrets": {
                key: value.decode("utf-8") for key, value in self._secrets.items()
            },
            "expires_at": self.expires_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_private_dict(cls, value: Mapping[str, Any]) -> CredentialMaterial:
        return cls(
            dict(value.get("secrets") or {}),
            expires_at=value.get("expires_at"),
            metadata=dict(value.get("metadata") or {}),
        )

    def wipe(self) -> None:
        for value in self._secrets.values():
            value[:] = b"\x00" * len(value)
        self._secrets.clear()
        self._wiped = True


class SecretStore(Protocol):
    name: str

    def get(self, reference: str) -> CredentialMaterial: ...

    def put(self, reference: str, material: CredentialMaterial) -> None: ...

    def delete(self, reference: str) -> bool: ...


class MemorySecretStore:
    name = "memory"

    def __init__(self) -> None:
        self._values: dict[str, CredentialMaterial] = {}
        self._lock = threading.RLock()

    def get(self, reference: str) -> CredentialMaterial:
        with self._lock:
            try:
                return self._values[reference].clone()
            except KeyError as exc:
                raise SecretNotFound(f"memory secret not found: {reference}") from exc

    def put(self, reference: str, material: CredentialMaterial) -> None:
        with self._lock:
            previous = self._values.get(reference)
            self._values[reference] = material.clone()
            if previous is not None:
                previous.wipe()

    def delete(self, reference: str) -> bool:
        with self._lock:
            previous = self._values.pop(reference, None)
            if previous is None:
                return False
            previous.wipe()
            return True


class EnvironmentSecretStore:
    """Read-only store for one variable or a comma-separated variable bundle."""

    name = "environment"

    def get(self, reference: str) -> CredentialMaterial:
        names = _environment_references(reference)
        missing = [name for name in names if os.environ.get(name) is None]
        if missing:
            raise SecretNotFound(
                f"environment variables are not set: {', '.join(missing)}"
            )
        if len(names) == 1:
            secrets = {"api_key": os.environ[names[0]]}
        else:
            secrets = {name: os.environ[name] for name in names}
        return CredentialMaterial(
            secrets,
            metadata={"source": "environment", "variables": list(names)},
        )

    def put(self, reference: str, material: CredentialMaterial) -> None:
        del reference, material
        raise CredentialError("environment secret store is read-only")

    def delete(self, reference: str) -> bool:
        del reference
        raise CredentialError("environment secret store is read-only")


class KeyringSecretStore:
    """Optional OS-keyring backend. Install `model-wiring[keyring]`."""

    name = "keyring"

    def __init__(self, service: str = "model-wiring") -> None:
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CredentialError(
                "keyring backend requires: pip install 'model-wiring[keyring]'"
            ) from exc
        self._keyring = keyring
        self._service = service

    def get(self, reference: str) -> CredentialMaterial:
        value = self._keyring.get_password(self._service, reference)
        if value is None:
            raise SecretNotFound(f"keyring secret not found: {reference}")
        try:
            return CredentialMaterial.from_private_dict(json.loads(value))
        except (TypeError, json.JSONDecodeError) as exc:
            raise CredentialError(f"invalid keyring material for {reference}") from exc

    def put(self, reference: str, material: CredentialMaterial) -> None:
        self._keyring.set_password(
            self._service,
            reference,
            json.dumps(material.private_dict(), sort_keys=True, separators=(",", ":")),
        )

    def delete(self, reference: str) -> bool:
        try:
            self._keyring.delete_password(self._service, reference)
            return True
        except self._keyring.errors.PasswordDeleteError:
            return False


class RefreshDriver(Protocol):
    def refresh(
        self, profile: CredentialProfile, material: CredentialMaterial
    ) -> CredentialMaterial: ...


class CredentialLease:
    """A short-lived credential or delegated-client reference."""

    __slots__ = ("_closed", "delegate", "material", "profile")

    def __init__(
        self,
        profile: CredentialProfile,
        *,
        material: CredentialMaterial | None = None,
        delegate: str | None = None,
    ) -> None:
        self.profile = profile
        self.material = material
        self.delegate = delegate
        self._closed = False

    def __repr__(self) -> str:
        return (
            f"CredentialLease(profile={self.profile.id!r}, auth={self.profile.auth_kind!r}, "
            "value=<redacted>)"
        )

    def reveal(self, key: str | None = None) -> str:
        if self._closed or self.material is None:
            raise CredentialError("lease has no revealable credential material")
        return self.material.reveal(key)

    def close(self) -> None:
        if self.material is not None:
            self.material.wipe()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class AuthBroker:
    def __init__(
        self,
        profiles: ProfileRegistry,
        *,
        stores: Mapping[str, SecretStore] | None = None,
        refresh_drivers: Mapping[str, RefreshDriver] | None = None,
    ) -> None:
        base: dict[str, SecretStore] = {
            "memory": MemorySecretStore(),
            "environment": EnvironmentSecretStore(),
        }
        base.update(stores or {})
        self.profiles = profiles
        self.stores = base
        self.refresh_drivers = dict(refresh_drivers or {})

    @contextmanager
    def lease(self, profile_id: str) -> Iterator[CredentialLease]:
        profile = self.profiles.get(profile_id)
        if not profile.enabled:
            raise CredentialError(f"credential profile is disabled: {profile_id}")
        if profile.auth_kind == "anonymous":
            lease = CredentialLease(profile)
        elif profile.auth_kind == "delegated":
            delegate = str(profile.metadata.get("delegate") or profile.provider_id)
            lease = CredentialLease(profile, delegate=delegate)
        else:
            if not profile.secret_ref:
                raise CredentialError(
                    f"credential profile has no secret reference: {profile_id}"
                )
            store_name = profile.secret_store or "keyring"
            try:
                store = self.stores[store_name]
            except KeyError as exc:
                raise CredentialError(
                    f"secret store is not registered: {store_name}"
                ) from exc
            material = store.get(profile.secret_ref)
            if profile.auth_kind == "oauth" and material.expired():
                material.wipe()
                material = self._refresh(profile, store)
            lease = CredentialLease(profile, material=material)
        try:
            yield lease
        finally:
            lease.close()

    def _refresh(
        self, profile: CredentialProfile, store: SecretStore
    ) -> CredentialMaterial:
        try:
            driver = self.refresh_drivers[profile.provider_id]
        except KeyError as exc:
            raise RefreshError(
                f"no OAuth refresh driver registered for provider {profile.provider_id}"
            ) from exc
        if not profile.secret_ref:
            raise RefreshError(f"OAuth profile has no secret reference: {profile.id}")
        with self.profiles.refresh_lease(profile.id):
            current = store.get(profile.secret_ref)
            if not current.expired():
                return current
            try:
                refreshed = driver.refresh(profile, current)
            finally:
                current.wipe()
            try:
                store.put(profile.secret_ref, refreshed)
                return refreshed.clone()
            finally:
                refreshed.wipe()


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _environment_references(reference: str) -> tuple[str, ...]:
    names = tuple(item.strip() for item in reference.split(",") if item.strip())
    if not names or any(not _ENVIRONMENT_NAME.fullmatch(name) for name in names):
        raise CredentialError("invalid environment credential reference")
    return names
