from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import fixture_catalog

from model_provider import CredentialProfile, ProfileRegistry
from model_provider.api import ProviderService


class ProviderServiceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
