from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import fixture_catalog, fixture_raw

from model_wiring import (
    Catalog,
    CredentialPool,
    CredentialProfile,
    ProfileRegistry,
    discover_environment_profiles,
)


class DiscoveryAndPoolTests(unittest.TestCase):
    def test_environment_discovery_checks_presence_without_copying_values(self) -> None:
        profiles = discover_environment_profiles(
            fixture_catalog(), environ={"OPENAI_API_KEY": "do-not-copy"}
        )

        self.assertEqual(["env:openai"], [profile.id for profile in profiles])
        profile = profiles[0]
        self.assertEqual("OPENAI_API_KEY", profile.secret_ref)
        self.assertEqual("environment", profile.secret_store)
        self.assertNotIn("do-not-copy", str(profile.to_dict()))

    def test_multi_variable_provider_requires_the_complete_bundle(self) -> None:
        raw = fixture_raw()
        raw["azure"] = {
            "id": "azure",
            "name": "Azure",
            "env": ["AZURE_RESOURCE_NAME", "AZURE_API_KEY"],
            "models": {},
        }
        catalog = Catalog.from_models_dev(raw)

        incomplete = discover_environment_profiles(
            catalog, environ={"AZURE_API_KEY": "secret"}
        )
        self.assertNotIn("env:azure", {profile.id for profile in incomplete})
        complete = discover_environment_profiles(
            catalog,
            environ={"AZURE_API_KEY": "secret", "AZURE_RESOURCE_NAME": "example"},
        )
        profile = next(profile for profile in complete if profile.id == "env:azure")
        self.assertEqual("credential_bundle", profile.auth_kind)
        self.assertEqual("AZURE_RESOURCE_NAME,AZURE_API_KEY", profile.secret_ref)

    def test_round_robin_pool_claims_atomically_and_records_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ProfileRegistry(Path(directory) / "profiles.sqlite3")
            for profile_id in ("a", "b"):
                registry.upsert(
                    CredentialProfile(
                        id=profile_id,
                        provider_id="openai",
                        auth_kind="api_key",
                        billing_kind="api",
                        secret_ref=profile_id.upper(),
                        secret_store="environment",
                    )
                )
            pool = CredentialPool("openai-api", ("a", "b"), "round_robin")

            claims = [pool.claim(registry).id for _ in range(3)]

            self.assertEqual(["a", "b", "a"], claims)
            self.assertEqual(2, registry.usage("a")["use_count"])
            self.assertEqual(1, registry.usage("b")["use_count"])

    def test_fill_first_and_least_used_are_distinct_explicit_policies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ProfileRegistry(Path(directory) / "profiles.sqlite3")
            for profile_id in ("a", "b"):
                registry.upsert(
                    CredentialProfile(
                        id=profile_id,
                        provider_id="openai",
                        auth_kind="api_key",
                        billing_kind="api",
                        secret_ref=profile_id.upper(),
                        secret_store="environment",
                    )
                )

            fill = CredentialPool("fill", ("a", "b"), "fill_first")
            least = CredentialPool("least", ("a", "b"), "least_used")

            self.assertEqual("a", fill.claim(registry).id)
            self.assertEqual("b", least.claim(registry).id)


if __name__ == "__main__":
    unittest.main()
