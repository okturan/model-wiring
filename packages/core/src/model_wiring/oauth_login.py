"""Browser and device sign-in built on the provider-neutral OAuth client.

Provider endpoints and client identities are **data**, supplied through an
access route's ``metadata.oauth``. Nothing here hard-codes a third party's
authorization server, so a route can be corrected or added by an overlay
without changing this module.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .access import AccessRoute
from .contracts import CredentialProfile, ProviderSpec
from .login import (
    LoginContext,
    LoginResult,
    LoginSession,
    OpenUrlPrompt,
    UserCodePrompt,
)
from .oauth import (
    AuthorizationPending,
    OAuthClient,
    OAuthError,
    OAuthProviderConfig,
    Transport,
)

SLOW_DOWN_BACKOFF_SECONDS = 5.0

CLOSE_PAGE = (
    b"<!doctype html><meta charset=utf-8><title>Sign-in complete</title>"
    b"<p>You can close this tab and return to your terminal.</p>"
)


class LoopbackRedirect:
    """Accept exactly one authorization callback on ``127.0.0.1``.

    The listener binds the loopback interface only, serves a single request,
    and never logs the query string, which carries the authorization code.
    """

    def __init__(self, *, path: str = "/callback") -> None:
        self.path = path
        self._result: dict[str, str] | None = None
        self._received = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != outer.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                outer._capture({key: values[0] for key, values in query.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(CLOSE_PAGE)))
                self.end_headers()
                self.wfile.write(CLOSE_PAGE)

            def log_message(self, *args: object) -> None:
                """Silence the default logger; the query carries a secret."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # shutdown() blocks for one poll interval; the stdlib default of 0.5s
        # is a visible pause between approving in the browser and continuing.
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02),
            daemon=True,
        )
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.path}"

    def _capture(self, values: Mapping[str, str]) -> None:
        if self._result is None:
            self._result = dict(values)
            self._received.set()

    def wait(self, *, timeout: float = 300.0) -> dict[str, str]:
        if not self._received.wait(timeout):
            raise OAuthError("timed out waiting for the browser to return")
        assert self._result is not None
        return self._result

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> LoopbackRedirect:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def route_oauth_config(
    route: AccessRoute, *, redirect_uri: str | None = None
) -> OAuthProviderConfig:
    """Build a provider config from the non-secret data an overlay supplies."""

    declared = route.metadata.get("oauth")
    if not isinstance(declared, Mapping):
        raise ValueError(
            f"access route {route.id!r} declares no metadata.oauth configuration"
        )
    missing = [
        field
        for field in ("client_id", "authorization_endpoint", "token_endpoint")
        if not declared.get(field)
    ]
    if missing:
        raise ValueError(
            f"access route {route.id!r} OAuth config is missing: {', '.join(missing)}"
        )
    return OAuthProviderConfig(
        client_id=str(declared["client_id"]),
        authorization_endpoint=str(declared["authorization_endpoint"]),
        token_endpoint=str(declared["token_endpoint"]),
        scopes=tuple(str(item) for item in declared.get("scopes", ())),
        redirect_uri=redirect_uri or _optional(declared.get("redirect_uri")),
        device_authorization_endpoint=_optional(
            declared.get("device_authorization_endpoint")
        ),
        client_auth_method=str(declared.get("client_auth_method") or "none"),
        extra_authorization_params=dict(
            declared.get("extra_authorization_params") or {}
        )
        or None,
        extra_token_params=dict(declared.get("extra_token_params") or {}) or None,
    )


class _OAuthDriverBase:
    def __init__(self, *, transport: Transport | None = None) -> None:
        self.transport = transport

    def _client(self, config: OAuthProviderConfig) -> OAuthClient:
        return OAuthClient(config, transport=self.transport)

    def _profile(
        self,
        session: LoginSession,
        context: LoginContext,
        reference: str,
    ) -> CredentialProfile:
        return CredentialProfile(
            id=f"{session.provider_id}-{session.route.id}",
            provider_id=session.provider_id,
            auth_kind="oauth",
            billing_kind=session.route.billing_kind,
            secret_ref=reference,
            secret_store=context.store_name,
            account_label=session.route.label,
            scopes=session.private.get("scopes", ()),
            metadata={"refresh_owner": "application"},
        )


class OAuthPkceDriver(_OAuthDriverBase):
    """Browser sign-in with PKCE and a loopback redirect."""

    id = "oauth_pkce"

    def begin(
        self, provider: ProviderSpec, route: AccessRoute, context: LoginContext
    ) -> LoginSession:
        redirect = LoopbackRedirect()
        try:
            config = route_oauth_config(route, redirect_uri=redirect.redirect_uri)
            request = self._client(config).begin_authorization(
                redirect_uri=redirect.redirect_uri
            )
        except Exception:
            redirect.close()
            raise
        return LoginSession(
            provider_id=provider.id,
            route=route,
            driver=self.id,
            prompt=OpenUrlPrompt(
                url=request.url,
                redirect_uri=redirect.redirect_uri,
                title=f"Approve {provider.name} access in your browser",
                doc_url=route.doc_url,
            ),
            private={
                "request": request,
                "redirect": redirect,
                "config": config,
                "scopes": config.scopes,
            },
        )

    def advance(
        self, session: LoginSession, response: Mapping[str, Any], context: LoginContext
    ) -> LoginResult:
        redirect: LoopbackRedirect = session.private["redirect"]
        request = session.private["request"]
        try:
            values = dict(response) or redirect.wait()
            if values.get("error"):
                raise OAuthError(
                    f"authorization was refused: {values['error']}",
                    code=str(values["error"]),
                )
            code = str(values.get("code") or "")
            if not code:
                raise OAuthError("authorization callback carried no code")
            material = self._client(session.private["config"]).exchange_code(
                code, request, returned_state=str(values.get("state") or "")
            )
        finally:
            redirect.close()
        reference = f"{session.provider_id}:{session.route.id}"
        try:
            context.store.put(reference, material)
        finally:
            material.wipe()
        return LoginResult(self._profile(session, context, reference))


class OAuthDeviceDriver(_OAuthDriverBase):
    """Device sign-in for machines that cannot open a browser.

    The provider's own ``interval`` sets the polling pace; ``sleep`` is
    overridable so tests do not have to wait it out.
    """

    id = "oauth_device"
    sleep = staticmethod(time.sleep)

    def begin(
        self, provider: ProviderSpec, route: AccessRoute, context: LoginContext
    ) -> LoginSession:
        config = route_oauth_config(route)
        authorization = self._client(config).begin_device_authorization()
        return LoginSession(
            provider_id=provider.id,
            route=route,
            driver=self.id,
            prompt=UserCodePrompt(
                verification_uri=authorization.verification_uri,
                verification_uri_complete=authorization.verification_uri_complete,
                user_code=authorization.user_code,
                interval=authorization.interval,
                expires_at=authorization.expires_at,
                title=f"Approve {provider.name} access with this code",
                doc_url=route.doc_url,
            ),
            private={
                "authorization": authorization,
                "config": config,
                "scopes": config.scopes,
            },
        )

    def advance(
        self, session: LoginSession, response: Mapping[str, Any], context: LoginContext
    ) -> LoginResult:
        del response
        authorization = session.private["authorization"]
        client = self._client(session.private["config"])
        interval = max(0.0, authorization.interval)
        material = None
        try:
            while material is None:
                try:
                    material = client.poll_device(authorization)
                except AuthorizationPending as pending:
                    if authorization.expires_at <= time.time():
                        raise OAuthError(
                            "device authorization expired", code="expired_token"
                        ) from None
                    if pending.code == "slow_down":
                        # RFC 8628 §3.5: back off, and keep the larger interval.
                        interval += SLOW_DOWN_BACKOFF_SECONDS
                    self.sleep(interval)
        finally:
            authorization.wipe()
        reference = f"{session.provider_id}:{session.route.id}"
        try:
            context.store.put(reference, material)
        finally:
            material.wipe()
        return LoginResult(self._profile(session, context, reference))


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
