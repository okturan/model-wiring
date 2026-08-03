from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from typing import Any

from helpers import fixture_catalog
from model_wiring import Catalog, CredentialProfile, ProfileRegistry
from model_wiring.api import ProviderService, api_server, issue_token, serve

# Only how often a stopped test server notices; the default costs half a second
# per teardown.
POLL_INTERVAL = 0.01


class ProviderServiceTests(unittest.TestCase):
    def test_provider_endpoint_uses_popularity_before_alphabetical_fallback(
        self,
    ) -> None:
        service = ProviderService(fixture_catalog())

        status, payload = service.get("/v1/providers", {})
        ids = [item["id"] for item in payload["items"]]

        self.assertEqual(200, status)
        self.assertLess(ids.index("openai"), ids.index("acme"))

    def test_empty_model_query_browses_the_catalog(self) -> None:
        service = ProviderService(fixture_catalog())

        status, payload = service.get(
            "/v1/models", {"provider": ["openai-codex"], "limit": ["10"]}
        )

        self.assertEqual(200, status)
        self.assertGreater(len(payload["items"]), 0)
        self.assertTrue(
            all(
                item["model"]["provider_id"] == "openai-codex"
                for item in payload["items"]
            )
        )

    def test_service_returns_only_public_profile_and_plan_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = ProfileRegistry(Path(directory) / "profiles.sqlite3")
            profiles.upsert(
                CredentialProfile(
                    id="codex",
                    provider_id="openai-codex",
                    auth_kind="delegated",
                    billing_kind="subscription",
                    metadata={"delegate": "codex-sdk"},
                )
            )
            service = ProviderService(fixture_catalog(), profiles)

            status, profile_payload = service.get("/v1/profiles", {})
            self.assertEqual(200, status)
            self.assertEqual("codex", profile_payload["items"][0]["id"])

            status, plan = service.post(
                "/v1/select",
                {
                    "model": "openai-codex/gpt-5.6-luna",
                    "credential_profile": "codex",
                    "effort": "high",
                    "tier": "fast",
                },
            )
            self.assertEqual(200, status)
            self.assertEqual("subscription", plan["billing_kind"])
            self.assertNotIn("token", str(plan).lower())

    def test_ambiguous_selection_is_a_conflict_with_candidates(self) -> None:
        service = ProviderService(fixture_catalog())
        status, payload = service.post("/v1/select", {"query": "luna"})

        self.assertEqual(409, status)
        self.assertGreater(len(payload["error"]["candidates"]), 1)

    def test_embedded_apps_can_supply_in_memory_profile_metadata(self) -> None:
        profile = CredentialProfile(
            id="atlas-codex-subscription",
            provider_id="openai-codex",
            auth_kind="delegated",
            billing_kind="subscription",
        )
        service = ProviderService(fixture_catalog(), (profile,))

        status, payload = service.get("/v1/profiles", {})

        self.assertEqual(200, status)
        self.assertEqual(
            ["atlas-codex-subscription"], [item["id"] for item in payload["items"]]
        )


class CountingService(ProviderService):
    """Proves whether a refused request ever reached the service at all."""

    def __init__(self, catalog: Catalog, profiles: Any = None) -> None:
        super().__init__(catalog, profiles)
        self.calls: list[str] = []

    def get(self, path: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        self.calls.append(f"GET {path}")
        return super().get(path, query)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(f"POST {path}")
        return super().post(path, body)


class ApiServerTestCase(unittest.TestCase):
    """A bound loopback service and a client that may or may not hold its token."""

    def setUp(self) -> None:
        self.service = CountingService(fixture_catalog())
        self.token = issue_token()
        self.port = self.start(self.service, self.token)

    def start(
        self,
        service: ProviderService,
        token: str,
        *,
        allowed_origin: str | None = None,
    ) -> int:
        server = api_server(service, token=token, port=0, allowed_origin=allowed_origin)
        thread = threading.Thread(
            target=server.serve_forever, args=(POLL_INTERVAL,), daemon=True
        )
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

        self.addCleanup(stop)
        return int(server.server_address[1])

    def call(
        self,
        path: str = "/v1/providers",
        *,
        token: str | None = "issued",
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        port: int | None = None,
    ) -> HTTPResponse:
        request_headers = dict(headers or {})
        if token is not None:
            presented = self.token if token == "issued" else token
            request_headers["Authorization"] = f"Bearer {presented}"
        connection = HTTPConnection("127.0.0.1", port or self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=request_headers)
        return connection.getresponse()


class ApiAuthenticationTests(ApiServerTestCase):
    def test_a_request_without_a_token_never_reaches_the_service(self) -> None:
        response = self.call(token=None)
        payload = json.loads(response.read())

        self.assertEqual(401, response.status)
        self.assertEqual([], self.service.calls)
        self.assertEqual("unauthorized", payload["error"]["type"])

    def test_a_wrong_token_is_refused_and_the_real_one_is_never_echoed(self) -> None:
        response = self.call(token="not-the-issued-token")
        payload = response.read().decode("utf-8")

        self.assertEqual(401, response.status)
        self.assertEqual([], self.service.calls)
        self.assertNotIn(self.token, payload)

    def test_a_prefix_of_the_token_is_not_enough(self) -> None:
        response = self.call(token=self.token[:-1])
        response.read()

        self.assertEqual(401, response.status)
        self.assertEqual([], self.service.calls)

    def test_a_token_in_another_scheme_is_not_accepted(self) -> None:
        response = self.call(
            token=None, headers={"Authorization": f"Basic {self.token}"}
        )
        response.read()

        self.assertEqual(401, response.status)
        self.assertEqual([], self.service.calls)

    def test_the_issued_token_serves_the_catalogue(self) -> None:
        response = self.call()
        payload = json.loads(response.read())

        self.assertEqual(200, response.status)
        self.assertTrue(payload["items"])
        self.assertEqual(["GET /v1/providers"], self.service.calls)

    def test_every_route_requires_the_token_including_liveness(self) -> None:
        refused = [
            self.call(path, token=None).status
            for path in ("/healthz", "/v1/catalog", "/v1/models", "/v1/profiles")
        ]

        self.assertEqual([401, 401, 401, 401], refused)
        self.assertEqual([], self.service.calls)

    def test_a_post_without_a_token_never_reaches_the_selector(self) -> None:
        response = self.call(
            "/v1/select",
            token=None,
            method="POST",
            body=json.dumps({"query": "luna"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        payload = json.loads(response.read())

        self.assertEqual(401, response.status)
        self.assertEqual([], self.service.calls)
        self.assertEqual("unauthorized", payload["error"]["type"])

    def test_a_post_with_the_token_still_selects(self) -> None:
        response = self.call(
            "/v1/select",
            method="POST",
            body=json.dumps({"model": "openai-codex/gpt-5.6-luna"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        payload = json.loads(response.read())

        self.assertEqual(200, response.status)
        self.assertEqual("openai-codex", payload["provider_id"])

    def test_the_refusal_names_the_scheme_a_client_must_use(self) -> None:
        response = self.call(token=None)
        response.read()

        self.assertEqual("Bearer", response.getheader("WWW-Authenticate"))


class ApiTokenTests(unittest.TestCase):
    def test_every_start_issues_a_different_unguessable_token(self) -> None:
        first = issue_token()
        second = issue_token()

        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 32)

    def test_a_service_cannot_be_started_without_a_token(self) -> None:
        service = ProviderService(fixture_catalog())

        with self.assertRaises(TypeError):
            serve(service)  # type: ignore[call-arg]

    def test_an_empty_token_is_refused_rather_than_serving_nobody(self) -> None:
        service = ProviderService(fixture_catalog())

        with self.assertRaisesRegex(ValueError, "token"):
            api_server(service, token="", port=0)

    def test_the_service_still_binds_loopback_only(self) -> None:
        service = ProviderService(fixture_catalog())

        with self.assertRaisesRegex(ValueError, "loopback"):
            api_server(service, token=issue_token(), host="0.0.0.0", port=0)


class ApiCorsTests(ApiServerTestCase):
    def test_a_browser_preflight_is_answered_so_the_header_can_be_sent(self) -> None:
        # A preflight never carries Authorization, so refusing it would leave a
        # browser client unable to ever present the token.
        port = self.start(
            self.service, self.token, allowed_origin="http://localhost:5173"
        )

        response = self.call("/v1/providers", token=None, method="OPTIONS", port=port)
        response.read()

        self.assertEqual(204, response.status)
        self.assertIn(
            "authorization",
            (response.getheader("Access-Control-Allow-Headers") or "").lower(),
        )
        self.assertEqual([], self.service.calls)


if __name__ == "__main__":
    unittest.main()
