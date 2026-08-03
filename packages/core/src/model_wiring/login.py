"""Guided provider onboarding built from serializable prompts.

A driver never prints, never reads stdin, and never opens a browser. It returns
a prompt describing what a human must supply; the embedding application decides
how to ask. That is what lets one flow serve the ANSI picker, the web component,
a JSON CLI, and a host application's own interface.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .access import AccessRoute, provider_access_routes
from .auth import CredentialMaterial, MemorySecretStore, SecretStore
from .catalog import Catalog
from .contracts import CredentialProfile, ProviderSpec
from .errors import CredentialError, ProfileError
from .profiles import ProfileRegistry

JSON = dict[str, Any]


@dataclass(frozen=True)
class SecretField:
    name: str
    label: str
    masked: bool = True
    help: str | None = None

    def to_dict(self) -> JSON:
        return {
            "name": self.name,
            "label": self.label,
            "masked": self.masked,
            "help": self.help,
        }


@dataclass(frozen=True)
class SecretPrompt:
    """Ask for credential values the user already holds."""

    kind: str = field(default="secret", init=False)
    fields: tuple[SecretField, ...] = ()
    title: str = "Enter your credential"
    doc_url: str | None = None

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)

    def to_dict(self) -> JSON:
        return {
            "kind": self.kind,
            "title": self.title,
            "doc_url": self.doc_url,
            "fields": [item.to_dict() for item in self.fields],
        }


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    detail: str | None = None

    def to_dict(self) -> JSON:
        return {"id": self.id, "label": self.label, "detail": self.detail}


@dataclass(frozen=True)
class ChoicePrompt:
    """Ask the user to pick one of several discovered options."""

    kind: str = field(default="choice", init=False)
    options: tuple[Choice, ...] = ()
    title: str = "Choose an existing sign-in"
    doc_url: str | None = None

    @property
    def option_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.options)

    def to_dict(self) -> JSON:
        return {
            "kind": self.kind,
            "title": self.title,
            "doc_url": self.doc_url,
            "options": [item.to_dict() for item in self.options],
        }


@dataclass(frozen=True)
class OpenUrlPrompt:
    """Send the user to an authorization page and wait for the redirect."""

    kind: str = field(default="open_url", init=False)
    url: str = ""
    redirect_uri: str | None = None
    title: str = "Approve access in your browser"
    doc_url: str | None = None

    def to_dict(self) -> JSON:
        return {
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "redirect_uri": self.redirect_uri,
            "doc_url": self.doc_url,
        }


@dataclass(frozen=True)
class UserCodePrompt:
    """Show a code the user types into the provider's device page."""

    kind: str = field(default="user_code", init=False)
    verification_uri: str = ""
    user_code: str = ""
    verification_uri_complete: str | None = None
    interval: float = 5.0
    expires_at: float | None = None
    title: str = "Enter this code to approve access"
    doc_url: str | None = None

    def to_dict(self) -> JSON:
        return {
            "kind": self.kind,
            "title": self.title,
            "verification_uri": self.verification_uri,
            "verification_uri_complete": self.verification_uri_complete,
            "user_code": self.user_code,
            "interval": self.interval,
            "expires_at": self.expires_at,
            "doc_url": self.doc_url,
        }


Prompt = SecretPrompt | ChoicePrompt | OpenUrlPrompt | UserCodePrompt


@dataclass
class LoginSession:
    """One in-flight login. ``private`` never crosses a surface boundary."""

    provider_id: str
    route: AccessRoute
    driver: str
    prompt: Prompt
    private: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JSON:
        return {
            "provider_id": self.provider_id,
            "route_id": self.route.id,
            "driver": self.driver,
            "prompt": self.prompt.to_dict(),
        }


@dataclass(frozen=True)
class LoginResult:
    profile: CredentialProfile

    def to_dict(self) -> JSON:
        return {"profile": self.profile.to_dict()}


class LoginDriver(Protocol):
    id: str

    def begin(
        self, provider: ProviderSpec, route: AccessRoute, context: LoginContext
    ) -> LoginSession: ...

    def advance(
        self, session: LoginSession, response: Mapping[str, Any], context: LoginContext
    ) -> LoginSession | LoginResult: ...


@dataclass
class LoginContext:
    """Everything a driver may touch, supplied by the broker.

    The secret store is resolved on first use so that describing a login never
    requires storage the caller has not installed or chosen yet.
    """

    store_name: str
    environ: Mapping[str, str]
    delegated_candidates: Mapping[str, Sequence[Mapping[str, Any]]]
    resolve_store: Callable[[], SecretStore]

    @property
    def store(self) -> SecretStore:
        return self.resolve_store()


class ApiKeyPasteDriver:
    """Store a key or credential bundle the user already holds."""

    id = "api_key_paste"

    def begin(
        self, provider: ProviderSpec, route: AccessRoute, context: LoginContext
    ) -> LoginSession:
        names = route.env or ("api_key",)
        single = len(names) == 1
        return LoginSession(
            provider_id=provider.id,
            route=route,
            driver=self.id,
            prompt=SecretPrompt(
                fields=tuple(
                    SecretField(
                        name=name,
                        label=name if not single else f"{provider.name} API key",
                    )
                    for name in names
                ),
                title=f"Connect {provider.name}",
                doc_url=route.doc_url,
            ),
        )

    def advance(
        self, session: LoginSession, response: Mapping[str, Any], context: LoginContext
    ) -> LoginResult:
        names = session.prompt.field_names  # type: ignore[union-attr]
        values = {name: str(response.get(name) or "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"missing credential values: {', '.join(missing)}")
        single = len(names) == 1
        secrets = {"api_key": values[names[0]]} if single else values
        profile_id = f"{session.provider_id}-{session.route.id}"
        reference = f"{session.provider_id}:{session.route.id}"
        context.store.put(reference, CredentialMaterial(secrets))
        return LoginResult(
            CredentialProfile(
                id=profile_id,
                provider_id=session.provider_id,
                auth_kind=session.route.kind,
                billing_kind=session.route.billing_kind,
                secret_ref=reference,
                secret_store=context.store_name,
                account_label=session.route.label,
                metadata={"variables": list(names)},
            )
        )


class CredentialBundleDriver(ApiKeyPasteDriver):
    id = "credential_bundle_paste"


class DelegatedImportDriver:
    """Reuse a login another tool already performed, without copying tokens."""

    id = "delegated_import"

    def begin(
        self, provider: ProviderSpec, route: AccessRoute, context: LoginContext
    ) -> LoginSession:
        candidates = [
            candidate
            for candidate in context.delegated_candidates.get(provider.id, ())
            if _candidate_present(candidate)
        ]
        if not candidates:
            raise ValueError(
                f"no existing {provider.name} sign-in was found on this machine"
            )
        return LoginSession(
            provider_id=provider.id,
            route=route,
            driver=self.id,
            prompt=ChoicePrompt(
                options=tuple(
                    Choice(
                        id=str(candidate["id"]),
                        label=str(candidate.get("label") or candidate["id"]),
                        detail=candidate.get("path"),
                    )
                    for candidate in candidates
                ),
                title=f"Use an existing {provider.name} sign-in",
                doc_url=route.doc_url,
            ),
            private={"candidates": candidates},
        )

    def advance(
        self, session: LoginSession, response: Mapping[str, Any], context: LoginContext
    ) -> LoginResult:
        chosen = str(response.get("choice") or "")
        candidate = next(
            (
                item
                for item in session.private["candidates"]
                if str(item["id"]) == chosen
            ),
            None,
        )
        if candidate is None:
            raise ValueError(f"unknown sign-in choice: {chosen or '(none)'}")
        return LoginResult(
            CredentialProfile(
                id=f"{session.provider_id}-{chosen}",
                provider_id=session.provider_id,
                auth_kind="delegated",
                billing_kind=session.route.billing_kind,
                account_label=str(candidate.get("label") or chosen),
                metadata={
                    "delegate": chosen,
                    "imported_from": str(candidate.get("label") or chosen),
                    "refresh_owner": str(candidate.get("refresh_owner") or "sdk"),
                    # Recorded so a probe can tell whether the owning tool is
                    # still signed in without reading the artifact's contents.
                    **(
                        {"delegate_path": str(candidate["path"])}
                        if candidate.get("path")
                        else {}
                    ),
                },
            )
        )


DEFAULT_DRIVERS: tuple[LoginDriver, ...] = (
    ApiKeyPasteDriver(),
    CredentialBundleDriver(),
    DelegatedImportDriver(),
)

# Sign-ins first-party tools create on this machine. Discovery only checks that
# the artifact exists; contents stay unread until the user picks one. A wrong or
# stale path therefore hides the candidate rather than offering a broken route.
DELEGATED_CANDIDATES: dict[str, tuple[dict[str, Any], ...]] = {
    "openai-codex": (
        {
            "id": "codex-cli",
            "label": "Codex CLI sign-in",
            "path": "~/.codex/auth.json",
            "refresh_owner": "sdk",
        },
    ),
    "anthropic": (
        {
            "id": "claude-code",
            "label": "Claude Code sign-in",
            "path": "~/.claude/.credentials.json",
            "refresh_owner": "sdk",
        },
    ),
    "github-copilot": (
        {
            "id": "gh-cli",
            "label": "GitHub CLI sign-in",
            "path": "~/.config/gh/hosts.yml",
            "refresh_owner": "application",
        },
    ),
}


class LoginBroker:
    """Turn an access route into a stored, non-secret credential profile."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        profiles: ProfileRegistry,
        stores: Mapping[str, SecretStore] | None = None,
        default_store: str = "keyring",
        drivers: Iterable[LoginDriver] | None = None,
        environ: Mapping[str, str] | None = None,
        delegated_candidates: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        oauth_transport: Any = None,
    ) -> None:
        self.catalog = catalog
        self.profiles = profiles
        self.stores: dict[str, SecretStore] = dict(stores or {})
        self.stores.setdefault("memory", MemorySecretStore())
        self.default_store = default_store
        if drivers is None:
            # Imported here because the OAuth drivers build on this module.
            from .oauth_login import OAuthDeviceDriver, OAuthPkceDriver

            drivers = (
                *DEFAULT_DRIVERS,
                OAuthPkceDriver(transport=oauth_transport),
                OAuthDeviceDriver(transport=oauth_transport),
            )
        self.drivers = {driver.id: driver for driver in drivers}
        self.environ = os.environ if environ is None else environ
        self.delegated_candidates = dict(delegated_candidates or {})

    def routes(self, provider_id: str) -> tuple[AccessRoute, ...]:
        """Catalogue routes plus any sign-in this machine can already delegate to."""

        return self._routes_for(self.catalog.provider(provider_id))

    def _routes_for(self, provider: ProviderSpec) -> tuple[AccessRoute, ...]:
        routes = provider_access_routes(provider)
        if any(route.kind == "delegated" for route in routes):
            return routes
        present = [
            candidate
            for candidate in self.delegated_candidates.get(provider.id, ())
            if _candidate_present(candidate)
        ]
        if not present:
            return routes
        # An existing tool login is the safest route, so it is offered first.
        delegated = AccessRoute(
            id="delegated",
            kind="delegated",
            billing_kind="subscription",
            label="Existing sign-in on this machine",
            doc_url=provider.doc_url,
            driver=DelegatedImportDriver.id,
        )
        return (delegated, *routes)

    def begin(
        self,
        provider_id: str,
        *,
        route_id: str | None = None,
        store: str | None = None,
    ) -> LoginSession:
        provider = self.catalog.provider(provider_id)
        route = self._route(provider, route_id)
        driver = self._driver(route)
        return driver.begin(provider, route, self._context(store))

    def advance(
        self,
        session: LoginSession,
        response: Mapping[str, Any],
        *,
        store: str | None = None,
    ) -> LoginSession | LoginResult:
        driver = self.drivers[session.driver]
        outcome = driver.advance(session, response, self._context(store))
        if isinstance(outcome, LoginResult):
            self.profiles.upsert(outcome.profile)
        return outcome

    def promote_environment(
        self, provider_id: str, *, store: str | None = None
    ) -> LoginResult:
        """Copy a live environment value into durable storage, once and explicitly."""

        provider = self.catalog.provider(provider_id)
        route = next(
            (item for item in provider_access_routes(provider) if item.env), None
        )
        if route is None:
            raise ValueError(f"{provider.name} declares no environment credential")
        missing = [name for name in route.env if not self.environ.get(name)]
        if missing:
            raise ValueError(f"environment variables are not set: {', '.join(missing)}")
        context = self._context(store)
        single = len(route.env) == 1
        values = {name: self.environ[name] for name in route.env}
        secrets = {"api_key": values[route.env[0]]} if single else values
        reference = f"{provider.id}:{route.id}"
        context.store.put(reference, CredentialMaterial(secrets))
        profile = CredentialProfile(
            id=f"{provider.id}-{route.id}",
            provider_id=provider.id,
            auth_kind=route.kind,
            billing_kind=route.billing_kind,
            secret_ref=reference,
            secret_store=context.store_name,
            account_label=route.label,
            metadata={
                "variables": list(route.env),
                "imported_from": "environment",
                "imported_at": time.time(),
            },
        )
        self.profiles.upsert(profile)
        return LoginResult(profile)

    def logout(self, profile_id: str) -> bool:
        """Remove the profile and any secret it referenced."""

        try:
            profile = self.profiles.get(profile_id)
        except ProfileError:
            return False
        if profile.secret_ref and profile.secret_store:
            store = self.stores.get(profile.secret_store)
            if store is not None:
                try:
                    store.delete(profile.secret_ref)
                except CredentialError:
                    pass
        return self.profiles.delete(profile_id)

    def _route(self, provider: ProviderSpec, route_id: str | None) -> AccessRoute:
        routes = self._routes_for(provider)
        if not routes:
            raise ValueError(f"no way to connect {provider.name} is known")
        if route_id is None:
            connectable = [item for item in routes if item.needs_credential]
            return connectable[0] if connectable else routes[0]
        for route in routes:
            if route.id == route_id or route.kind == route_id:
                return route
        raise ValueError(f"{provider.name} has no access route {route_id!r}")

    def _driver(self, route: AccessRoute) -> LoginDriver:
        driver = self.drivers.get(route.driver or "")
        if driver is None:
            raise ValueError(
                f"no login driver is registered for {route.label!r} "
                f"({route.driver or route.kind})"
            )
        return driver

    def _context(self, store: str | None) -> LoginContext:
        name = store or self.default_store
        return LoginContext(
            store_name=name,
            environ=self.environ,
            delegated_candidates=self.delegated_candidates,
            resolve_store=lambda: self._store(name),
        )

    def _store(self, name: str) -> SecretStore:
        try:
            return self.stores[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.stores)) or "none"
            hint = (
                " Install the keyring extra (pip install 'model-wiring[keyring]') "
                "or choose another store."
                if name == "keyring"
                else ""
            )
            raise ValueError(
                f"secret store {name!r} is not available; registered stores: "
                f"{available}.{hint}"
            ) from exc


def _candidate_present(candidate: Mapping[str, Any]) -> bool:
    """Report existence only. Contents stay unread until the user chooses."""

    path = candidate.get("path")
    if path is None:
        return True
    return Path(str(path)).expanduser().exists()
