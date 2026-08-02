from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import fixture_catalog

from model_wiring import CredentialProfile, ProfileRegistry
from model_wiring.api import ProviderService


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


if __name__ == "__main__":
    unittest.main()
