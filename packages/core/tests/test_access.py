from __future__ import annotations

import unittest

from helpers import fixture_catalog, fixture_raw
from model_wiring import Catalog, CredentialProfile
from model_wiring.access import (
    AccessRoute,
    provider_access,
    provider_access_routes,
)


def catalog_with(provider_id: str, raw: dict) -> Catalog:
    payload = fixture_raw()
    payload[provider_id] = raw
    return Catalog.from_models_dev(payload, fetched_at="2026-08-02T00:00:00Z")


class AccessRouteTests(unittest.TestCase):
    def test_single_variable_provider_offers_an_api_key_route_naming_the_variable(
        self,
    ) -> None:
        provider = fixture_catalog().provider("openai")

        routes = provider_access_routes(provider)

        self.assertEqual(1, len(routes))
        self.assertEqual("api_key", routes[0].kind)
        self.assertEqual(("OPENAI_API_KEY",), routes[0].env)
        self.assertEqual("api", routes[0].billing_kind)

    def test_single_variable_provider_is_not_described_as_a_credential_bundle(
        self,
    ) -> None:
        provider = fixture_catalog().provider("openai")

        kinds = {route.kind for route in provider_access_routes(provider)}

        self.assertNotIn("credential_bundle", kinds)

    def test_multi_variable_provider_offers_one_bundle_route_listing_every_variable(
        self,
    ) -> None:
        catalog = catalog_with(
            "cloudy",
            {
                "name": "Cloudy",
                "env": ["CLOUDY_ACCESS_KEY", "CLOUDY_SECRET", "CLOUDY_REGION"],
                "models": {"c-1": {"name": "C1"}},
            },
        )

        routes = provider_access_routes(catalog.provider("cloudy"))

        self.assertEqual(1, len(routes))
        self.assertEqual("credential_bundle", routes[0].kind)
        self.assertEqual(
            ("CLOUDY_ACCESS_KEY", "CLOUDY_SECRET", "CLOUDY_REGION"), routes[0].env
        )

    def test_provider_without_credentials_offers_an_anonymous_local_route(self) -> None:
        provider = fixture_catalog().provider("local")

        routes = provider_access_routes(provider)

        self.assertEqual(1, len(routes))
        self.assertEqual("anonymous", routes[0].kind)
        self.assertEqual("local", routes[0].billing_kind)

    def test_route_carries_the_documentation_url_that_issues_the_credential(
        self,
    ) -> None:
        provider = fixture_catalog().provider("openai")

        self.assertIsNotNone(provider_access_routes(provider)[0].doc_url)

    def test_an_auth_method_may_carry_the_instructions_a_derived_route_shows(
        self,
    ) -> None:
        catalog = fixture_catalog(
            overlays=(
                {
                    "providers": {
                        "openai": {
                            "auth_methods": [
                                {
                                    "kind": "delegated",
                                    "billing_kinds": ["subscription"],
                                    "instructions": "Reuses an existing sign-in.",
                                }
                            ]
                        }
                    }
                },
            )
        )

        route = provider_access_routes(catalog.provider("openai"))[0]

        self.assertEqual("Reuses an existing sign-in.", route.instructions)

    def test_routes_are_serializable_without_secrets(self) -> None:
        route = provider_access_routes(fixture_catalog().provider("openai"))[0]

        payload = route.to_dict()

        self.assertEqual("api_key", payload["kind"])
        self.assertEqual(["OPENAI_API_KEY"], payload["env"])
        self.assertNotIn("secret", str(payload).lower())


class ProviderAccessTests(unittest.TestCase):
    def test_provider_with_routes_but_no_profile_is_connectable(self) -> None:
        access = provider_access(fixture_catalog().provider("openai"))

        self.assertEqual("connectable", access.credential_state)
        self.assertTrue(access.routes)

    def test_provider_with_a_profile_is_configured(self) -> None:
        profile = CredentialProfile(
            id="openai-main",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="api-key:openai:main",
            secret_store="memory",
        )

        access = provider_access(fixture_catalog().provider("openai"), (profile,))

        self.assertEqual("configured", access.credential_state)

    def test_anonymous_provider_needs_no_credential_to_be_configured(self) -> None:
        access = provider_access(fixture_catalog().provider("local"))

        self.assertEqual("configured", access.credential_state)

    def test_provider_without_any_known_route_is_unavailable(self) -> None:
        catalog = catalog_with(
            "mystery",
            {"name": "Mystery", "models": {"m-1": {"name": "M1"}}},
        )
        provider = catalog.provider("mystery")
        stripped = type(provider)(
            id=provider.id,
            name=provider.name,
            models=provider.models,
            metadata={"access_routes": []},
        )

        access = provider_access(stripped)

        self.assertEqual("unavailable", access.credential_state)
        self.assertEqual((), access.routes)

    def test_access_names_the_variable_a_user_must_supply(self) -> None:
        access = provider_access(fixture_catalog().provider("openai"))

        self.assertIn("OPENAI_API_KEY", access.required_variables)


class CatalogAccessIntegrationTests(unittest.TestCase):
    def test_overlay_can_declare_routes_the_upstream_catalog_does_not_know(
        self,
    ) -> None:
        catalog = fixture_catalog(
            overlays=(
                {
                    "providers": {
                        "openai": {
                            "access_routes": [
                                {
                                    "id": "subscription",
                                    "kind": "oauth",
                                    "billing_kind": "subscription",
                                    "label": "ChatGPT subscription",
                                }
                            ]
                        }
                    }
                },
            )
        )

        routes = provider_access_routes(catalog.provider("openai"))

        self.assertEqual(("subscription",), tuple(route.id for route in routes))
        self.assertEqual("subscription", routes[0].billing_kind)

    def test_every_catalogued_provider_reports_an_explicit_access_answer(self) -> None:
        catalog = fixture_catalog()

        for provider in catalog.snapshot.providers.values():
            access = provider_access(provider)
            self.assertIn(
                access.credential_state,
                {"configured", "connectable", "unavailable"},
                provider.id,
            )
            for route in access.routes:
                self.assertIsInstance(route, AccessRoute)
                self.assertTrue(route.label, provider.id)


if __name__ == "__main__":
    unittest.main()
