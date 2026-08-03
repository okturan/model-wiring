from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from helpers import fixture_catalog
from model_wiring import (
    AuthBroker,
    CredentialMaterial,
    CredentialProfile,
    MemorySecretStore,
    ProfileRegistry,
)
from model_wiring.broker import CredentialBroker, RuntimeCredential
from model_wiring.errors import CredentialError, CredentialUnavailable, RefreshError


class CountingRefreshDriver:
    """Stands in for the one network call a refresh is allowed to make."""

    def __init__(self, *, fails: bool = False) -> None:
        self.calls = 0
        self.fails = fails
        self.seen_refresh_tokens: list[str] = []
        self._lock = threading.Lock()

    def refresh(
        self, profile: CredentialProfile, material: CredentialMaterial
    ) -> CredentialMaterial:
        with self._lock:
            self.calls += 1
            self.seen_refresh_tokens.append(material.reveal("refresh_token"))
        if self.fails:
            raise RuntimeError("provider rejected the refresh token")
        return CredentialMaterial(
            {"access_token": "fresh-token", "refresh_token": "rotated-token"},
            expires_at=time.time() + 3600,
        )


class BlockingRefreshDriver(CountingRefreshDriver):
    """Holds the refresh open so a second entrant would be observable."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def refresh(
        self, profile: CredentialProfile, material: CredentialMaterial
    ) -> CredentialMaterial:
        with self._lock:
            self.calls += 1
            self.seen_refresh_tokens.append(material.reveal("refresh_token"))
        self.entered.set()
        if not self.release.wait(timeout=30):
            raise RuntimeError("the test never released the refresh driver")
        return CredentialMaterial(
            {"access_token": "fresh-token", "refresh_token": "rotated-token"},
            expires_at=time.time() + 3600,
        )


class ObservableRegistry(ProfileRegistry):
    """Reports when a caller reaches the cross-process mutual-exclusion point."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.reached_lease = threading.Event()

    @contextmanager
    def refresh_lease(self, profile_id: str, **kwargs: float) -> Iterator[None]:
        self.reached_lease.set()
        with super().refresh_lease(profile_id, **kwargs):
            yield


class CountingStore(MemorySecretStore):
    """Proves whether resolution touched secret material at all."""

    name = "counting"

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get(self, reference: str) -> CredentialMaterial:
        self.reads += 1
        return super().get(reference)

    def references(self) -> tuple[str, ...]:
        return tuple(sorted(self._values))


def oauth_profile(profile_id: str, *, provider: str = "provider") -> CredentialProfile:
    return CredentialProfile(
        id=profile_id,
        provider_id=provider,
        auth_kind="oauth",
        billing_kind="subscription",
        secret_ref=profile_id,
        secret_store="memory",
    )


class BrokerRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "profiles.sqlite3"
        self.profiles = ProfileRegistry(self.database)
        self.store = MemorySecretStore()
        self.driver = CountingRefreshDriver()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def seed(self, profile: CredentialProfile, *, expires_in: float) -> None:
        material = CredentialMaterial(
            {"access_token": "stale-token", "refresh_token": "refresh-old"},
            expires_at=time.time() + expires_in,
        )
        self.store.put(profile.secret_ref or profile.id, material)
        material.wipe()
        self.profiles.upsert(profile)

    def broker(self, **kwargs: object) -> CredentialBroker:
        return self.shared_broker(self.profiles, self.driver, **kwargs)

    def shared_broker(
        self,
        registry: ProfileRegistry,
        driver: CountingRefreshDriver,
        **kwargs: object,
    ) -> CredentialBroker:
        auth = AuthBroker(
            registry,
            stores={"memory": self.store},
            refresh_drivers={"provider": driver},
        )
        kwargs.setdefault("refresh_skew_seconds", 300)
        return CredentialBroker(auth, **kwargs)  # type: ignore[arg-type]

    def test_credential_inside_the_skew_refreshes_and_stores_the_result(self) -> None:
        self.seed(oauth_profile("oauth"), expires_in=120)
        broker = self.broker(refresh_skew_seconds=300)

        self.assertTrue(broker.refresh("oauth"))

        self.assertEqual(1, self.driver.calls)
        self.assertEqual(["refresh-old"], self.driver.seen_refresh_tokens)
        stored = self.store.get("oauth")
        self.assertEqual("fresh-token", stored.reveal("access_token"))
        self.assertEqual("rotated-token", stored.reveal("refresh_token"))
        stored.wipe()

    def test_credential_beyond_the_skew_issues_no_token_request(self) -> None:
        self.seed(oauth_profile("oauth"), expires_in=3600)
        broker = self.broker(refresh_skew_seconds=300)

        self.assertFalse(broker.refresh("oauth"))

        self.assertEqual(0, self.driver.calls)
        stored = self.store.get("oauth")
        self.assertEqual("stale-token", stored.reveal("access_token"))
        stored.wipe()

    def test_concurrent_brokers_issue_exactly_one_token_request(self) -> None:
        self.seed(oauth_profile("oauth"), expires_in=60)
        driver = BlockingRefreshDriver()
        # Two registries over one path stand in for two processes; the secret
        # store is shared because a real keyring is shared the same way.
        holder = self.shared_broker(ProfileRegistry(self.database), driver)
        waiting_registry = ObservableRegistry(self.database)
        waiter = self.shared_broker(waiting_registry, driver)
        wrote: dict[str, bool] = {}
        observed: dict[str, str] = {}
        failures: list[BaseException] = []

        def run(name: str, broker: CredentialBroker) -> None:
            try:
                wrote[name] = broker.refresh("oauth")
                with broker.auth.lease("oauth") as lease:
                    observed[name] = lease.reveal("access_token")
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(exc)

        first = threading.Thread(target=run, args=("holder", holder))
        first.start()
        # Entering the driver proves the holder owns the refresh lease.
        self.assertTrue(driver.entered.wait(timeout=30), "no broker refreshed")
        second = threading.Thread(target=run, args=("waiter", waiter))
        second.start()
        self.assertTrue(
            waiting_registry.reached_lease.wait(timeout=30),
            "the second broker refreshed without taking the refresh lease",
        )
        driver.release.set()
        first.join(timeout=30)
        second.join(timeout=30)

        self.assertEqual([], failures)
        self.assertEqual(1, driver.calls)
        self.assertEqual(["refresh-old"], driver.seen_refresh_tokens)
        self.assertEqual({"holder": True, "waiter": False}, wrote)
        self.assertEqual({"holder": "fresh-token", "waiter": "fresh-token"}, observed)

    def test_failed_refresh_keeps_prior_material_and_releases_the_lease(self) -> None:
        self.driver.fails = True
        self.seed(oauth_profile("oauth"), expires_in=60)
        broker = self.broker(refresh_skew_seconds=300)

        with self.assertRaises(RefreshError) as caught:
            broker.refresh("oauth")

        self.assertNotIn("refresh-old", str(caught.exception))
        stored = self.store.get("oauth")
        self.assertEqual("stale-token", stored.reveal("access_token"))
        self.assertEqual("refresh-old", stored.reveal("refresh_token"))
        stored.wipe()
        with self.profiles.refresh_lease("oauth", wait_seconds=0.2):
            pass

    def test_disabled_profile_is_neither_leased_nor_refreshed(self) -> None:
        self.seed(oauth_profile("oauth"), expires_in=60)
        broker = self.broker(refresh_skew_seconds=300)

        disabled = broker.disable("oauth")

        self.assertFalse(disabled.enabled)
        self.assertFalse(self.profiles.get("oauth").enabled)
        with self.assertRaises(CredentialError):
            broker.refresh("oauth")
        with self.assertRaises(CredentialError), broker.auth.lease("oauth"):
            pass
        self.assertEqual((), broker.refresh_due())
        self.assertEqual(0, self.driver.calls)

    def test_reenabled_profile_is_eligible_again_with_material_intact(self) -> None:
        self.seed(oauth_profile("oauth"), expires_in=3600)
        broker = self.broker(refresh_skew_seconds=300)
        broker.disable("oauth")

        enabled = broker.enable("oauth")

        self.assertTrue(enabled.enabled)
        with broker.auth.lease("oauth") as lease:
            self.assertEqual("stale-token", lease.reveal("access_token"))
        self.assertEqual(0, self.driver.calls)

    def test_refresh_due_writes_only_the_profiles_inside_the_skew(self) -> None:
        self.seed(oauth_profile("due"), expires_in=60)
        self.seed(oauth_profile("distant"), expires_in=3600)
        broker = self.broker(refresh_skew_seconds=300)

        self.assertEqual(("due",), broker.refresh_due())
        self.assertEqual(1, self.driver.calls)

    def test_snapshot_carries_identity_and_no_secret_value(self) -> None:
        profile = oauth_profile("oauth")
        self.seed(profile, expires_in=3600)
        self.profiles.upsert(
            CredentialProfile(
                id=profile.id,
                provider_id=profile.provider_id,
                auth_kind=profile.auth_kind,
                billing_kind=profile.billing_kind,
                secret_ref=profile.secret_ref,
                secret_store=profile.secret_store,
                metadata={"last_probe_state": "ready", "last_probe_at": 1.5},
            )
        )
        broker = self.broker()

        snapshots = broker.snapshot()
        payload = json.dumps([item.to_dict() for item in snapshots])

        self.assertEqual(1, len(snapshots))
        self.assertEqual("oauth", snapshots[0].profile_id)
        self.assertEqual("provider", snapshots[0].provider_id)
        self.assertEqual("oauth", snapshots[0].auth_kind)
        self.assertEqual("subscription", snapshots[0].billing_kind)
        self.assertTrue(snapshots[0].enabled)
        self.assertIsNotNone(snapshots[0].expires_at)
        self.assertEqual("ready", snapshots[0].last_probe_state)
        self.assertNotIn("stale-token", payload)
        self.assertNotIn("refresh-old", payload)
        self.assertNotIn("access_token", payload)


class CredentialPrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "profiles.sqlite3"
        self.profiles = ProfileRegistry(self.database)
        self.store = CountingStore()
        self.driver = CountingRefreshDriver()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def broker(self, **kwargs: object) -> CredentialBroker:
        auth = AuthBroker(
            self.profiles,
            stores={"memory": self.store},
            refresh_drivers={"acme": self.driver},
        )
        return CredentialBroker(auth, **kwargs)  # type: ignore[arg-type]

    def seed(
        self,
        profile_id: str,
        *,
        priority: int = 100,
        billing_kind: str = "api",
        secret: str = "stored-secret",
        enabled: bool = True,
    ) -> CredentialProfile:
        material = CredentialMaterial({"api_key": secret})
        self.store.put(profile_id, material)
        material.wipe()
        profile = CredentialProfile(
            id=profile_id,
            provider_id="acme",
            auth_kind="api_key",
            billing_kind=billing_kind,
            secret_ref=profile_id,
            secret_store="memory",
            priority=priority,
            enabled=enabled,
        )
        self.profiles.upsert(profile)
        return profile

    def test_runtime_credential_outranks_stored_and_is_never_persisted(self) -> None:
        self.seed("stored")
        broker = self.broker()
        runtime = RuntimeCredential("acme", {"api_key": "runtime-secret"})

        resolution = broker.resolve("acme", runtime=runtime)
        with broker.lease("acme", runtime=runtime) as lease:
            revealed = lease.reveal()

        self.assertEqual("runtime", resolution.source)
        self.assertEqual("runtime-secret", revealed)
        self.assertNotIn("runtime-secret", repr(runtime))
        self.assertNotIn("runtime-secret", json.dumps(resolution.to_dict()))
        self.assertEqual(["stored"], [item.id for item in self.profiles.list()])
        self.assertEqual(("stored",), self.store.references())
        stored = self.store.get("stored")
        self.assertEqual("stored-secret", stored.reveal())
        stored.wipe()

    def test_runtime_credential_for_another_provider_is_refused(self) -> None:
        broker = self.broker()
        runtime = RuntimeCredential("other", {"api_key": "runtime-secret"})

        with self.assertRaisesRegex(CredentialError, "other"):
            broker.resolve("acme", runtime=runtime)

    def test_selected_profile_outranks_a_higher_priority_stored_profile(self) -> None:
        self.seed("cheap", priority=10)
        self.seed("chosen", priority=90)
        broker = self.broker()

        self.assertEqual("cheap", broker.resolve("acme").profile.id)
        selected = broker.resolve("acme", profile_id="chosen")

        self.assertEqual("selected", selected.source)
        self.assertEqual("chosen", selected.profile.id)
        with broker.lease("acme", profile_id="chosen") as lease:
            self.assertEqual("stored-secret", lease.reveal())

    def test_selecting_a_profile_from_another_provider_is_refused(self) -> None:
        self.profiles.upsert(
            CredentialProfile(
                id="elsewhere",
                provider_id="openai",
                auth_kind="api_key",
                billing_kind="api",
                secret_ref="elsewhere",
                secret_store="memory",
            )
        )
        broker = self.broker()

        with self.assertRaisesRegex(CredentialError, "openai"):
            broker.resolve("acme", profile_id="elsewhere")

    def test_highest_priority_enabled_stored_profile_wins(self) -> None:
        self.seed("cheap", priority=10)
        self.seed("chosen", priority=90)
        broker = self.broker()

        resolution = broker.resolve("acme")

        self.assertEqual("stored", resolution.source)
        self.assertEqual("cheap", resolution.profile.id)

    def test_disabled_profile_is_skipped_by_resolution(self) -> None:
        self.seed("cheap", priority=10)
        self.seed("chosen", priority=90)
        broker = self.broker()

        broker.disable("cheap")
        resolution = broker.resolve("acme")

        self.assertEqual("chosen", resolution.profile.id)
        self.assertIn("cheap", " ".join(resolution.considered))

    def test_selecting_a_disabled_profile_fails_rather_than_substituting(self) -> None:
        self.seed("cheap", priority=10)
        self.seed("chosen", priority=90)
        broker = self.broker()
        broker.disable("chosen")

        with self.assertRaisesRegex(CredentialError, "disabled"):
            broker.resolve("acme", profile_id="chosen")

    def test_environment_profile_is_the_last_rung(self) -> None:
        with patch.dict(os.environ, {"ACME_KEY": "env-secret"}):
            broker = self.broker(catalog=fixture_catalog())

            resolution = broker.resolve("acme")
            with broker.lease("acme") as lease:
                revealed = lease.reveal()

        self.assertEqual("environment", resolution.source)
        self.assertEqual("env:acme", resolution.profile.id)
        self.assertEqual("env-secret", revealed)
        self.assertEqual((), self.profiles.list())

    def test_stored_profile_outranks_a_discovered_environment_profile(self) -> None:
        self.seed("stored", priority=900)
        with patch.dict(os.environ, {"ACME_KEY": "env-secret"}):
            broker = self.broker(catalog=fixture_catalog())

            resolution = broker.resolve("acme")

        self.assertEqual("stored", resolution.source)
        self.assertEqual("stored", resolution.profile.id)

    def test_requested_billing_kind_never_falls_back_to_another(self) -> None:
        self.seed("metered", billing_kind="api")
        with patch.dict(os.environ, {"ACME_KEY": "env-secret"}):
            broker = self.broker(catalog=fixture_catalog())

            with self.assertRaises(CredentialUnavailable) as caught:
                broker.resolve("acme", billing_kind="subscription")

        message = str(caught.exception)
        self.assertIn("subscription", message)
        self.assertIn("metered", message)
        self.assertEqual(0, self.driver.calls)

    def test_explicit_selection_may_cross_billing_kinds(self) -> None:
        self.seed("metered", billing_kind="api")
        broker = self.broker()

        resolution = broker.resolve(
            "acme", profile_id="metered", billing_kind="subscription"
        )

        self.assertEqual("selected", resolution.source)
        self.assertEqual("api", resolution.profile.billing_kind)

    def test_exhausted_chain_fails_naming_candidates_before_any_call(self) -> None:
        broker = self.broker(catalog=fixture_catalog(), environ={})

        with self.assertRaises(CredentialUnavailable) as caught:
            broker.resolve("acme")

        considered = caught.exception.considered
        self.assertIn("acme", str(caught.exception))
        self.assertTrue(any("runtime" in item for item in considered), considered)
        self.assertTrue(any("stored" in item for item in considered), considered)
        self.assertTrue(any("environment" in item for item in considered), considered)
        self.assertEqual(0, self.store.reads)
        self.assertEqual(0, self.driver.calls)

    def test_resolution_serializes_without_secret_material(self) -> None:
        self.seed("stored", secret="stored-secret")
        broker = self.broker()

        payload = json.dumps(broker.resolve("acme").to_dict())

        self.assertNotIn("stored-secret", payload)
        self.assertIn("stored", payload)

    def test_lease_refreshes_ahead_of_expiry_before_yielding(self) -> None:
        material = CredentialMaterial(
            {"access_token": "stale-token", "refresh_token": "refresh-old"},
            expires_at=time.time() + 60,
        )
        self.store.put("oauth", material)
        material.wipe()
        self.profiles.upsert(
            CredentialProfile(
                id="oauth",
                provider_id="acme",
                auth_kind="oauth",
                billing_kind="subscription",
                secret_ref="oauth",
                secret_store="memory",
            )
        )
        broker = self.broker(refresh_skew_seconds=300)

        with broker.lease("acme") as lease:
            self.assertEqual("fresh-token", lease.reveal("access_token"))

        self.assertEqual(1, self.driver.calls)


if __name__ == "__main__":
    unittest.main()
