"""Read-only checks that answer "does this credential still work?".

A probe never sends application content to a provider and never returns secret
material. It reports the strongest claim the available evidence supports: a
stored credential that has not been checked is ``unknown``, not ``ready``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .auth import AuthBroker, CredentialLease
from .contracts import CredentialProfile

JSON = dict[str, Any]

PROBE_STATES = ("ready", "expired", "unavailable", "policy_denied", "unknown")

# Billing answers "which account pays"; entitlement answers "what access does
# this identity carry". They are one lookup apart, never inferred from a model.
ENTITLEMENT_BY_BILLING = {
    "subscription": "subscription",
    "api": "usage_api",
    "marketplace": "marketplace",
    "local": "local",
}

# A driver receives a live lease and returns (state, account_fingerprint, detail).
ProbeDriver = Callable[[CredentialLease], tuple[str, str | None, str | None]]

# Deliberately empty. A driver has to call a provider's own endpoint, and this
# package ships no third-party endpoint it cannot verify against a first-party
# source — the same rule the OAuth routes follow. Local evidence (expiry, a
# missing secret, a vanished delegated sign-in) needs no driver; anything else
# reports "unknown" until a consumer registers one here or passes `drivers=`.
PROBE_DRIVERS: dict[str, ProbeDriver] = {}


@dataclass(frozen=True)
class ProbeResult:
    """What a probe could establish, and nothing more."""

    profile_id: str
    provider_id: str
    state: str
    entitlement_class: str | None = None
    account_fingerprint: str | None = None
    detail: str | None = None
    checked_at: float = 0.0

    def __post_init__(self) -> None:
        if self.state not in PROBE_STATES:
            raise ValueError(f"unsupported probe state: {self.state}")

    @property
    def usable(self) -> bool:
        return self.state == "ready"

    def to_dict(self) -> JSON:
        return {
            "profile_id": self.profile_id,
            "provider_id": self.provider_id,
            "state": self.state,
            "entitlement_class": self.entitlement_class,
            "account_fingerprint": self.account_fingerprint,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "usable": self.usable,
        }


def entitlement_for(profile: CredentialProfile) -> str | None:
    return ENTITLEMENT_BY_BILLING.get(profile.billing_kind)


class Prober:
    """Check stored credentials without spending them on real work."""

    def __init__(
        self,
        broker: AuthBroker,
        *,
        drivers: Mapping[str, ProbeDriver] | None = None,
    ) -> None:
        self.broker = broker
        self.drivers = dict(drivers or {})

    def probe(self, profile_id: str, *, record: bool = True) -> ProbeResult:
        try:
            profile = self.broker.profiles.get(profile_id)
        except Exception as exc:
            return ProbeResult(
                profile_id=profile_id,
                provider_id="",
                state="unavailable",
                detail=str(exc),
                checked_at=time.time(),
            )
        result = self._check(profile)
        if record:
            self._record(profile, result)
        return result

    def probe_all(self, *, record: bool = True) -> tuple[ProbeResult, ...]:
        return tuple(
            self.probe(profile.id, record=record) for profile in self._stored_profiles()
        )

    def _stored_profiles(self) -> Iterable[CredentialProfile]:
        return tuple(self.broker.profiles.list())

    def _check(self, profile: CredentialProfile) -> ProbeResult:
        def outcome(
            state: str,
            *,
            fingerprint: str | None = None,
            detail: str | None = None,
        ) -> ProbeResult:
            return ProbeResult(
                profile_id=profile.id,
                provider_id=profile.provider_id,
                state=state,
                entitlement_class=entitlement_for(profile),
                account_fingerprint=fingerprint or _account_fingerprint(profile),
                detail=detail,
                checked_at=time.time(),
            )

        if not profile.enabled:
            return outcome("unavailable", detail="credential profile is disabled")
        if profile.auth_kind == "anonymous":
            return outcome("ready")
        if profile.auth_kind == "delegated":
            return self._check_delegate(profile, outcome)

        # Read expiry straight from the store. Leasing would attempt a refresh,
        # which both hides the expiry and makes a "read-only" check mutate state.
        try:
            expired = self._is_expired(profile)
        except Exception as exc:
            return outcome("unavailable", detail=str(exc))
        if expired:
            return outcome("expired", detail="the stored token is past its expiry")

        driver = self.drivers.get(profile.provider_id)
        if driver is None:
            # Nothing here can confirm the credential works, and claiming
            # otherwise would be a promise we cannot keep.
            return outcome(
                "unknown", detail="no probe driver is registered for this provider"
            )
        try:
            with self.broker.lease(profile.id) as lease:
                state, fingerprint, detail = driver(lease)
        except Exception as exc:
            return outcome("unavailable", detail=str(exc))
        return outcome(state, fingerprint=fingerprint, detail=detail)

    def _is_expired(self, profile: CredentialProfile) -> bool:
        if not profile.secret_ref:
            raise ValueError("credential profile has no secret reference")
        store = self.broker.stores.get(profile.secret_store or "keyring")
        if store is None:
            raise ValueError(f"secret store is not registered: {profile.secret_store}")
        material = store.get(profile.secret_ref)
        try:
            return material.expired()
        finally:
            material.wipe()

    @staticmethod
    def _check_delegate(
        profile: CredentialProfile, outcome: Callable[..., ProbeResult]
    ) -> ProbeResult:
        declared = profile.metadata.get("delegate_path")
        if declared is None:
            return outcome(
                "unknown", detail="the owning tool does not expose a checkable artifact"
            )
        if Path(str(declared)).expanduser().exists():
            return outcome("ready")
        return outcome(
            "unavailable",
            detail=f"the {profile.metadata.get('delegate') or 'delegated'} sign-in is gone",
        )

    def _record(self, profile: CredentialProfile, result: ProbeResult) -> None:
        """Store the outcome as non-secret metadata so surfaces can show it."""

        metadata = dict(profile.metadata)
        metadata["last_probe_state"] = result.state
        metadata["last_probe_at"] = result.checked_at
        if result.entitlement_class:
            metadata["entitlement_class"] = result.entitlement_class
        if result.account_fingerprint:
            metadata["account_fingerprint"] = result.account_fingerprint
        self.broker.profiles.upsert(replace(profile, metadata=metadata))


def _account_fingerprint(profile: CredentialProfile) -> str | None:
    value = profile.metadata.get("account_fingerprint")
    return str(value) if value else None
