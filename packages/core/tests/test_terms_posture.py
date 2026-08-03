from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from helpers import fixture_catalog
from model_wiring import Catalog, MemorySecretStore, ProfileRegistry
from model_wiring.access import AccessRoute, provider_access_routes
from model_wiring.login import LoginBroker, permitted_drivers

OAUTH_CONFIG = {
    "client_id": "application-supplied-client",
    "authorization_endpoint": "https://acme.example/authorize",
    "token_endpoint": "https://acme.example/token",
    "device_authorization_endpoint": "https://acme.example/device",
}


def oauth_provider(posture: str | None, *, driver: str = "oauth_pkce") -> dict:
    """A provider whose single route is OAuth, declaring ``posture`` or nothing."""

    route: dict[str, Any] = {
        "id": "subscription",
        "kind": "oauth",
        "billing_kind": "subscription",
        "label": "Acme subscription",
        "driver": driver,
        "metadata": {"oauth": dict(OAUTH_CONFIG)},
    }
    if posture is not None:
        route["terms_posture"] = posture
    return {
        "acme-cloud": {
            "name": "Acme Cloud",
            "models": {"a-1": {"name": "A1"}},
            "access_routes": [route],
        }
    }


def refusing_transport():
    """Any provider contact is a failure of the gate under test."""

    def transport(url, parameters, headers, timeout):
        raise AssertionError(f"the provider was contacted at {url}")

    return transport


def broker_for(raw: dict, transport=None) -> LoginBroker:
    catalog = Catalog.from_models_dev(
        raw, fetched_at="2026-08-02T00:00:00Z", include_default_overlays=False
    )
    return LoginBroker(
        catalog,
        profiles=ProfileRegistry(Path(tempfile.mkdtemp()) / "profiles.sqlite3"),
        stores={"memory": MemorySecretStore()},
        default_store="memory",
        oauth_transport=transport or refusing_transport(),
    )


class TermsPostureDeclarationTests(unittest.TestCase):
    def test_a_derived_route_that_declares_nothing_reports_unverified(self) -> None:
        route = provider_access_routes(fixture_catalog().provider("openai"))[0]

        self.assertEqual("unverified", route.terms_posture)

    def test_a_declared_route_that_omits_the_field_reports_unverified(self) -> None:
        route = AccessRoute.from_dict(
            {"id": "subscription", "kind": "oauth", "billing_kind": "subscription"}
        )

        self.assertEqual("unverified", route.terms_posture)

    def test_an_overlay_declared_posture_is_reported_in_place_of_the_default(
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
                                    "terms_posture": "third_party_permitted",
                                }
                            ]
                        }
                    }
                },
            )
        )

        route = provider_access_routes(catalog.provider("openai"))[0]

        self.assertEqual("third_party_permitted", route.terms_posture)

    def test_an_auth_method_may_declare_the_posture_a_derived_route_carries(
        self,
    ) -> None:
        catalog = fixture_catalog(
            overlays=(
                {
                    "providers": {
                        "openai": {
                            "auth_methods": [
                                {
                                    "kind": "oauth",
                                    "billing_kinds": ["subscription"],
                                    "terms_posture": "first_party_only",
                                }
                            ]
                        }
                    }
                },
            )
        )

        route = provider_access_routes(catalog.provider("openai"))[0]

        self.assertEqual("first_party_only", route.terms_posture)

    def test_the_posture_survives_a_to_dict_from_dict_round_trip(self) -> None:
        route = AccessRoute(
            id="subscription",
            kind="oauth",
            billing_kind="subscription",
            label="Acme subscription",
            terms_posture="third_party_permitted",
        )

        restored = AccessRoute.from_dict(json.loads(json.dumps(route.to_dict())))

        self.assertEqual("third_party_permitted", restored.to_dict()["terms_posture"])
        self.assertEqual(route, restored)

    def test_an_unknown_posture_is_refused_rather_than_silently_permitted(self) -> None:
        with self.assertRaises(ValueError) as caught:
            AccessRoute(
                id="subscription",
                kind="oauth",
                billing_kind="subscription",
                label="Acme subscription",
                terms_posture="probably_fine",
            )

        self.assertIn("probably_fine", str(caught.exception))


class DriverGatingTests(unittest.TestCase):
    def test_a_first_party_only_route_offers_delegated_import_only(self) -> None:
        login = broker_for(oauth_provider("first_party_only"))

        self.assertEqual(
            ("delegated_import",),
            login.offered_drivers("acme-cloud", route_id="subscription"),
        )

    def test_a_first_party_only_route_refuses_a_browser_flow_naming_the_posture(
        self,
    ) -> None:
        login = broker_for(oauth_provider("first_party_only"))

        with self.assertRaises(ValueError) as caught:
            login.begin("acme-cloud", route_id="subscription", driver="oauth_pkce")

        self.assertIn("first_party_only", str(caught.exception))

    def test_a_first_party_only_route_refuses_a_device_flow_naming_the_posture(
        self,
    ) -> None:
        login = broker_for(oauth_provider("first_party_only"))

        with self.assertRaises(ValueError) as caught:
            login.begin("acme-cloud", route_id="subscription", driver="oauth_device")

        self.assertIn("first_party_only", str(caught.exception))

    def test_a_route_declaring_a_gated_driver_refuses_it_without_being_asked(
        self,
    ) -> None:
        login = broker_for(oauth_provider("first_party_only"))

        with self.assertRaises(ValueError) as caught:
            login.begin("acme-cloud", route_id="subscription")

        self.assertIn("first_party_only", str(caught.exception))

    def test_an_unverified_route_behaves_exactly_like_a_first_party_only_one(
        self,
    ) -> None:
        unverified = broker_for(oauth_provider(None))
        first_party = broker_for(oauth_provider("first_party_only"))

        self.assertEqual(
            first_party.offered_drivers("acme-cloud", route_id="subscription"),
            unverified.offered_drivers("acme-cloud", route_id="subscription"),
        )
        for driver in ("oauth_pkce", "oauth_device"):
            with self.assertRaises(ValueError) as caught:
                unverified.begin("acme-cloud", route_id="subscription", driver=driver)
            self.assertIn("unverified", str(caught.exception))

    def test_a_third_party_permitted_route_offers_all_three_drivers(self) -> None:
        login = broker_for(oauth_provider("third_party_permitted"))

        self.assertEqual(
            ("delegated_import", "oauth_pkce", "oauth_device"),
            login.offered_drivers("acme-cloud", route_id="subscription"),
        )

    def test_a_gated_request_never_reaches_the_provider(self) -> None:
        login = broker_for(oauth_provider("unverified"))

        with self.assertRaises(ValueError):
            login.begin("acme-cloud", route_id="subscription", driver="oauth_pkce")

    def test_a_permitted_route_still_begins_the_browser_flow(self) -> None:
        def transport(url, parameters, headers, timeout):
            return {"access_token": "unused", "expires_in": 3600}

        login = broker_for(oauth_provider("third_party_permitted"), transport)

        session = login.begin("acme-cloud", route_id="subscription")

        self.assertEqual("oauth_pkce", session.driver)
        session.private["redirect"].close()

    def test_posture_never_gates_the_credential_the_user_already_holds(self) -> None:
        route = provider_access_routes(fixture_catalog().provider("openai"))[0]

        self.assertIn("api_key_paste", permitted_drivers(route))

    def test_a_route_needing_no_credential_offers_no_driver(self) -> None:
        route = provider_access_routes(fixture_catalog().provider("local"))[0]

        self.assertEqual((), permitted_drivers(route))


if __name__ == "__main__":
    unittest.main()
