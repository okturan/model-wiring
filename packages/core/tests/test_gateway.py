from __future__ import annotations

import json
import logging
import tempfile
import threading
import unittest
from http.client import HTTPConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from helpers import fixture_catalog
from model_wiring import (
    AuthBroker,
    Catalog,
    CredentialMaterial,
    CredentialProfile,
    MemorySecretStore,
    ProfileRegistry,
)
from model_wiring.broker import CredentialBroker
from model_wiring.gateway import (
    GatewayRecord,
    GatewayRoute,
    InferenceGateway,
    gateway_routes,
    gateway_server,
    provider_gateway_routes,
)

PROVIDER_SECRET = "acme-provider-secret"

# Only how often a stopped test server notices; the default costs half a second
# per teardown.
POLL_INTERVAL = 0.01


class _ProviderHandler(BaseHTTPRequestHandler):
    """A provider's own API, scripted by the test that owns it."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _serve(self, method: str) -> None:
        provider = self.server.provider  # type: ignore[attr-defined]
        provider.requests.append(
            {
                "method": method,
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
            }
        )
        provider.bodies.append(self._read_body(provider))
        if provider.chunks is None:
            self.send_response(provider.status)
            for key, value in provider.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(provider.body)))
            self.end_headers()
            self.wfile.write(provider.body)
            return
        self.send_response(provider.status)
        for key, value in provider.headers.items():
            self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index, chunk in enumerate(provider.chunks):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            if index == 0:
                provider.sent_first_chunk.set()
                if not provider.release.wait(timeout=30):
                    raise RuntimeError("the test never released the provider")
        self.wfile.write(b"0\r\n\r\n")

    def _read_body(self, provider: FakeProvider) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return b""
        if not provider.prefix_bytes:
            return self.rfile.read(length)
        prefix = self.rfile.read(provider.prefix_bytes)
        provider.received_prefix.set()
        remaining = length - len(prefix)
        return prefix + (self.rfile.read(remaining) if remaining > 0 else b"")


class _ProviderServer(ThreadingHTTPServer):
    daemon_threads = True
    provider: FakeProvider


class FakeProvider:
    """A local stand-in for a provider endpoint. Never a real provider."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.bodies: list[bytes] = []
        self.status = 200
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        self.body = b'{"ok": true}'
        self.chunks: tuple[bytes, ...] | None = None
        self.prefix_bytes = 0
        self.release = threading.Event()
        self.sent_first_chunk = threading.Event()
        self.received_prefix = threading.Event()
        self._server = _ProviderServer(("127.0.0.1", 0), _ProviderHandler)
        self._server.provider = self
        self._thread = threading.Thread(
            target=self._server.serve_forever, args=(POLL_INTERVAL,), daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)


class CountingStore(MemorySecretStore):
    """Proves whether a request path read secret material at all."""

    name = "counting"

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get(self, reference: str) -> CredentialMaterial:
        self.reads += 1
        return super().get(reference)


def gateway_overlay(
    base_url: str | None,
    *,
    paths: list[str] | None = None,
    credential_header: str = "Authorization",
    credential_scheme: str = "Bearer",
    api_url: str | None = None,
    gateway: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything provider-specific lives here, as catalogue data."""

    declared: dict[str, Any] = {
        "mount": "/acme",
        "paths": paths if paths is not None else ["/v1/chat/completions", "/v1/models"],
        "methods": ["POST", "GET"],
        "credential_header": credential_header,
        "credential_scheme": credential_scheme,
    }
    if base_url is not None:
        declared["base_url"] = base_url
    if gateway is not None:
        declared.update(gateway)
    return {
        "providers": {
            "acme": {
                "api_url": api_url,
                "metadata": {
                    "access_routes": [
                        {
                            "id": "api",
                            "kind": "api_key",
                            "billing_kind": "api",
                            "label": "Acme API key",
                            "env": ["ACME_KEY"],
                            "metadata": {"gateway": declared},
                        }
                    ]
                },
            }
        }
    }


class GatewayRouteDataTests(unittest.TestCase):
    def test_routes_are_read_from_access_route_metadata(self) -> None:
        catalog = fixture_catalog(
            overlays=[gateway_overlay("http://127.0.0.1:9111/base")]
        )

        routes = gateway_routes(catalog)

        self.assertEqual(1, len(routes))
        route = routes[0]
        self.assertEqual("acme", route.provider_id)
        self.assertEqual("api", route.id)
        self.assertEqual("/acme", route.mount)
        self.assertEqual("http://127.0.0.1:9111/base", route.base_url)
        self.assertEqual(("/v1/chat/completions", "/v1/models"), route.paths)
        self.assertEqual(("POST", "GET"), route.methods)
        self.assertEqual("api", route.billing_kind)

    def test_a_provider_declaring_no_gateway_data_exposes_no_route(self) -> None:
        catalog = fixture_catalog()

        self.assertEqual((), gateway_routes(catalog))

    def test_base_url_falls_back_to_the_catalogued_api_url(self) -> None:
        catalog = fixture_catalog(
            overlays=[gateway_overlay(None, api_url="http://127.0.0.1:9112")]
        )

        self.assertEqual("http://127.0.0.1:9112", gateway_routes(catalog)[0].base_url)

    def test_a_route_with_no_base_url_anywhere_is_not_exposed(self) -> None:
        catalog = fixture_catalog(overlays=[gateway_overlay(None)])

        self.assertEqual((), gateway_routes(catalog))

    def test_declared_paths_match_exactly_and_by_prefix(self) -> None:
        catalog = fixture_catalog(
            overlays=[
                gateway_overlay(
                    "http://127.0.0.1:9113", paths=["/v1/models", "/v1/responses/*"]
                )
            ]
        )
        route = gateway_routes(catalog)[0]

        self.assertEqual("/v1/models", route.match("POST", "/acme/v1/models"))
        self.assertEqual(
            "/v1/responses/abc", route.match("POST", "/acme/v1/responses/abc")
        )
        self.assertEqual("/v1/responses", route.match("POST", "/acme/v1/responses"))
        self.assertIsNone(route.match("POST", "/acme/v1/chat/completions"))
        self.assertIsNone(route.match("DELETE", "/acme/v1/models"))
        self.assertIsNone(route.match("POST", "/acmex/v1/models"))
        self.assertIsNone(route.match("POST", "/v1/models"))

    def test_provider_routes_survive_a_serialization_round_trip(self) -> None:
        catalog = fixture_catalog(overlays=[gateway_overlay("http://127.0.0.1:9114")])
        provider = catalog.provider("acme")

        route = provider_gateway_routes(provider)[0]
        payload = json.dumps(route.to_dict())

        self.assertEqual(route, GatewayRoute.from_dict(json.loads(payload)))

    def test_the_module_embeds_no_provider_endpoint(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src" / "model_wiring" / "gateway.py"
        ).read_text(encoding="utf-8")

        offenders = [line.strip() for line in source.splitlines() if "://" in line]

        self.assertEqual(
            [], offenders, "a provider endpoint is written into the module"
        )


class ObservedGateway(InferenceGateway):
    """Signals each completed record so a test never races the handler thread.

    A client reads the last byte of a response before the thread that wrote it
    appends its record, so reading `records` straight after a call is a race.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._recorded = threading.Condition()

    def _record(self, record: GatewayRecord) -> GatewayRecord:
        result = super()._record(record)
        with self._recorded:
            self._recorded.notify_all()
        return result

    def wait_for_records(
        self, count: int = 1, timeout: float = 10.0
    ) -> tuple[GatewayRecord, ...]:
        with self._recorded:
            self._recorded.wait_for(lambda: len(self.records) >= count, timeout)
        return self.records


class GatewayTestCase(unittest.TestCase):
    """A gateway, a broker over a temporary store, and one fake provider."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.profiles = ProfileRegistry(self.root / "profiles.sqlite3")
        self.store = CountingStore()
        self.provider = FakeProvider()
        self.addCleanup(self.provider.close)
        self.addCleanup(self.directory.cleanup)
        self.catalog = self.build_catalog()
        self.broker = CredentialBroker(
            AuthBroker(self.profiles, stores={"counting": self.store}),
            catalog=self.catalog,
            environ={},
        )
        self.gateway = ObservedGateway(self.broker, gateway_routes(self.catalog))
        self.server = gateway_server(self.gateway, port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, args=(POLL_INTERVAL,), daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop_server)

    def build_catalog(self) -> Catalog:
        return fixture_catalog(overlays=[gateway_overlay(self.provider.base_url)])

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def records(self, count: int = 1) -> tuple[GatewayRecord, ...]:
        """The records for `count` finished requests, once they are finished."""

        return self.gateway.wait_for_records(count)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def seed_profile(
        self,
        profile_id: str = "acme-api",
        *,
        secret: str = PROVIDER_SECRET,
        priority: int = 100,
    ) -> CredentialProfile:
        material = CredentialMaterial({"api_key": secret})
        self.store.put(profile_id, material)
        material.wipe()
        profile = CredentialProfile(
            id=profile_id,
            provider_id="acme",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref=profile_id,
            secret_store="counting",
            priority=priority,
        )
        self.profiles.upsert(profile)
        return profile

    def connect(self, *, timeout: float = 10.0) -> HTTPConnection:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        self.addCleanup(connection.close)
        return connection

    def call(
        self,
        path: str = "/acme/v1/chat/completions",
        *,
        token: str | None = "issued",
        method: str = "POST",
        body: bytes = b'{"model": "acme/gpt", "messages": []}',
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> HTTPResponse:
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        if token is not None:
            presented = self.gateway.token if token == "issued" else token
            request_headers["Authorization"] = f"Bearer {presented}"
        connection = self.connect(timeout=timeout)
        connection.request(method, path, body=body, headers=request_headers)
        return connection.getresponse()


class GatewayAuthenticationTests(GatewayTestCase):
    def test_a_request_without_a_token_reads_nothing_and_calls_nobody(self) -> None:
        self.seed_profile()

        response = self.call(token=None)
        payload = response.read()

        self.assertEqual(401, response.status)
        self.assertEqual([], self.provider.requests)
        self.assertEqual(0, self.store.reads)
        self.assertNotIn(PROVIDER_SECRET, payload.decode("utf-8"))

    def test_a_request_with_a_wrong_token_reads_nothing_and_calls_nobody(self) -> None:
        self.seed_profile()

        response = self.call(token="not-the-issued-token")
        response.read()

        self.assertEqual(401, response.status)
        self.assertEqual([], self.provider.requests)
        self.assertEqual(0, self.store.reads)

    def test_an_unknown_route_is_refused_before_the_token_is_even_useful(self) -> None:
        self.seed_profile()

        response = self.call("/acme/v1/unknown", token=None)
        response.read()

        self.assertEqual(401, response.status)
        self.assertEqual([], self.provider.requests)

    def test_the_issued_token_proceeds(self) -> None:
        self.seed_profile()

        response = self.call()
        payload = response.read()

        self.assertEqual(200, response.status)
        self.assertEqual(b'{"ok": true}', payload)
        self.assertEqual(1, len(self.provider.requests))

    def test_every_gateway_issues_its_own_unguessable_token(self) -> None:
        other = InferenceGateway(self.broker, gateway_routes(self.catalog))

        self.assertNotEqual(self.gateway.token, other.token)
        self.assertGreaterEqual(len(self.gateway.token), 32)

    def test_a_supplied_token_is_honoured_so_a_caller_can_persist_one(self) -> None:
        gateway = InferenceGateway(self.broker, (), token="chosen-token")

        self.assertEqual("chosen-token", gateway.token)

    def test_the_gateway_refuses_to_bind_a_non_loopback_interface(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            gateway_server(self.gateway, host="0.0.0.0", port=0)


class EgressInjectionTests(GatewayTestCase):
    def test_a_client_sending_only_the_gateway_token_reaches_the_provider(
        self,
    ) -> None:
        self.seed_profile()

        response = self.call()
        payload = response.read()
        sent = self.provider.requests[0]["headers"]

        self.assertEqual(200, response.status)
        self.assertEqual(f"Bearer {PROVIDER_SECRET}", sent["authorization"])
        self.assertNotIn(PROVIDER_SECRET, payload.decode("utf-8"))
        self.assertNotIn(PROVIDER_SECRET, str(dict(response.getheaders())))
        self.assertNotIn(
            PROVIDER_SECRET,
            json.dumps([record.to_dict() for record in self.records()]),
        )

    def test_the_gateway_token_is_never_forwarded_to_the_provider(self) -> None:
        self.seed_profile()

        self.call().read()
        sent = self.provider.requests[0]["headers"]

        self.assertNotIn(self.gateway.token, json.dumps(sent))
        self.assertEqual(f"Bearer {PROVIDER_SECRET}", sent["authorization"])

    def test_the_client_request_body_reaches_the_provider_unchanged(self) -> None:
        self.seed_profile()
        body = b'{"model": "acme/gpt", "messages": [{"role": "user"}]}'

        self.call(body=body).read()

        self.assertEqual(body, self.provider.bodies[0])
        self.assertEqual(len(body), self.records()[0].request_bytes)

    def test_a_routing_hint_header_is_ours_and_stops_at_the_gateway(self) -> None:
        self.seed_profile()

        self.call(headers={"X-Model-Wiring-Profile": "acme-api"}).read()
        sent = self.provider.requests[0]["headers"]

        self.assertNotIn("x-model-wiring-profile", sent)
        self.assertEqual("acme-api", self.records()[0].profile_id)

    def test_no_resolvable_credential_fails_before_any_provider_request(self) -> None:
        response = self.call()
        payload = json.loads(response.read())

        self.assertEqual(503, response.status)
        self.assertEqual([], self.provider.requests)
        self.assertEqual("credential_unavailable", payload["error"]["type"])
        self.assertIn("acme", payload["error"]["message"])

    def test_a_delegated_sign_in_cannot_be_injected_and_says_so(self) -> None:
        self.profiles.upsert(
            CredentialProfile(
                id="acme-delegated",
                provider_id="acme",
                auth_kind="delegated",
                billing_kind="api",
                metadata={"delegate": "acme-cli"},
            )
        )

        response = self.call()
        payload = json.loads(response.read())

        self.assertEqual(503, response.status)
        self.assertEqual([], self.provider.requests)
        self.assertIn("delegated", payload["error"]["message"])
        self.assertNotIn(PROVIDER_SECRET, payload["error"]["message"])


class DeclaredCredentialShapeTests(GatewayTestCase):
    """The header a provider wants its key in is data, not a branch here."""

    def build_catalog(self) -> Catalog:
        return fixture_catalog(
            overlays=[
                gateway_overlay(
                    self.provider.base_url,
                    credential_header="x-api-key",
                    credential_scheme="",
                    gateway={"headers": {"acme-version": "2026-01-01"}},
                )
            ]
        )

    def test_the_credential_header_and_scheme_come_from_route_data(self) -> None:
        self.seed_profile()

        self.call().read()
        sent = self.provider.requests[0]["headers"]

        self.assertEqual(PROVIDER_SECRET, sent["x-api-key"])
        self.assertNotIn("authorization", sent)
        self.assertNotIn(self.gateway.token, json.dumps(sent))

    def test_declared_static_headers_travel_with_the_request(self) -> None:
        self.seed_profile()

        self.call().read()

        self.assertEqual(
            "2026-01-01", self.provider.requests[0]["headers"]["acme-version"]
        )


class StreamingPassthroughTests(GatewayTestCase):
    def test_response_chunks_are_forwarded_as_they_arrive(self) -> None:
        self.seed_profile()
        self.provider.headers = {"Content-Type": "text/event-stream"}
        self.provider.chunks = (b'data: {"delta": "one"}\n\n', b"data: [DONE]\n\n")

        response = self.call(timeout=10)
        # The provider is blocked until `release`, so a gateway that waited for
        # the whole body could not have produced this first chunk.
        first = response.read1(4096)

        self.assertTrue(self.provider.sent_first_chunk.is_set())
        self.assertFalse(self.provider.release.is_set())
        self.assertEqual(b'data: {"delta": "one"}\n\n', first)
        self.assertEqual("text/event-stream", response.getheader("Content-Type"))
        self.provider.release.set()
        self.assertEqual(b"data: [DONE]\n\n", response.read())
        self.assertEqual(
            sum(len(chunk) for chunk in self.provider.chunks or ()),
            self.records()[0].response_bytes,
        )

    def test_the_request_body_is_forwarded_before_the_client_finishes_it(self) -> None:
        self.seed_profile()
        head = b'{"prompt": "one'
        tail = b'-two"}'
        self.provider.prefix_bytes = len(head)
        connection = self.connect(timeout=10)
        connection.putrequest("POST", "/acme/v1/chat/completions")
        connection.putheader("Authorization", f"Bearer {self.gateway.token}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(head) + len(tail)))
        connection.endheaders()

        connection.send(head)
        # Nothing has closed the request body yet; a gateway that buffered it
        # whole would still be waiting rather than sending.
        arrived = self.provider.received_prefix.wait(timeout=10)
        connection.send(tail)
        response = connection.getresponse()
        response.read()

        self.assertTrue(arrived, "the gateway held the request body")
        self.assertEqual(200, response.status)
        self.assertEqual(head + tail, self.provider.bodies[0])

    def test_a_body_without_a_declared_length_is_refused_before_egress(self) -> None:
        self.seed_profile()
        connection = self.connect()
        connection.putrequest("POST", "/acme/v1/chat/completions")
        connection.putheader("Authorization", f"Bearer {self.gateway.token}")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()

        response = connection.getresponse()
        response.read()

        self.assertEqual(411, response.status)
        self.assertEqual([], self.provider.requests)


class CapturingHandler(logging.Handler):
    """Keeps every record anything in the process emits."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        return "\n".join(
            f"{record.name} {record.getMessage()} {record.args!r} {record.__dict__!r}"
            for record in self.records
        )


class ContentHandlingTests(GatewayTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.captured = CapturingHandler()
        root = logging.getLogger()
        self.addCleanup(root.setLevel, root.level)
        self.addCleanup(root.removeHandler, self.captured)
        root.addHandler(self.captured)
        root.setLevel(logging.DEBUG)

    def test_prompt_text_reaches_no_log_record_metric_or_stored_file(self) -> None:
        self.seed_profile()
        prompt = "MAGIC-PROMPT-84f1c2"
        completion = "MAGIC-COMPLETION-19b7ad"
        self.provider.body = json.dumps({"content": completion}).encode("utf-8")
        request_body = json.dumps({"model": "acme/gpt", "prompt": prompt}).encode(
            "utf-8"
        )

        response = self.call(body=request_body)
        payload = response.read().decode("utf-8")

        self.assertEqual(200, response.status)
        self.assertIn(completion, payload)
        self.assertEqual(prompt, json.loads(self.provider.bodies[0])["prompt"])
        # The gateway logs from its worker thread, which can still be running
        # when the client has finished reading. Wait for the request to be
        # recorded before judging what was logged, or this races.
        metrics = json.dumps([record.to_dict() for record in self.records()])
        self.assertTrue(self.captured.records, "nothing logged, so nothing was checked")
        self.assertTrue(
            any(
                record.name.startswith("model_wiring")
                for record in self.captured.records
            ),
            "the gateway logged nothing, so this proves nothing",
        )
        logged = self.captured.text()
        for secret in (prompt, completion, PROVIDER_SECRET, self.gateway.token):
            self.assertNotIn(secret, logged)
            self.assertNotIn(secret, metrics)
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                blob = path.read_bytes()
                self.assertNotIn(prompt.encode("utf-8"), blob, str(path))
                self.assertNotIn(completion.encode("utf-8"), blob, str(path))

    def test_the_record_carries_metadata_and_nothing_else(self) -> None:
        self.seed_profile()

        self.call(body=b'{"prompt": "x"}').read()
        record = self.records()[0]

        self.assertEqual(
            {
                "provider_id",
                "route_id",
                "profile_id",
                "method",
                "path",
                "status",
                "request_bytes",
                "response_bytes",
                "duration_seconds",
                "started_at",
            },
            set(record.to_dict()),
        )
        self.assertEqual("acme", record.provider_id)
        self.assertEqual("acme-api", record.profile_id)
        self.assertEqual("/v1/chat/completions", record.path)
        self.assertEqual(15, record.request_bytes)
        self.assertEqual(200, record.status)


class ProviderFailureTests(GatewayTestCase):
    def test_a_rate_limit_is_returned_verbatim_with_no_retry_or_switch(self) -> None:
        self.seed_profile(priority=10)
        self.seed_profile("acme-spare", secret="spare-secret", priority=90)
        self.provider.status = 429
        self.provider.body = b'{"error": {"type": "rate_limit_error"}}'
        self.provider.headers = {
            "Content-Type": "application/json",
            "Retry-After": "30",
        }

        response = self.call()
        payload = response.read()

        self.assertEqual(429, response.status)
        self.assertEqual(b'{"error": {"type": "rate_limit_error"}}', payload)
        self.assertEqual("30", response.getheader("Retry-After"))
        self.assertEqual(1, len(self.provider.requests))
        self.assertEqual(
            f"Bearer {PROVIDER_SECRET}",
            self.provider.requests[0]["headers"]["authorization"],
        )
        self.assertEqual([429], [record.status for record in self.records()])

    def test_an_unreachable_provider_is_reported_and_not_retried(self) -> None:
        self.seed_profile()
        self.provider.close()

        response = self.call()
        payload = json.loads(response.read())

        self.assertEqual(502, response.status)
        self.assertEqual("upstream_error", payload["error"]["type"])
        self.assertEqual((), self.gateway.records)


class SingleRequestTests(GatewayTestCase):
    def test_an_unknown_path_returns_404_without_contacting_a_provider(self) -> None:
        self.seed_profile()

        response = self.call("/acme/v1/embeddings")
        payload = json.loads(response.read())

        self.assertEqual(404, response.status)
        self.assertEqual([], self.provider.requests)
        self.assertEqual(0, self.store.reads)
        self.assertEqual("not_found", payload["error"]["type"])

    def test_a_path_under_no_declared_mount_returns_404(self) -> None:
        self.seed_profile()

        response = self.call("/openai/v1/chat/completions")
        response.read()

        self.assertEqual(404, response.status)
        self.assertEqual([], self.provider.requests)

    def test_only_the_methods_a_route_declares_reach_the_provider(self) -> None:
        self.seed_profile()

        declared = self.call("/acme/v1/models", method="GET", body=b"")
        declared.read()
        undeclared = self.call("/acme/v1/models", method="DELETE", body=b"")
        undeclared.read()

        self.assertEqual(200, declared.status)
        self.assertEqual(501, undeclared.status)
        self.assertEqual(1, len(self.provider.requests))

    def test_a_tool_use_response_is_returned_as_is_with_no_further_action(self) -> None:
        self.seed_profile()
        self.provider.body = json.dumps(
            {
                "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "name": "read_file", "input": {}}],
            }
        ).encode("utf-8")

        response = self.call()
        payload = response.read()

        self.assertEqual(200, response.status)
        self.assertEqual(self.provider.body, payload)
        self.assertEqual(1, len(self.provider.requests))
        self.assertEqual(1, len(self.records()))


if __name__ == "__main__":
    unittest.main()
