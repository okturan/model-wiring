"""Provider/model/auth selection without an agent runtime."""

from .auth import (
    AuthBroker,
    CredentialLease,
    CredentialMaterial,
    EnvironmentSecretStore,
    KeyringSecretStore,
    MemorySecretStore,
    SecretStore,
)
from .catalog import Catalog, ModelsDevSource, SearchHit, load_overlay
from .contracts import (
    AuthMethod,
    CatalogSnapshot,
    CredentialProfile,
    ModelSpec,
    ModelVariant,
    ProviderSpec,
    SelectionIntent,
    SelectionPlan,
)
from .discovery import discover_environment_profiles
from .oauth import (
    AuthorizationPending,
    AuthorizationRequest,
    DeviceAuthorization,
    OAuthClient,
    OAuthError,
    OAuthProviderConfig,
)
from .pool import POOL_STRATEGIES, CredentialPool
from .popularity import (
    POPULAR_PROVIDER_IDS,
    provider_popularity_key,
    provider_popularity_rank,
)
from .profiles import ProfileRegistry
from .selection import Selector

__all__ = [
    "POOL_STRATEGIES",
    "POPULAR_PROVIDER_IDS",
    "AuthBroker",
    "AuthMethod",
    "AuthorizationPending",
    "AuthorizationRequest",
    "Catalog",
    "CatalogSnapshot",
    "CredentialLease",
    "CredentialMaterial",
    "CredentialPool",
    "CredentialProfile",
    "DeviceAuthorization",
    "EnvironmentSecretStore",
    "KeyringSecretStore",
    "MemorySecretStore",
    "ModelSpec",
    "ModelVariant",
    "ModelsDevSource",
    "OAuthClient",
    "OAuthError",
    "OAuthProviderConfig",
    "ProfileRegistry",
    "ProviderSpec",
    "SearchHit",
    "SecretStore",
    "SelectionIntent",
    "SelectionPlan",
    "Selector",
    "discover_environment_profiles",
    "load_overlay",
    "provider_popularity_key",
    "provider_popularity_rank",
]
