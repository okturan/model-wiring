"""Small loopback JSON service over the same public contracts.

Every request carries a bearer token issued when the service starts. Binding
127.0.0.1 keeps the service off the network but not away from other processes on
the machine, and this service answers questions about a user's credentials.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .access import provider_access
from .catalog import Catalog
from .contracts import CredentialProfile, ProviderSpec, SelectionIntent
from .errors import AmbiguousSelection, ModelProviderError
from .popularity import provider_popularity_key
from .profiles import ProfileRegistry
from .selection import Selector

MAX_BODY_BYTES = 256 * 1024

BEARER_SCHEME = "Bearer"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def issue_token() -> str:
    """Mint the bearer token one service start uses and prints once."""

    return secrets.token_urlsafe(32)


class ProviderService:
    def __init__(
        self,
        catalog: Catalog,
        profiles: ProfileRegistry | Sequence[CredentialProfile] | None = None,
    ) -> None:
        self.catalog = catalog
        self.profiles = profiles
        self.selector = Selector(catalog, profiles)

    def get(self, path: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        if path == "/healthz":
            return 200, {"ok": True, "catalog_digest": self.catalog.snapshot.digest}
        if path == "/v1/catalog":
            include_models = query.get("models", ["false"])[0].lower() == "true"
            return 200, self.catalog.snapshot.to_dict(include_models=include_models)
        if path == "/v1/providers":
            return 200, {
                "items": [
                    self._provider_payload(provider)
                    for provider in sorted(
                        self.catalog.snapshot.providers.values(),
                        key=provider_popularity_key,
                    )
                ]
            }
        if path == "/v1/models":
            text = query.get("q", [""])[0]
            provider = query.get("provider", [None])[0]
            limit = min(max(int(query.get("limit", ["20"])[0]), 1), 200)
            if text:
                items = [
                    hit.to_dict()
                    for hit in self.catalog.search(text, provider=provider, limit=limit)
                ]
            else:
                provider_id = self.catalog.provider(provider).id if provider else None
                items = [
                    {
                        "score": None,
                        "provider": candidate_provider.to_dict(include_models=False),
                        "model": model.to_dict(),
                    }
                    for candidate_provider in sorted(
                        self.catalog.snapshot.providers.values(),
                        key=provider_popularity_key,
                    )
                    if provider_id is None or candidate_provider.id == provider_id
                    for model in sorted(
                        candidate_provider.models.values(), key=lambda item: item.id
                    )
                ][:limit]
            return 200, {"items": items}
        if path == "/v1/profiles":
            items = (
                self.profiles.list()
                if isinstance(self.profiles, ProfileRegistry)
                else tuple(self.profiles or ())
            )
            return 200, {"items": [profile.to_dict() for profile in items]}
        return 404, {"error": {"type": "not_found", "message": "route not found"}}

    def _stored_profiles(self) -> tuple[CredentialProfile, ...]:
        if isinstance(self.profiles, ProfileRegistry):
            return tuple(self.profiles.list())
        return tuple(self.profiles or ())

    def _provider_payload(self, provider: ProviderSpec) -> dict[str, Any]:
        """Serve catalogue facts and the non-secret answer to "how do I connect?"."""

        access = provider_access(
            provider,
            tuple(
                profile
                for profile in self._stored_profiles()
                if profile.provider_id == provider.id and profile.enabled
            ),
        )
        payload = provider.to_dict(include_models=False)
        payload["access_routes"] = [route.to_dict() for route in access.routes]
        payload["required_variables"] = list(access.required_variables)
        payload["credential_state"] = access.credential_state
        payload["probe_state"] = _best_probe_state(access.profiles)
        payload["entitlement_class"] = next(
            (
                str(profile.metadata["entitlement_class"])
                for profile in access.profiles
                if profile.metadata.get("entitlement_class")
            ),
            None,
        )
        return payload

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/v1/select":
            try:
                plan = self.selector.select(SelectionIntent.from_dict(body))
                return 200, plan.to_dict()
            except AmbiguousSelection as exc:
                return 409, {
                    "error": {
                        "type": "ambiguous_selection",
                        "message": str(exc),
                        "candidates": list(exc.candidates),
                    }
                }
            except ModelProviderError as exc:
                return 422, {
                    "error": {"type": exc.__class__.__name__, "message": str(exc)}
                }
        return 404, {"error": {"type": "not_found", "message": "route not found"}}


def make_handler(
    service: ProviderService, *, token: str, allowed_origin: str | None = None
) -> type[BaseHTTPRequestHandler]:
    """Serve ``service`` to callers presenting ``token`` and to nobody else."""

    if not token:
        raise ValueError("the loopback API requires a bearer token")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ModelWiring/0.1"

        def do_GET(self) -> None:
            if not self._authorized():
                return
            parsed = urlparse(self.path)
            try:
                status, payload = service.get(parsed.path, parse_qs(parsed.query))
            except (ValueError, ModelProviderError) as exc:
                status, payload = (
                    400,
                    {"error": {"type": exc.__class__.__name__, "message": str(exc)}},
                )
            self._write(status, payload)

        def do_POST(self) -> None:
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self._write(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {
                        "error": {
                            "type": "body_too_large",
                            "message": "request body too large",
                        }
                    },
                )
                return
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise TypeError("JSON body must be an object")
                parsed = urlparse(self.path)
                status, payload = service.post(parsed.path, body)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                status, payload = (
                    400,
                    {"error": {"type": "invalid_json", "message": str(exc)}},
                )
            self._write(status, payload)

        def do_OPTIONS(self) -> None:
            # A preflight carries no Authorization header by definition, so
            # refusing it here would leave a browser unable to ever present one.
            if allowed_origin is None:
                self._write(
                    HTTPStatus.FORBIDDEN,
                    {"error": {"type": "cors_disabled", "message": "CORS is disabled"}},
                )
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Authorization, Content-Type"
            )
            self.send_header("Vary", "Origin")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            # Keep stdout clean for parent-process protocols. Operators can wrap
            # the service with their own access logging.
            del format, args

        def _authorized(self) -> bool:
            presented = _presented_token(self.headers.get("Authorization"))
            if presented is not None and secrets.compare_digest(presented, token):
                return True
            self._drain()
            self._write(
                HTTPStatus.UNAUTHORIZED,
                {
                    "error": {
                        "type": "unauthorized",
                        "message": "this service requires the bearer token it "
                        "printed at startup",
                    }
                },
                headers=(("WWW-Authenticate", BEARER_SCHEME),),
            )
            return False

        def _drain(self) -> None:
            """Consume a refused request's declared body, unread.

            Closing a socket with unread bytes costs the caller the refusal it
            was about to read.
            """

            declared = self.headers.get("Content-Length")
            length = int(declared) if declared and declared.isdigit() else 0
            if 0 < length <= MAX_BODY_BYTES:
                self.rfile.read(length)

        def _write(
            self,
            status: int,
            payload: dict[str, Any],
            *,
            headers: Sequence[tuple[str, str]] = (),
        ) -> None:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            for name, value in headers:
                self.send_header(name, value)
            if allowed_origin is not None:
                self.send_header("Access-Control-Allow-Origin", allowed_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def api_server(
    service: ProviderService,
    *,
    token: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    allowed_origin: str | None = None,
) -> ThreadingHTTPServer:
    """Bind the service without serving it, so a caller can print its address."""

    if host not in LOOPBACK_HOSTS:
        raise ValueError("provider service is loopback-only by default")
    return ThreadingHTTPServer(
        (host, port),
        make_handler(service, token=token, allowed_origin=allowed_origin),
    )


def serve(
    service: ProviderService,
    *,
    token: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    allowed_origin: str | None = None,
) -> None:
    """Serve until interrupted. ``token`` is required: loopback is not a wall."""

    server = api_server(
        service, token=token, host=host, port=port, allowed_origin=allowed_origin
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _presented_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != BEARER_SCHEME.lower():
        return None
    return value.strip() or None


def _best_probe_state(profiles: Sequence[CredentialProfile]) -> str | None:
    """Report the best outcome any profile achieved, or None if unprobed."""

    states = {
        str(profile.metadata["last_probe_state"])
        for profile in profiles
        if profile.metadata.get("last_probe_state")
    }
    for preferred in ("ready", "unknown", "expired", "policy_denied", "unavailable"):
        if preferred in states:
            return preferred
    return None
