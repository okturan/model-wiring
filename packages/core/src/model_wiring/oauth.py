"""Provider-neutral OAuth 2.0 PKCE, device-code, and refresh primitives.

This module handles protocol plumbing only. Provider discovery, browser launch,
callback serving, and user consent UI belong to the embedding application.
"""

from __future__ import annotations

import json
import secrets
import time
from base64 import b64encode, urlsafe_b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .auth import CredentialMaterial
from .contracts import CredentialProfile
from .errors import RefreshError


class OAuthError(RefreshError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class AuthorizationPending(OAuthError):
    pass


@dataclass(frozen=True)
class OAuthProviderConfig:
    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: tuple[str, ...] = ()
    redirect_uri: str | None = None
    device_authorization_endpoint: str | None = None
    client_auth_method: str = "none"
    extra_authorization_params: Mapping[str, str] | None = None
    extra_token_params: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        supported = {"none", "client_secret_post", "client_secret_basic"}
        if self.client_auth_method not in supported:
            raise ValueError(
                f"unsupported OAuth client auth method: {self.client_auth_method}"
            )


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    code_verifier: str = field(repr=False)
    redirect_uri: str

    def public_dict(self) -> dict[str, str]:
        # The verifier is intentionally excluded. The app keeps this object
        # private until the authorization callback is exchanged.
        return {"url": self.url, "state": self.state, "redirect_uri": self.redirect_uri}


class DeviceAuthorization:
    __slots__ = (
        "_device_code",
        "expires_at",
        "interval",
        "user_code",
        "verification_uri",
        "verification_uri_complete",
    )

    def __init__(
        self,
        *,
        verification_uri: str,
        verification_uri_complete: str | None,
        user_code: str,
        device_code: str,
        expires_at: float,
        interval: float,
    ) -> None:
        self.verification_uri = verification_uri
        self.verification_uri_complete = verification_uri_complete
        self.user_code = user_code
        self.expires_at = expires_at
        self.interval = interval
        self._device_code = bytearray(device_code.encode("utf-8"))

    def __repr__(self) -> str:
        return (
            "DeviceAuthorization("
            f"verification_uri={self.verification_uri!r}, user_code={self.user_code!r}, "
            "device_code=<redacted>)"
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "verification_uri": self.verification_uri,
            "verification_uri_complete": self.verification_uri_complete,
            "user_code": self.user_code,
            "expires_at": self.expires_at,
            "interval": self.interval,
        }

    def _reveal_device_code(self) -> str:
        return self._device_code.decode("utf-8")

    def wipe(self) -> None:
        self._device_code[:] = b"\x00" * len(self._device_code)


Transport = Callable[
    [str, Mapping[str, str], Mapping[str, str], float], Mapping[str, Any]
]


class OAuthClient:
    """Generic standards-based OAuth client and AuthBroker refresh driver."""

    def __init__(
        self,
        config: OAuthProviderConfig,
        *,
        client_secret: Callable[[], str] | None = None,
        transport: Transport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.config = config
        self.client_secret = client_secret
        self.transport = transport or _post_form
        self.timeout = timeout

    def begin_authorization(
        self,
        *,
        state: str | None = None,
        redirect_uri: str | None = None,
        scopes: tuple[str, ...] | None = None,
    ) -> AuthorizationRequest:
        callback = redirect_uri or self.config.redirect_uri
        if not callback:
            raise OAuthError("OAuth authorization requires a redirect URI")
        verifier = secrets.token_urlsafe(64)
        challenge = (
            urlsafe_b64encode(sha256(verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        csrf_state = state or secrets.token_urlsafe(32)
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": callback,
            "scope": " ".join(scopes if scopes is not None else self.config.scopes),
            "state": csrf_state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        params.update(self.config.extra_authorization_params or {})
        separator = "&" if "?" in self.config.authorization_endpoint else "?"
        return AuthorizationRequest(
            url=f"{self.config.authorization_endpoint}{separator}{urlencode(params)}",
            state=csrf_state,
            code_verifier=verifier,
            redirect_uri=callback,
        )

    def exchange_code(
        self,
        code: str,
        request: AuthorizationRequest,
        *,
        returned_state: str,
    ) -> CredentialMaterial:
        if not secrets.compare_digest(returned_state, request.state):
            raise OAuthError("OAuth state mismatch")
        params = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": request.redirect_uri,
            "code_verifier": request.code_verifier,
        }
        return self._token_request(params)

    def begin_device_authorization(
        self, *, scopes: tuple[str, ...] | None = None
    ) -> DeviceAuthorization:
        endpoint = self.config.device_authorization_endpoint
        if not endpoint:
            raise OAuthError(
                "provider does not declare a device authorization endpoint"
            )
        params = {
            "client_id": self.config.client_id,
            "scope": " ".join(scopes if scopes is not None else self.config.scopes),
        }
        response = self.transport(endpoint, params, {}, self.timeout)
        try:
            return DeviceAuthorization(
                verification_uri=str(response["verification_uri"]),
                verification_uri_complete=(
                    str(response["verification_uri_complete"])
                    if response.get("verification_uri_complete")
                    else None
                ),
                user_code=str(response["user_code"]),
                device_code=str(response["device_code"]),
                expires_at=time.time() + float(response["expires_in"]),
                interval=float(response.get("interval", 5)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OAuthError("invalid device authorization response") from exc

    def poll_device(self, authorization: DeviceAuthorization) -> CredentialMaterial:
        if authorization.expires_at <= time.time():
            raise OAuthError("device authorization expired", code="expired_token")
        try:
            return self._token_request(
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": authorization._reveal_device_code(),
                }
            )
        except OAuthError as exc:
            if exc.code in {"authorization_pending", "slow_down"}:
                raise AuthorizationPending(str(exc), code=exc.code) from exc
            raise

    def refresh(
        self, profile: CredentialProfile, material: CredentialMaterial
    ) -> CredentialMaterial:
        del profile
        try:
            refresh_token = material.reveal("refresh_token")
        except Exception as exc:
            raise OAuthError("OAuth material has no refresh token") from exc
        refreshed = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        if not refreshed.has("refresh_token"):
            private = refreshed.private_dict()
            private["secrets"]["refresh_token"] = refresh_token
            replacement = CredentialMaterial.from_private_dict(private)
            refreshed.wipe()
            return replacement
        return refreshed

    def _token_request(self, parameters: Mapping[str, str]) -> CredentialMaterial:
        params = {
            "client_id": self.config.client_id,
            **(self.config.extra_token_params or {}),
        }
        params.update(parameters)
        headers: dict[str, str] = {}
        if self.config.client_auth_method != "none":
            if self.client_secret is None:
                raise OAuthError(
                    "confidential OAuth client has no client-secret resolver"
                )
            secret = self.client_secret()
            if self.config.client_auth_method == "client_secret_post":
                params["client_secret"] = secret
            else:
                raw = f"{self.config.client_id}:{secret}".encode()
                headers["Authorization"] = f"Basic {b64encode(raw).decode('ascii')}"
                params.pop("client_id", None)
        response = self.transport(
            self.config.token_endpoint, params, headers, self.timeout
        )
        if response.get("error"):
            code = str(response["error"])
            detail = str(response.get("error_description") or code)
            raise OAuthError(f"OAuth token endpoint: {detail}", code=code)
        try:
            access_token = str(response["access_token"])
        except KeyError as exc:
            raise OAuthError("OAuth token response has no access_token") from exc
        secret_values = {"access_token": access_token}
        if response.get("refresh_token"):
            secret_values["refresh_token"] = str(response["refresh_token"])
        expires_at = None
        if response.get("expires_in") is not None:
            expires_at = time.time() + float(response["expires_in"])
        public_keys = {"token_type", "scope", "id_token_expires_in"}
        metadata = {key: response[key] for key in public_keys if key in response}
        return CredentialMaterial(
            secret_values, expires_at=expires_at, metadata=metadata
        )


def _post_form(
    url: str,
    parameters: Mapping[str, str],
    headers: Mapping[str, str],
    timeout: float,
) -> Mapping[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "model-wiring/0.1",
        **headers,
    }
    request = Request(
        url,
        data=urlencode(parameters).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        body = exc.read()
    except URLError as exc:
        raise OAuthError(f"OAuth endpoint unavailable: {exc.reason}") from exc
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OAuthError("OAuth endpoint returned non-JSON data") from exc
    if not isinstance(value, Mapping):
        raise OAuthError("OAuth endpoint returned a non-object response")
    return value
