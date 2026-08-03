from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from model_wiring import (
    AuthBroker,
    CredentialMaterial,
    CredentialProfile,
    MemorySecretStore,
    ProfileRegistry,
)
from model_wiring.probe import Prober, ProbeResult


def registry() -> ProfileRegistry:
    return ProfileRegistry(Path(tempfile.mkdtemp()) / "profiles.sqlite3")


def prober_with(
    *profiles: CredentialProfile, drivers: dict | None = None
) -> tuple[Prober, ProfileRegistry, MemorySecretStore]:
    store = MemorySecretStore()
    profiles_registry = registry()
    for profile in profiles:
        profiles_registry.upsert(profile)
    broker = AuthBroker(profiles_registry, stores={"memory": store})
    return Prober(broker, drivers=drivers or {}), profiles_registry, store


def api_key_profile(provider: str = "openai") -> CredentialProfile:
    return CredentialProfile(
        id=f"{provider}-api_key",
        provider_id=provider,
        auth_kind="api_key",
        billing_kind="api",
        secret_ref=f"{provider}:api_key",
        secret_store="memory",
    )


def oauth_profile() -> CredentialProfile:
    return CredentialProfile(
        id="acme-subscription",
        provider_id="acme",
        auth_kind="oauth",
        billing_kind="subscription",
        secret_ref="acme:subscription",
        secret_store="memory",
    )


class LocalEvidenceTests(unittest.TestCase):
    def test_a_provider_needing_no_credential_is_ready_and_local(self) -> None:
        profile = CredentialProfile(
            id="ollama-local",
            provider_id="ollama",
            auth_kind="anonymous",
            billing_kind="local",
        )
        prober, _, _ = prober_with(profile)

        result = prober.probe("ollama-local")

        self.assertEqual("ready", result.state)
        self.assertEqual("local", result.entitlement_class)

    def test_an_expired_token_is_reported_without_calling_the_provider(self) -> None:
        prober, _, store = prober_with(oauth_profile())
        store.put(
            "acme:subscription",
            CredentialMaterial(
                {"access_token": "stale"}, expires_at=time.time() - 3600
            ),
        )

        result = prober.probe("acme-subscription")

        self.assertEqual("expired", result.state)
        self.assertEqual("subscription", result.entitlement_class)

    def test_a_missing_secret_is_unavailable_rather_than_an_exception(self) -> None:
        prober, _, _ = prober_with(api_key_profile())

        result = prober.probe("openai-api_key")

        self.assertEqual("unavailable", result.state)
        self.assertIn("secret", (result.detail or "").lower())

    def test_a_disabled_profile_is_unavailable(self) -> None:
        profile = CredentialProfile(
            id="openai-api_key",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="openai:api_key",
            secret_store="memory",
            enabled=False,
        )
        prober, _, store = prober_with(profile)
        store.put("openai:api_key", CredentialMaterial({"api_key": "sk-x"}))

        result = prober.probe("openai-api_key")

        self.assertEqual("unavailable", result.state)

    def test_a_delegated_sign_in_whose_artifact_is_gone_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            profile = CredentialProfile(
                id="openai-codex-codex-cli",
                provider_id="openai-codex",
                auth_kind="delegated",
                billing_kind="subscription",
                metadata={
                    "delegate": "codex-cli",
                    "delegate_path": str(Path(home) / "gone.json"),
                },
            )
            prober, _, _ = prober_with(profile)

            result = prober.probe("openai-codex-codex-cli")

            self.assertEqual("unavailable", result.state)

    def test_a_delegated_sign_in_whose_artifact_exists_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            artifact = Path(home) / "auth.json"
            artifact.write_text("{}", encoding="utf-8")
            profile = CredentialProfile(
                id="openai-codex-codex-cli",
                provider_id="openai-codex",
                auth_kind="delegated",
                billing_kind="subscription",
                metadata={"delegate": "codex-cli", "delegate_path": str(artifact)},
            )
            prober, _, _ = prober_with(profile)

            result = prober.probe("openai-codex-codex-cli")

            self.assertEqual("ready", result.state)
            self.assertEqual("subscription", result.entitlement_class)

    def test_a_stored_credential_with_no_driver_is_unknown_not_ready(self) -> None:
        """Claiming a credential works without checking would be a false promise."""

        prober, _, store = prober_with(api_key_profile())
        store.put("openai:api_key", CredentialMaterial({"api_key": "sk-x"}))

        result = prober.probe("openai-api_key")

        self.assertEqual("unknown", result.state)


class DriverTests(unittest.TestCase):
    def test_a_driver_can_confirm_the_credential_works(self) -> None:
        def driver(lease):
            return "ready", "acct_public_1234", None

        prober, _, store = prober_with(api_key_profile(), drivers={"openai": driver})
        store.put("openai:api_key", CredentialMaterial({"api_key": "sk-x"}))

        result = prober.probe("openai-api_key")

        self.assertEqual("ready", result.state)
        self.assertEqual("acct_public_1234", result.account_fingerprint)
        self.assertEqual("usage_api", result.entitlement_class)

    def test_an_authenticated_but_unentitled_account_is_policy_denied(self) -> None:
        def driver(lease):
            return "policy_denied", None, "no Copilot subscription on this account"

        prober, _, store = prober_with(
            api_key_profile("github-copilot"), drivers={"github-copilot": driver}
        )
        store.put("github-copilot:api_key", CredentialMaterial({"api_key": "gh"}))

        result = prober.probe("github-copilot-api_key")

        self.assertEqual("policy_denied", result.state)
        self.assertIn("Copilot", result.detail or "")

    def test_a_driver_failure_is_unavailable_rather_than_crashing_the_caller(
        self,
    ) -> None:
        def driver(lease):
            raise OSError("network unreachable")

        prober, _, store = prober_with(api_key_profile(), drivers={"openai": driver})
        store.put("openai:api_key", CredentialMaterial({"api_key": "sk-x"}))

        result = prober.probe("openai-api_key")

        self.assertEqual("unavailable", result.state)
        self.assertIn("network unreachable", result.detail or "")

    def test_the_driver_sees_a_lease_and_the_secret_never_reaches_the_result(
        self,
    ) -> None:
        seen: list[str] = []

        def driver(lease):
            seen.append(lease.reveal("api_key"))
            return "ready", None, None

        prober, _, store = prober_with(api_key_profile(), drivers={"openai": driver})
        store.put("openai:api_key", CredentialMaterial({"api_key": "sk-secret-value"}))

        result = prober.probe("openai-api_key")

        self.assertEqual(["sk-secret-value"], seen)
        self.assertNotIn("sk-secret-value", json.dumps(result.to_dict()))


class RecordingTests(unittest.TestCase):
    def test_the_outcome_is_recorded_as_non_secret_profile_metadata(self) -> None:
        prober, profiles, store = prober_with(oauth_profile())
        store.put(
            "acme:subscription",
            CredentialMaterial({"access_token": "x"}, expires_at=time.time() - 1),
        )

        prober.probe("acme-subscription")

        recorded = profiles.get("acme-subscription").metadata
        self.assertEqual("expired", recorded["last_probe_state"])
        self.assertIsInstance(recorded["last_probe_at"], float)

    def test_recording_can_be_skipped_for_a_read_only_check(self) -> None:
        prober, profiles, _ = prober_with(api_key_profile())

        prober.probe("openai-api_key", record=False)

        self.assertNotIn("last_probe_state", profiles.get("openai-api_key").metadata)

    def test_probing_every_profile_returns_one_result_each(self) -> None:
        prober, _, _ = prober_with(api_key_profile(), oauth_profile())

        results = prober.probe_all()

        self.assertEqual(2, len(results))
        self.assertTrue(all(isinstance(item, ProbeResult) for item in results))

    def test_an_unknown_profile_is_reported_rather_than_raising(self) -> None:
        prober, _, _ = prober_with()

        result = prober.probe("nope")

        self.assertEqual("unavailable", result.state)
        self.assertIn("nope", result.detail or "")


if __name__ == "__main__":
    unittest.main()
