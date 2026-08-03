"""Provider/model/auth selection without an agent runtime."""

from .access import (
    AccessRoute,
    ProviderAccess,
    provider_access,
    provider_access_routes,
)
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
from .login import (
    ChoicePrompt,
    LoginBroker,
    LoginDriver,
    LoginResult,
    LoginSession,
    OpenUrlPrompt,
    SecretPrompt,
    UserCodePrompt,
)
from .oauth import (
    AuthorizationPending,
    AuthorizationRequest,
    DeviceAuthorization,
    OAuthClient,
    OAuthError,
    OAuthProviderConfig,
)
from .oauth_login import (
    LoopbackRedirect,
    OAuthDeviceDriver,
    OAuthPkceDriver,
    route_oauth_config,
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
    "AccessRoute",
    "AuthBroker",
    "AuthMethod",
    "AuthorizationPending",
    "AuthorizationRequest",
    "Catalog",
    "CatalogSnapshot",
    "ChoicePrompt",
    "CredentialLease",
    "CredentialMaterial",
    "CredentialPool",
    "CredentialProfile",
    "DeviceAuthorization",
    "EnvironmentSecretStore",
    "KeyringSecretStore",
    "LoginBroker",
    "LoginDriver",
    "LoginResult",
    "LoginSession",
    "LoopbackRedirect",
    "MemorySecretStore",
    "ModelSpec",
    "ModelVariant",
    "ModelsDevSource",
    "OAuthClient",
    "OAuthDeviceDriver",
    "OAuthError",
    "OAuthPkceDriver",
    "OAuthProviderConfig",
    "OpenUrlPrompt",
    "ProfileRegistry",
    "ProviderAccess",
    "ProviderSpec",
    "SearchHit",
    "SecretPrompt",
    "SecretStore",
    "SelectionIntent",
    "SelectionPlan",
    "Selector",
    "UserCodePrompt",
    "discover_environment_profiles",
    "load_overlay",
    "provider_access",
    "provider_access_routes",
    "provider_popularity_key",
    "provider_popularity_rank",
    "route_oauth_config",
]
