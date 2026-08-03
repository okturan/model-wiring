from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import fixture_catalog
from model_wiring import Catalog, MemorySecretStore, ProfileRegistry
from model_wiring.access import provider_access_routes
from model_wiring.errors import SecretNotFound
from model_wiring.login import (
    ChoicePrompt,
    LoginBroker,
    LoginResult,
    SecretPrompt,
)


def broker(**kwargs: object) -> tuple[LoginBroker, ProfileRegistry, MemorySecretStore]:
    registry = ProfileRegistry(Path(tempfile.mkdtemp()) / "profiles.sqlite3")
    store = MemorySecretStore()
    return (
        LoginBroker(
            fixture_catalog(),
            profiles=registry,
            stores={"memory": store},
            default_store="memory",
            **kwargs,
        ),
        registry,
        store,
    )


class ApiKeyLoginTests(unittest.TestCase):
    def test_beginning_an_api_key_login_asks_for_the_named_variable(self) -> None:
        login, _, _ = broker()

        session = login.begin("openai")

        self.assertIsInstance(session.prompt, SecretPrompt)
        self.assertEqual(("OPENAI_API_KEY",), tuple(session.prompt.field_names))

    def test_prompt_is_serializable_so_any_surface_can_render_it(self) -> None:
        login, _, _ = broker()

        payload = login.begin("openai").to_dict()

        json.dumps(payload)
        self.assertEqual("secret", payload["prompt"]["kind"])
        self.assertTrue(payload["prompt"]["fields"][0]["masked"])

    def test_supplying_a_key_stores_it_and_registers_a_profile(self) -> None:
        login, registry, store = broker()
        session = login.begin("openai")

        result = login.advance(session, {"OPENAI_API_KEY": "sk-not-a-real-key"})

        self.assertIsInstance(result, LoginResult)
        profile = registry.get(result.profile.id)
        self.assertEqual("openai", profile.provider_id)
        self.assertEqual("api_key", profile.auth_kind)
        self.assertEqual("api", profile.billing_kind)
        self.assertEqual(
            "sk-not-a-real-key", store.get(profile.secret_ref).reveal("api_key")
        )

    def test_the_stored_profile_never_records_the_secret_itself(self) -> None:
        login, registry, _ = broker()
        session = login.begin("openai")

        result = login.advance(session, {"OPENAI_API_KEY": "sk-not-a-real-key"})

        recorded = json.dumps(registry.get(result.profile.id).to_dict())
        self.assertNotIn("sk-not-a-real-key", recorded)

    def test_an_empty_key_is_rejected_before_anything_is_stored(self) -> None:
        login, registry, _ = broker()
        session = login.begin("openai")

        with self.assertRaises(ValueError):
            login.advance(session, {"OPENAI_API_KEY": "   "})

        self.assertEqual((), tuple(registry.list()))

    def test_the_session_prompt_points_at_documentation_that_issues_the_key(
        self,
    ) -> None:
        login, _, _ = broker()

        session = login.begin("openai")

        self.assertEqual(
            provider_access_routes(fixture_catalog().provider("openai"))[0].doc_url,
            session.prompt.doc_url,
        )


def bundle_broker() -> tuple[LoginBroker, ProfileRegistry, MemorySecretStore]:
    registry = ProfileRegistry(Path(tempfile.mkdtemp()) / "profiles.sqlite3")
    store = MemorySecretStore()
    catalog = Catalog.from_models_dev(
        {
            "cloudy": {
                "name": "Cloudy",
                "env": ["CLOUDY_KEY", "CLOUDY_SECRET"],
                "models": {"c-1": {"name": "C1"}},
            }
        },
        fetched_at="2026-08-02T00:00:00Z",
    )
    return (
        LoginBroker(
            catalog,
            profiles=registry,
            stores={"memory": store},
            default_store="memory",
        ),
        registry,
        store,
    )


class CredentialBundleLoginTests(unittest.TestCase):
    def test_bundle_login_asks_for_every_variable_at_once(self) -> None:
        login, _, _ = bundle_broker()

        session = login.begin("cloudy")

        self.assertEqual(("CLOUDY_KEY", "CLOUDY_SECRET"), session.prompt.field_names)

    def test_a_partial_bundle_is_refused(self) -> None:
        login, _, _ = bundle_broker()
        session = login.begin("cloudy")

        with self.assertRaises(ValueError):
            login.advance(session, {"CLOUDY_KEY": "value"})

    def test_a_complete_bundle_stores_every_value_under_its_own_name(self) -> None:
        login, registry, store = bundle_broker()
        session = login.begin("cloudy")

        result = login.advance(
            session, {"CLOUDY_KEY": "key-value", "CLOUDY_SECRET": "secret-value"}
        )

        material = store.get(registry.get(result.profile.id).secret_ref)
        self.assertEqual("key-value", material.reveal("CLOUDY_KEY"))
        self.assertEqual("secret-value", material.reveal("CLOUDY_SECRET"))


class EnvironmentPromotionTests(unittest.TestCase):
    def test_environment_value_can_be_promoted_into_durable_storage(self) -> None:
        login, registry, store = broker(environ={"OPENAI_API_KEY": "sk-from-env"})

        result = login.promote_environment("openai")

        profile = registry.get(result.profile.id)
        self.assertEqual("memory", profile.secret_store)
        self.assertEqual("sk-from-env", store.get(profile.secret_ref).reveal("api_key"))

    def test_promotion_records_where_the_credential_came_from(self) -> None:
        login, registry, _ = broker(environ={"OPENAI_API_KEY": "sk-from-env"})

        result = login.promote_environment("openai")

        self.assertEqual(
            "environment", registry.get(result.profile.id).metadata["imported_from"]
        )

    def test_promotion_without_the_variable_set_explains_the_problem(self) -> None:
        login, _, _ = broker(environ={})

        with self.assertRaises(ValueError) as caught:
            login.promote_environment("openai")

        self.assertIn("OPENAI_API_KEY", str(caught.exception))


class DelegatedImportTests(unittest.TestCase):
    def test_import_offers_only_candidates_that_actually_exist(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            present = Path(home) / "auth.json"
            present.write_text("{}", encoding="utf-8")
            login, _, _ = broker(
                delegated_candidates={
                    "openai": (
                        {"id": "codex-cli", "label": "Codex CLI", "path": str(present)},
                        {
                            "id": "ghost",
                            "label": "Missing tool",
                            "path": str(Path(home) / "absent.json"),
                        },
                    )
                }
            )

            session = login.begin("openai", route_id="delegated")

            self.assertIsInstance(session.prompt, ChoicePrompt)
            self.assertEqual(("codex-cli",), tuple(session.prompt.option_ids))

    def test_choosing_a_candidate_creates_a_delegated_profile_without_copying_tokens(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as home:
            present = Path(home) / "auth.json"
            present.write_text('{"tokens": {"access_token": "secret-value"}}', "utf-8")
            login, registry, _ = broker(
                delegated_candidates={
                    "openai": (
                        {"id": "codex-cli", "label": "Codex CLI", "path": str(present)},
                    )
                }
            )
            session = login.begin("openai", route_id="delegated")

            result = login.advance(session, {"choice": "codex-cli"})

            profile = registry.get(result.profile.id)
            self.assertEqual("delegated", profile.auth_kind)
            self.assertEqual("codex-cli", profile.metadata["delegate"])
            self.assertIsNone(profile.secret_ref)
            self.assertNotIn("secret-value", json.dumps(profile.to_dict()))


class LogoutTests(unittest.TestCase):
    def test_logout_removes_the_profile_and_its_secret(self) -> None:
        login, registry, store = broker()
        session = login.begin("openai")
        result = login.advance(session, {"OPENAI_API_KEY": "sk-not-a-real-key"})
        reference = result.profile.secret_ref

        login.logout(result.profile.id)

        self.assertEqual((), tuple(registry.list()))
        with self.assertRaises(SecretNotFound):
            store.get(reference)


if __name__ == "__main__":
    unittest.main()
