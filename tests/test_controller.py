from __future__ import annotations

import unittest

from helpers import fixture_catalog
from model_provider import CredentialProfile

from model_provider_surfaces import SelectionController


class ControllerTests(unittest.TestCase):
    def test_browse_search_choose_and_resolve_share_core_contract(self) -> None:
        profile = CredentialProfile(
            id="codex",
            provider_id="openai-codex",
            auth_kind="delegated",
            billing_kind="subscription",
            metadata={"delegate": "codex-sdk"},
        )
        controller = SelectionController(fixture_catalog(), [profile])
        controller.search("luna", provider="openai-codex")

        self.assertEqual(1, len(controller.view().candidates))
        controller.choose()
        view = controller.view()
        self.assertEqual("openai-codex/gpt-5.6-luna", view.selected_model)
        self.assertIsNone(view.variant)
        self.assertIsNone(view.effort)
        self.assertIsNone(view.tier)
        self.assertEqual("subscription", view.billing_kind)
        self.assertEqual("codex", view.credential_profile)
        self.assertTrue(view.route_ready)

        controller.cycle_effort()
        controller.cycle_variant()
        controller.cycle_tier()
        plan = controller.resolve()

        self.assertEqual("low", plan.effort)
        self.assertEqual("fast", plan.variant)
        self.assertEqual("standard", plan.tier)
        self.assertEqual("codex", plan.credential_profile)

    def test_move_wraps_and_search_error_is_renderable(self) -> None:
        controller = SelectionController(fixture_catalog(), limit=3)
        controller.move(-1)
        self.assertEqual(2, controller.view().cursor)

        controller.search("does-not-exist-xyz")
        self.assertEqual((), controller.view().candidates)
        self.assertIn("No models", controller.view().error or "")

    def test_authenticated_provider_without_profile_is_not_marked_ready(self) -> None:
        controller = SelectionController(fixture_catalog())
        controller.search("luna", provider="openai")
        controller.choose()

        self.assertTrue(controller.view().auth_required)
        self.assertFalse(controller.view().route_ready)

    def test_multiple_billing_routes_remain_unresolved(self) -> None:
        profiles = [
            CredentialProfile(
                id="api",
                provider_id="openai",
                auth_kind="api_key",
                billing_kind="api",
                secret_ref="OPENAI_API_KEY",
                secret_store="environment",
            ),
            CredentialProfile(
                id="market",
                provider_id="openai",
                auth_kind="api_key",
                billing_kind="marketplace",
                secret_ref="market-key",
                secret_store="keyring",
            ),
        ]
        controller = SelectionController(fixture_catalog(), profiles)
        controller.search("luna", provider="openai")
        controller.choose()

        self.assertIsNone(controller.view().billing_kind)
        self.assertIsNone(controller.view().credential_profile)
        controller.cycle_billing()
        self.assertEqual("api", controller.view().billing_kind)
        self.assertEqual("api", controller.view().credential_profile)


if __name__ == "__main__":
    unittest.main()
