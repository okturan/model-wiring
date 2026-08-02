from __future__ import annotations

import unittest

from helpers import fixture_catalog
from model_wiring.api import ProviderService


class ProviderAccessEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ProviderService(fixture_catalog())

    def test_provider_listing_serves_the_routes_a_surface_needs(self) -> None:
        status, payload = self.service.get("/v1/providers", {})

        self.assertEqual(200, status)
        openai = next(item for item in payload["items"] if item["id"] == "openai")
        self.assertIn("access_routes", openai)
        self.assertEqual(["OPENAI_API_KEY"], openai["required_variables"])
        self.assertEqual("connectable", openai["credential_state"])

    def test_configured_provider_is_reported_as_configured(self) -> None:
        status, payload = self.service.get("/v1/providers", {})

        local = next(item for item in payload["items"] if item["id"] == "local")

        self.assertEqual(200, status)
        self.assertEqual("configured", local["credential_state"])

    def test_provider_listing_never_serves_secret_material(self) -> None:
        _, payload = self.service.get("/v1/providers", {})

        encoded = str(payload).lower()

        self.assertNotIn("secret_ref", encoded)
        self.assertNotIn("access_token", encoded)


if __name__ == "__main__":
    unittest.main()
