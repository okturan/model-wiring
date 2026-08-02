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
from .profiles import ProfileRegistry
from .selection import Selector

__all__ = [
    "POOL_STRATEGIES",
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
]
