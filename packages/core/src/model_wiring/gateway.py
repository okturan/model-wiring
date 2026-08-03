"""Loopback proxy that injects credentials at egress and streams bodies through.

This is the first component in the kit that ever sees application content, so it
buffers no body, logs no body, and stores no body. One client request becomes
exactly one provider request, and the provider's own answer comes back unchanged.

Which paths exist, where they forward to, and which header carries the
credential are catalogue data on an access route. Nothing provider-specific is
written here.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, BinaryIO, Protocol
from urllib.parse import urlsplit

from .access import provider_access_routes
from .auth import CredentialLease
from .broker import CredentialBroker
from .catalog import Catalog
from .contracts import ProviderSpec
from .errors import CredentialError, ModelProviderError

JSON = dict[str, Any]

LOGGER = logging.getLogger(__name__)
# A kit does not write to an application's stderr uninvited; an embedding app
# attaches its own handler when it wants these records.
LOGGER.addHandler(logging.NullHandler())

# Bodies move in pieces no larger than this, and each piece is written on as it
# arrives. Nothing ever holds a whole request or response.
CHUNK_BYTES = 64 * 1024

# A refused request whose body is larger than this is abandoned rather than
# consumed, so an unauthenticated caller cannot make the gateway read on.
DRAIN_LIMIT_BYTES = 1024 * 1024

# A hop-by-hop header describes one connection, so it stops at this proxy.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# The client's own credential to this gateway, and the routing hints it may
# send, are ours alone: they are replaced or dropped before egress.
PROFILE_HEADER = "X-Model-Wiring-Profile"
_CLIENT_ONLY_HEADERS = frozenset({"authorization", "host", PROFILE_HEADER.lower()})

_STRIPPED_REQUEST_HEADERS = HOP_BY_HOP_HEADERS | _CLIENT_ONLY_HEADERS

BEARER_SCHEME = "Bearer"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Statuses that carry no body, so framing one would be a protocol error.
_BODILESS_STATUSES = frozenset(
    {int(HTTPStatus.NO_CONTENT), int(HTTPStatus.NOT_MODIFIED)}
)


class GatewayError(ModelProviderError):
    """A request cannot be forwarded."""


class GatewayUnauthorized(GatewayError):
    """The gateway bearer token was missing or wrong."""


class RouteNotFound(GatewayError):
    """No declared route matches this method and path."""


class UpstreamUnavailable(GatewayError):
    """The provider endpoint could not be reached. It is never retried."""


@dataclass(frozen=True)
class GatewayRoute:
    """One provider-shaped path the gateway exposes, declared as data."""

    id: str
    provider_id: str
    base_url: str
    mount: str = ""
    paths: tuple[str, ...] = ()
    methods: tuple[str, ...] = ("POST",)
    # The read-only call this route offers as proof that a credential works.
    # Undeclared means unverifiable: nothing here invents one.
    probe_path: str | None = None
    probe_method: str = "GET"
    credential_header: str = "Authorization"
    credential_scheme: str = BEARER_SCHEME
    credential_key: str | None = None
    billing_kind: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError(f"gateway route {self.id} declares no upstream endpoint")
        if not self.credential_header:
            raise ValueError(f"gateway route {self.id} declares no credential header")

    @property
    def prefix(self) -> str:
        return self.mount.rstrip("/")

    def match(self, method: str, path: str) -> str | None:
        """Return the provider path this request maps to, or None."""

        if method.upper() not in self.methods:
            return None
        prefix = self.prefix
        if not prefix:
            suffix = path
        elif path == prefix:
            suffix = "/"
        elif path.startswith(f"{prefix}/"):
            suffix = path[len(prefix) :]
        else:
            return None
        if any(_path_matches(pattern, suffix) for pattern in self.paths):
            return suffix
        return None

    def upstream_url(self, path: str, query: str = "") -> str:
        base = self.base_url.rstrip("/")
        target = f"{base}{path}"
        return f"{target}?{query}" if query else target

    def to_dict(self) -> JSON:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "base_url": self.base_url,
            "mount": self.mount,
            "paths": list(self.paths),
            "methods": list(self.methods),
            "probe_path": self.probe_path,
            "probe_method": self.probe_method,
            "credential_header": self.credential_header,
            "credential_scheme": self.credential_scheme,
            "credential_key": self.credential_key,
            "billing_kind": self.billing_kind,
            "headers": dict(self.headers),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GatewayRoute:
        return cls(
            id=str(value.get("id") or "gateway"),
            provider_id=str(value["provider_id"]),
            base_url=str(value.get("base_url") or ""),
            mount=str(value.get("mount") or "").rstrip("/"),
            paths=tuple(str(item) for item in value.get("paths", ()) if item),
            methods=tuple(
                str(item).upper() for item in value.get("methods") or ("POST",) if item
            ),
            probe_path=_optional(value.get("probe_path")),
            probe_method=str(value.get("probe_method") or "GET").upper(),
            credential_header=str(value.get("credential_header") or "Authorization"),
            credential_scheme=str(value.get("credential_scheme", BEARER_SCHEME) or ""),
            credential_key=_optional(value.get("credential_key")),
            billing_kind=_declared_billing_kind(value.get("billing_kind")),
            headers={
                str(key): str(item)
                for key, item in (value.get("headers") or {}).items()
            },
        )


def provider_gateway_routes(provider: ProviderSpec) -> tuple[GatewayRoute, ...]:
    """Build the routes ``provider`` declares in its access-route metadata.

    Nothing is inferred. A provider that declares no ``gateway`` block, no path,
    or no endpoint to forward to exposes nothing, and unknown paths 404.
    """

    routes: list[GatewayRoute] = []
    for access in provider_access_routes(provider):
        declared = access.metadata.get("gateway")
        if not isinstance(declared, Mapping):
            continue
        base_url = str(declared.get("base_url") or provider.api_url or "")
        paths = [str(item) for item in declared.get("paths") or () if item]
        if not base_url or not paths:
            continue
        routes.append(
            GatewayRoute.from_dict(
                {
                    **declared,
                    "id": declared.get("id") or access.id,
                    "provider_id": provider.id,
                    "base_url": base_url,
                    "mount": declared.get("mount") or f"/{provider.id}",
                    "paths": paths,
                    "billing_kind": declared.get("billing_kind", access.billing_kind),
                }
            )
        )
    return tuple(routes)


def gateway_routes(
    catalog: Catalog, *, provider_ids: Iterable[str] | None = None
) -> tuple[GatewayRoute, ...]:
    """Every route the catalogue declares, in provider order."""

    wanted = set(provider_ids) if provider_ids is not None else None
    return tuple(
        route
        for provider in sorted(
            catalog.snapshot.providers.values(), key=lambda item: item.id
        )
        if wanted is None or provider.id in wanted
        for route in provider_gateway_routes(provider)
    )


@dataclass(frozen=True)
class GatewayRecord:
    """What one forwarded request is allowed to leave behind: no content."""

    provider_id: str
    route_id: str
    profile_id: str
    method: str
    path: str
    status: int
    request_bytes: int
    response_bytes: int
    duration_seconds: float
    started_at: float

    def to_dict(self) -> JSON:
        return {
            "provider_id": self.provider_id,
            "route_id": self.route_id,
            "profile_id": self.profile_id,
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class GatewayRequest:
    """One inbound call. The token and the body stay out of every repr."""

    method: str
    target: str
    headers: tuple[tuple[str, str], ...] = ()
    body: BinaryIO | None = field(default=None, repr=False)
    content_length: int | None = None
    token: str | None = field(default=None, repr=False)
    profile_id: str | None = None

    @property
    def path(self) -> str:
        return self.target.split("?", 1)[0]

    @property
    def query(self) -> str:
        _, separator, query = self.target.partition("?")
        return query if separator else ""


@dataclass(frozen=True)
class UpstreamRequest:
    """The single provider call a client request becomes.

    ``headers`` carries the injected credential, so it is kept out of the repr
    and dropped as soon as the request has been sent.
    """

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    body: BinaryIO | None = field(default=None, repr=False)
    timeout: float = 600.0


class UpstreamResponse(Protocol):
    """A provider response being read, not a provider response held."""

    status: int
    headers: Sequence[tuple[str, str]]

    def chunks(self, size: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class ResponseSink(Protocol):
    """Where a forwarded response is written, one piece at a time."""

    def begin(self, status: int, headers: Sequence[tuple[str, str]]) -> None: ...

    def write(self, chunk: bytes) -> None: ...

    def finish(self) -> None: ...


Transport = Callable[[UpstreamRequest], UpstreamResponse]


class HttpTransport:
    """Standard-library egress: one connection, one request, streamed both ways."""

    def __call__(self, request: UpstreamRequest) -> UpstreamResponse:
        parts = urlsplit(request.url)
        if parts.scheme == "https":
            connection: HTTPConnection = HTTPSConnection(
                parts.netloc, timeout=request.timeout
            )
        elif parts.scheme == "http":
            connection = HTTPConnection(parts.netloc, timeout=request.timeout)
        else:
            raise GatewayError(f"unsupported upstream scheme: {parts.scheme!r}")
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        try:
            connection.request(
                request.method,
                target,
                body=request.body,
                headers=request.headers,
            )
            response = connection.getresponse()
        except OSError as exc:
            connection.close()
            # No retry and no fallback: the application's own handling is the
            # only policy this proxy is entitled to apply.
            raise UpstreamUnavailable(f"provider endpoint unavailable: {exc}") from exc
        return _HttpUpstream(connection, response)


class _HttpUpstream:
    __slots__ = ("_connection", "_response", "headers", "status")

    def __init__(self, connection: HTTPConnection, response: HTTPResponse) -> None:
        self._connection = connection
        self._response = response
        self.status = int(response.status)
        self.headers = tuple(response.getheaders())

    def chunks(self, size: int) -> Iterator[bytes]:
        # read1 yields what has already arrived instead of waiting for a full
        # buffer, which is what makes a streamed completion stream.
        read = getattr(self._response, "read1", self._response.read)
        while True:
            chunk = read(size)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class _RequestBody:
    """Reads exactly the declared number of bytes, a piece at a time.

    ``read1`` on the source returns what has already arrived, so a client still
    sending its body has the first pieces forwarded rather than held.
    """

    __slots__ = ("_remaining", "_source", "bytes_read")

    def __init__(self, source: BinaryIO, length: int) -> None:
        self._source = source
        self._remaining = length
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        wanted = self._remaining if size < 0 else min(size, self._remaining)
        read1 = getattr(self._source, "read1", None)
        chunk = read1(wanted) if read1 is not None else self._source.read(wanted)
        self._remaining -= len(chunk)
        self.bytes_read += len(chunk)
        return chunk


class InferenceGateway:
    """Provider-shaped loopback routes with credentials injected at egress.

    A client holds the gateway's bearer token and nothing else; the provider
    credential is resolved per request through the broker and attached on the
    way out, so it never exists on the client side of this process.
    """

    def __init__(
        self,
        broker: CredentialBroker,
        routes: Iterable[GatewayRoute] = (),
        *,
        token: str | None = None,
        transport: Transport | None = None,
        timeout: float = 600.0,
        chunk_bytes: int = CHUNK_BYTES,
        history: int = 64,
    ) -> None:
        self.broker = broker
        self.routes = tuple(routes)
        self.token = token or secrets.token_urlsafe(32)
        self.transport = transport or HttpTransport()
        self.timeout = float(timeout)
        self.chunk_bytes = int(chunk_bytes)
        self._records: deque[GatewayRecord] = deque(maxlen=history)

    @property
    def records(self) -> tuple[GatewayRecord, ...]:
        """Metadata for recent requests. Never content, never a credential."""

        return tuple(self._records)

    def authorize(self, presented: str | None) -> None:
        if not presented or not secrets.compare_digest(presented, self.token):
            raise GatewayUnauthorized("the gateway bearer token is missing or wrong")

    def route_for(self, method: str, target: str) -> tuple[GatewayRoute, str] | None:
        path = target.split("?", 1)[0]
        for route in self.routes:
            upstream_path = route.match(method, path)
            if upstream_path is not None:
                return route, upstream_path
        return None

    def forward(self, request: GatewayRequest, sink: ResponseSink) -> GatewayRecord:
        """Forward one request and stream one response back. Never two."""

        # Authentication comes first so that an unauthenticated caller cannot
        # make this process read a secret or open a connection.
        self.authorize(request.token)
        matched = self.route_for(request.method, request.target)
        if matched is None:
            raise RouteNotFound(f"no gateway route for {request.method} {request.path}")
        route, upstream_path = matched
        started = time.time()
        length = request.content_length or 0
        body = (
            _RequestBody(request.body, length)
            if request.body is not None and length > 0
            else None
        )
        with self.broker.lease(
            route.provider_id,
            profile_id=request.profile_id,
            billing_kind=route.billing_kind,
        ) as lease:
            profile_id = lease.profile.id
            headers = self._egress_headers(request, route, lease)
        egress = UpstreamRequest(
            method=request.method,
            url=route.upstream_url(upstream_path, request.query),
            headers=headers,
            body=body,
            timeout=self.timeout,
        )
        try:
            upstream = self.transport(egress)
        finally:
            # Python cannot wipe a str, so dropping the last reference to the
            # revealed credential is the most that can be done here.
            headers.pop(route.credential_header, None)
        response_bytes = 0
        try:
            sink.begin(upstream.status, _response_headers(upstream.headers))
            for chunk in upstream.chunks(self.chunk_bytes):
                response_bytes += len(chunk)
                sink.write(chunk)
            sink.finish()
        finally:
            upstream.close()
        return self._record(
            GatewayRecord(
                provider_id=route.provider_id,
                route_id=route.id,
                profile_id=profile_id,
                method=request.method,
                path=upstream_path,
                status=upstream.status,
                request_bytes=body.bytes_read if body is not None else 0,
                response_bytes=response_bytes,
                duration_seconds=time.time() - started,
                started_at=started,
            )
        )

    def _egress_headers(
        self, request: GatewayRequest, route: GatewayRoute, lease: CredentialLease
    ) -> dict[str, str]:
        headers = {
            name: value
            for name, value in request.headers
            if name.lower() not in _STRIPPED_REQUEST_HEADERS
            and name.lower() != route.credential_header.lower()
        }
        headers.update(route.headers)
        headers[route.credential_header] = _credential_value(route, lease)
        return headers

    def _record(self, record: GatewayRecord) -> GatewayRecord:
        self._records.append(record)
        LOGGER.info(
            "forwarded %s %s %s status=%d profile=%s bytes_in=%d bytes_out=%d "
            "duration_ms=%.1f",
            record.method,
            record.provider_id,
            record.path,
            record.status,
            record.profile_id,
            record.request_bytes,
            record.response_bytes,
            record.duration_seconds * 1000,
        )
        return record


def make_gateway_handler(gateway: InferenceGateway) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ModelWiringGateway/0.1"

        def do_GET(self) -> None:
            self._forward("GET")

        def do_POST(self) -> None:
            self._forward("POST")

        def log_message(self, format: str, *args: object) -> None:
            # A request line can name a model and carry a query; the gateway
            # writes its own metadata record instead of an access log.
            del format, args

        def _forward(self, method: str) -> None:
            sink = _HandlerSink(self)
            declared = self.headers.get("Content-Length")
            if declared is None and "chunked" in (
                self.headers.get("Transfer-Encoding") or ""
            ):
                self._refuse(
                    sink,
                    HTTPStatus.LENGTH_REQUIRED,
                    "length_required",
                    "a request body must declare Content-Length",
                )
                return
            request = GatewayRequest(
                method=method,
                target=self.path,
                headers=tuple(self.headers.items()),
                body=self.rfile,
                content_length=int(declared) if declared else None,
                token=_presented_token(self.headers.get("Authorization")),
                profile_id=self.headers.get(PROFILE_HEADER),
            )
            try:
                gateway.forward(request, sink)
            except GatewayUnauthorized as exc:
                self._refuse(sink, HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
            except RouteNotFound as exc:
                self._refuse(sink, HTTPStatus.NOT_FOUND, "not_found", str(exc))
            except CredentialError as exc:
                self._refuse(
                    sink,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "credential_unavailable",
                    str(exc),
                )
            except (GatewayError, OSError) as exc:
                # A connection that fails mid-stream ends the response rather
                # than replacing it: the client has already seen the provider's
                # own status, and no second attempt is made.
                self._refuse(sink, HTTPStatus.BAD_GATEWAY, "upstream_error", str(exc))

        def _refuse(
            self, sink: _HandlerSink, status: HTTPStatus, kind: str, message: str
        ) -> None:
            if sink.started:
                # The provider's own status already reached the client; there is
                # no honest way to replace it now.
                self.close_connection = True
                return
            LOGGER.warning("refused %s %s: %s", self.command, kind, message)
            payload = json.dumps(
                {"error": {"type": kind, "message": message}},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self._drain()
            self.send_response_only(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def _drain(self) -> None:
            """Discard a bounded prefix of an unforwarded body, unread.

            Closing a socket with unread bytes costs the client the response it
            was about to read, so a small body is consumed and dropped.
            """

            declared = self.headers.get("Content-Length")
            length = int(declared) if declared and declared.isdigit() else 0
            while 0 < length <= DRAIN_LIMIT_BYTES:
                chunk = self.rfile.read(min(length, CHUNK_BYTES))
                if not chunk:
                    return
                length -= len(chunk)

    return Handler


class _HandlerSink:
    """Writes a forwarded response straight out to the client socket."""

    __slots__ = ("_chunked", "_handler", "started")

    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        self._handler = handler
        self._chunked = False
        self.started = False

    def begin(self, status: int, headers: Sequence[tuple[str, str]]) -> None:
        self._handler.send_response_only(status)
        for name, value in headers:
            self._handler.send_header(name, value)
        self._chunked = status not in _BODILESS_STATUSES and not any(
            name.lower() == "content-length" for name, _ in headers
        )
        if self._chunked:
            self._handler.send_header("Transfer-Encoding", "chunked")
        self._handler.end_headers()
        self.started = True

    def write(self, chunk: bytes) -> None:
        if self._chunked:
            self._handler.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
        else:
            self._handler.wfile.write(chunk)

    def finish(self) -> None:
        if self._chunked:
            self._handler.wfile.write(b"0\r\n\r\n")


def gateway_server(
    gateway: InferenceGateway, *, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """Bind a loopback listener. A reachable gateway can spend a subscription."""

    if host not in LOOPBACK_HOSTS:
        raise ValueError("the inference gateway is loopback-only")
    server = ThreadingHTTPServer((host, port), make_gateway_handler(gateway))
    server.daemon_threads = True
    return server


def serve_gateway(
    gateway: InferenceGateway, *, host: str = "127.0.0.1", port: int = 0
) -> None:
    server = gateway_server(gateway, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _credential_value(route: GatewayRoute, lease: CredentialLease) -> str:
    try:
        secret = lease.reveal(route.credential_key)
    except CredentialError as exc:
        raise CredentialError(
            f"the credential for provider {route.provider_id} cannot be injected at "
            f"egress: profile {lease.profile.id} is {lease.profile.auth_kind}"
        ) from exc
    scheme = route.credential_scheme.strip()
    return f"{scheme} {secret}" if scheme else secret


def _response_headers(
    headers: Sequence[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """The provider's own headers, minus the ones that end at this hop."""

    return tuple(
        (name, value)
        for name, value in headers
        if name.lower() not in HOP_BY_HOP_HEADERS
    )


def _presented_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != BEARER_SCHEME.lower():
        return None
    return value.strip() or None


def _path_matches(pattern: str, path: str) -> bool:
    """A declared path is exact unless it ends in ``/*``."""

    if pattern.endswith("/*"):
        stem = pattern[:-2]
        return path == stem or path.startswith(f"{stem}/")
    return path == pattern


def _declared_billing_kind(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text != "unknown" else None


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
