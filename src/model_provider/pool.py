"""Credential-pool choice independent from model routing and execution."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .contracts import CredentialProfile
from .errors import ProfileError
from .profiles import ProfileRegistry

POOL_STRATEGIES = frozenset({"fill_first", "round_robin", "least_used"})


@dataclass(frozen=True)
class CredentialPool:
    id: str
    profile_ids: tuple[str, ...]
    strategy: str = "fill_first"

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("credential pool ID must not be empty")
        if not self.profile_ids:
            raise ValueError("credential pool must contain at least one profile")
        if len(set(self.profile_ids)) != len(self.profile_ids):
            raise ValueError("credential pool profile IDs must be unique")
        if self.strategy not in POOL_STRATEGIES:
            raise ValueError(f"unsupported credential pool strategy: {self.strategy}")

    @property
    def digest(self) -> str:
        value = "\0".join((self.id, self.strategy, *self.profile_ids))
        return sha256(value.encode("utf-8")).hexdigest()

    def claim(self, registry: ProfileRegistry) -> CredentialProfile:
        profile = registry.claim_from_pool(
            self.profile_ids,
            pool_id=f"{self.id}:{self.digest[:16]}",
            strategy=self.strategy,
        )
        if not profile.enabled:
            raise ProfileError(
                f"credential pool selected disabled profile: {profile.id}"
            )
        return profile
