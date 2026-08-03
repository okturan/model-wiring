from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import POSIX_PERMISSIONS
from model_wiring import (
    AuthBroker,
    CredentialMaterial,
    CredentialProfile,
    EnvironmentSecretStore,
    MemorySecretStore,
    ProfileRegistry,
)
from model_wiring.errors import CredentialError, RefreshError, SecretNotFound


class FakeRefreshDriver:
    def __init__(self) -> None:
        self.calls = 0

    def refresh(
        self, profile: CredentialProfile, material: CredentialMaterial
    ) -> CredentialMaterial:
        self.calls += 1
        self.last_profile = profile.id
        self.last_refresh_token = material.reveal("refresh_token")
        return CredentialMaterial(
            {"access_token": "fresh-token", "refresh_token": "rotated-token"},
            expires_at=time.time() + 3600,
        )


class FailingWriteStore(MemorySecretStore):
    name = "failing"

    def put(self, reference: str, material: CredentialMaterial) -> None:
        if hasattr(self, "seeded"):
            self.failed_material = material
            raise CredentialError("simulated keyring write failure")
        super().put(reference, material)
        self.seeded = True


class ProfileAndAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "profiles.sqlite3"
        self.profiles = ProfileRegistry(self.database)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_profile_registry_is_private_and_orders_by_priority(self) -> None:
        second = CredentialProfile(
            id="second",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="SECOND",
            secret_store="environment",
            priority=20,
        )
        first = CredentialProfile(
            id="first",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="FIRST",
            secret_store="environment",
            priority=10,
        )
        self.profiles.upsert(second)
        self.profiles.upsert(first)

        if POSIX_PERMISSIONS:
            self.assertEqual(0o600, os.stat(self.database).st_mode & 0o777)
        self.assertEqual(
            ["first", "second"], [item.id for item in self.profiles.list()]
        )
        self.assertEqual("FIRST", self.profiles.get("first").secret_ref)
        self.assertTrue(self.profiles.delete("second"))
        self.assertFalse(self.profiles.delete("second"))

    def test_material_and_lease_representations_are_redacted_and_wiped(self) -> None:
        store = MemorySecretStore()
        original = CredentialMaterial({"api_key": "top-secret"})
        store.put("key", original)
        original.wipe()
        profile = CredentialProfile(
            id="api",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="key",
            secret_store="memory",
        )
        self.profiles.upsert(profile)
        broker = AuthBroker(self.profiles, stores={"memory": store})

        with broker.lease("api") as lease:
            self.assertEqual("top-secret", lease.reveal())
            self.assertNotIn("top-secret", repr(lease))
            material = lease.material
        self.assertIsNotNone(material)
        with self.assertRaises(CredentialError):
            material.reveal()  # type: ignore[union-attr]

    def test_environment_store_is_read_only(self) -> None:
        store = EnvironmentSecretStore()
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "env-secret"}):
            material = store.get("TEST_PROVIDER_KEY")
            self.assertEqual("env-secret", material.reveal())
            material.wipe()
        with self.assertRaises(SecretNotFound):
            store.get("TEST_PROVIDER_KEY_DOES_NOT_EXIST")
        with self.assertRaises(CredentialError):
            store.put("TEST_PROVIDER_KEY", CredentialMaterial({"api_key": "x"}))

    def test_environment_store_materializes_complete_multi_variable_bundle(
        self,
    ) -> None:
        store = EnvironmentSecretStore()
        with patch.dict(
            os.environ,
            {
                "AZURE_API_KEY": "super-secret-value",
                "AZURE_RESOURCE_NAME": "resource",
            },
        ):
            material = store.get("AZURE_API_KEY,AZURE_RESOURCE_NAME")
            self.assertEqual("super-secret-value", material.reveal("AZURE_API_KEY"))
            self.assertEqual("resource", material.reveal("AZURE_RESOURCE_NAME"))
            self.assertNotIn("super-secret-value", repr(material))
            material.wipe()
        with self.assertRaises(CredentialError):
            store.get("GOOD,bad-reference!")

    def test_delegated_profile_never_materializes_a_token(self) -> None:
        profile = CredentialProfile(
            id="codex",
            provider_id="openai-codex",
            auth_kind="delegated",
            billing_kind="subscription",
            metadata={"delegate": "codex-sdk"},
        )
        self.profiles.upsert(profile)
        broker = AuthBroker(self.profiles)

        with broker.lease("codex") as lease:
            self.assertEqual("codex-sdk", lease.delegate)
            self.assertIsNone(lease.material)
            with self.assertRaises(CredentialError):
                lease.reveal()

    def test_expired_oauth_material_refreshes_and_rotates_atomically(self) -> None:
        store = MemorySecretStore()
        expired = CredentialMaterial(
            {"access_token": "old", "refresh_token": "refresh-old"},
            expires_at=time.time() - 10,
        )
        store.put("oauth", expired)
        expired.wipe()
        profile = CredentialProfile(
            id="oauth",
            provider_id="provider",
            auth_kind="oauth",
            billing_kind="subscription",
            secret_ref="oauth",
            secret_store="memory",
        )
        self.profiles.upsert(profile)
        driver = FakeRefreshDriver()
        broker = AuthBroker(
            self.profiles,
            stores={"memory": store},
            refresh_drivers={"provider": driver},
        )

        with broker.lease("oauth") as lease:
            self.assertEqual("fresh-token", lease.reveal("access_token"))
        with broker.lease("oauth") as lease:
            self.assertEqual("fresh-token", lease.reveal("access_token"))

        self.assertEqual(1, driver.calls)
        self.assertEqual("refresh-old", driver.last_refresh_token)
        persisted = store.get("oauth")
        self.assertEqual("rotated-token", persisted.reveal("refresh_token"))
        persisted.wipe()

    def test_refresh_lease_times_out_instead_of_double_writing(self) -> None:
        with (
            self.profiles.refresh_lease("oauth", ttl_seconds=1, wait_seconds=0.2),
            self.assertRaises(RefreshError),
            self.profiles.refresh_lease("oauth", ttl_seconds=1, wait_seconds=0.1),
        ):
            pass

    def test_failed_rotated_token_write_still_wipes_temporary_material(self) -> None:
        store = FailingWriteStore()
        expired = CredentialMaterial(
            {"access_token": "old", "refresh_token": "refresh-old"},
            expires_at=time.time() - 10,
        )
        store.put("oauth", expired)
        expired.wipe()
        self.profiles.upsert(
            CredentialProfile(
                id="oauth-fail",
                provider_id="provider",
                auth_kind="oauth",
                billing_kind="subscription",
                secret_ref="oauth",
                secret_store="failing",
            )
        )
        broker = AuthBroker(
            self.profiles,
            stores={"failing": store},
            refresh_drivers={"provider": FakeRefreshDriver()},
        )

        with (
            self.assertRaisesRegex(CredentialError, "simulated"),
            broker.lease("oauth-fail"),
        ):
            pass
        with self.assertRaises(CredentialError):
            store.failed_material.reveal()


if __name__ == "__main__":
    unittest.main()
