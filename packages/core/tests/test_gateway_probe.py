from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from helpers import fixture_catalog
from model_wiring import (
    AuthBroker,
    Catalog,
    CredentialMaterial,
    CredentialProfile,
    ProfileRegistry,
)
from model_wiring.broker import CredentialBroker
from model_wiring.gateway import InferenceGateway, gateway_routes
from model_wiring.probe import (
    PROBE_DRIVERS,
    Prober,
    gateway_probe_driver,
    gateway_probe_drivers,
)
from test_gateway import PROVIDER_SECRET, CountingStore, FakeProvider, gateway_overlay
from test_login_cli import CliFixture, run


class GatewayProbeTestCase(unittest.TestCase):
    """A prober whose driver verifies through the gateway's own egress path."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.provider = FakeProvider()
        self.addCleanup(self.provider.close)
        self.profiles = ProfileRegistry(self.root / "profiles.sqlite3")
        self.store = CountingStore()
        self.catalog = self.build_catalog()
        self.auth = AuthBroker(self.profiles, stores={"counting": self.store})
        self.broker = CredentialBroker(self.auth, catalog=self.catalog, environ={})
        self.gateway = InferenceGateway(self.broker, gateway_routes(self.catalog))
        self.prober = Prober(self.auth, drivers=gateway_probe_drivers(self.gateway))

    def declared(self) -> dict[str, Any]:
        return {"probe_path": "/v1/models", "probe_method": "GET"}

    def build_catalog(self) -> Catalog:
        return fixture_catalog(
            overlays=[gateway_overlay(self.provider.base_url, gateway=self.declared())]
        )

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


class GatewayProbeTests(GatewayProbeTestCase):
    def test_a_declared_verification_call_reaches_the_provider(self) -> None:
        self.seed_profile()

        result = self.prober.probe("acme-api")

        self.assertEqual("ready", result.state)
        self.assertEqual("usage_api", result.entitlement_class)
        self.assertEqual(1, len(self.provider.requests))
        self.assertEqual("GET", self.provider.requests[0]["method"])
        self.assertEqual("/v1/models", self.provider.requests[0]["path"])
        self.assertEqual(
            f"Bearer {PROVIDER_SECRET}",
            self.provider.requests[0]["headers"]["authorization"],
        )

    def test_the_verification_call_carries_no_application_content(self) -> None:
        self.seed_profile()

        self.prober.probe("acme-api")

        self.assertEqual([b""], self.provider.bodies)

    def test_the_probe_checks_the_profile_it_was_asked_about(self) -> None:
        # The chain would prefer the higher-priority profile; a probe that let it
        # would answer about a credential nobody asked about.
        self.seed_profile("acme-preferred", secret="preferred-secret", priority=10)
        self.seed_profile("acme-spare", secret="spare-secret", priority=90)

        result = self.prober.probe("acme-spare")

        self.assertEqual("ready", result.state)
        self.assertEqual(
            "Bearer spare-secret",
            self.provider.requests[0]["headers"]["authorization"],
        )

    def test_a_rejected_credential_is_reported_as_expired(self) -> None:
        self.seed_profile()
        self.provider.status = 401
        self.provider.body = b'{"error": "invalid api key"}'

        result = self.prober.probe("acme-api")

        self.assertEqual("expired", result.state)
        self.assertIn("401", result.detail or "")

    def test_a_denied_credential_is_reported_as_policy_denied(self) -> None:
        self.seed_profile()
        self.provider.status = 403

        result = self.prober.probe("acme-api")

        self.assertEqual("policy_denied", result.state)

    def test_an_answer_that_proves_nothing_stays_unknown(self) -> None:
        self.seed_profile()
        self.provider.status = 429

        result = self.prober.probe("acme-api")

        self.assertEqual("unknown", result.state)
        self.assertIn("429", result.detail or "")

    def test_a_provider_outage_is_reported_without_raising(self) -> None:
        self.seed_profile()
        self.provider.close()

        result = self.prober.probe("acme-api")

        self.assertEqual("unavailable", result.state)

    def test_the_result_and_the_stored_record_carry_no_secret(self) -> None:
        self.seed_profile()
        self.provider.body = b'{"data": [{"id": "acme/gpt"}]}'

        result = self.prober.probe("acme-api")
        stored = self.profiles.get("acme-api")

        for secret in (PROVIDER_SECRET, self.gateway.token):
            self.assertNotIn(secret, json.dumps(result.to_dict()))
            self.assertNotIn(secret, json.dumps(stored.to_dict()))
        self.assertEqual("ready", stored.metadata["last_probe_state"])

    def test_the_response_body_is_discarded_rather_than_reported(self) -> None:
        self.seed_profile()
        self.provider.body = b'{"data": "MAGIC-MODEL-LIST-31f9"}'

        result = self.prober.probe("acme-api")

        self.assertNotIn("MAGIC-MODEL-LIST-31f9", json.dumps(result.to_dict()))
        self.assertEqual(
            len(self.provider.body), self.gateway.records[0].response_bytes
        )


class UndeclaredVerificationTests(GatewayProbeTestCase):
    """A route that declares no verification call gets no driver."""

    def declared(self) -> dict[str, Any]:
        return {}

    def test_no_driver_is_registered(self) -> None:
        self.assertEqual({}, gateway_probe_drivers(self.gateway))

    def test_the_probe_reports_unknown_rather_than_guessing(self) -> None:
        self.seed_profile()

        result = self.prober.probe("acme-api")

        self.assertEqual("unknown", result.state)
        self.assertEqual([], self.provider.requests)

    def test_building_a_driver_for_such_a_route_is_refused(self) -> None:
        route = gateway_routes(self.catalog)[0]

        with self.assertRaisesRegex(ValueError, "verification"):
            gateway_probe_driver(self.gateway, route)


class ProbeCommandTests(CliFixture):
    """`access probe` verifies through a real call when the data declares one."""

    def setUp(self) -> None:
        super().setUp()
        self.provider = FakeProvider()
        self.addCleanup(self.provider.close)
        overlay = self.workspace / "gateway-overlay.json"
        overlay.write_text(
            json.dumps(
                gateway_overlay(
                    self.provider.base_url,
                    paths=["/v1/models"],
                    gateway={"methods": ["GET"], "probe_path": "/v1/models"},
                )
            ),
            encoding="utf-8",
        )
        self.overlay = ("--overlay", str(overlay))
        os.environ["ACME_KEY"] = "acme-environment-secret"
        self.addCleanup(os.environ.pop, "ACME_KEY", None)
        run(
            *self.common,
            "profile",
            "add",
            "--id",
            "acme-api",
            "--provider",
            "acme",
            "--auth-kind",
            "api_key",
            "--billing-kind",
            "api",
            "--secret-store",
            "environment",
            "--secret-ref",
            "ACME_KEY",
            "--json",
        )

    def test_the_probe_command_verifies_through_an_authenticated_call(self) -> None:
        code, payload = run(*self.common, *self.overlay, "access", "probe", "--json")

        self.assertEqual(0, code)
        self.assertEqual(["ready"], [item["state"] for item in payload["items"]])
        self.assertEqual(1, len(self.provider.requests))
        self.assertEqual(
            "Bearer acme-environment-secret",
            self.provider.requests[0]["headers"]["authorization"],
        )
        self.assertNotIn("acme-environment-secret", json.dumps(payload))

    def test_a_rejected_credential_fails_the_command(self) -> None:
        self.provider.status = 401

        code, payload = run(*self.common, *self.overlay, "access", "probe", "--json")

        self.assertEqual(0, code)
        self.assertEqual(["expired"], [item["state"] for item in payload["items"]])

    def test_without_the_route_declaration_nothing_is_called(self) -> None:
        code, payload = run(*self.common, "access", "probe", "--json")

        self.assertEqual(0, code)
        self.assertEqual(["unknown"], [item["state"] for item in payload["items"]])
        self.assertEqual([], self.provider.requests)


class ShippedProbeDriverTests(unittest.TestCase):
    def test_the_package_registers_no_provider_probe_by_default(self) -> None:
        self.assertEqual({}, PROBE_DRIVERS)

    def test_the_module_embeds_no_provider_endpoint(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src" / "model_wiring" / "probe.py"
        ).read_text(encoding="utf-8")

        offenders = [line.strip() for line in source.splitlines() if "://" in line]

        self.assertEqual(
            [], offenders, "a provider endpoint is written into the module"
        )


if __name__ == "__main__":
    unittest.main()
