"""Small loopback JSON service over the same public contracts."""

from __future__ import annotations

import json
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
    service: ProviderService, *, allowed_origin: str | None = None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ModelWiring/0.1"

        def do_GET(self) -> None:
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
            if allowed_origin is None:
                self._write(
                    HTTPStatus.FORBIDDEN,
                    {"error": {"type": "cors_disabled", "message": "CORS is disabled"}},
                )
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            # Keep stdout clean for parent-process protocols. Operators can wrap
            # the service with their own access logging.
            del format, args

        def _write(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            if allowed_origin is not None:
                self.send_header("Access-Control-Allow-Origin", allowed_origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def serve(
    service: ProviderService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allowed_origin: str | None = None,
) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("provider service is loopback-only by default")
    server = ThreadingHTTPServer(
        (host, port), make_handler(service, allowed_origin=allowed_origin)
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
