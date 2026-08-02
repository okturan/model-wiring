from __future__ import annotations

import unittest

from helpers import fixture_catalog
from model_provider import CredentialProfile

from model_provider_surfaces import SelectionController


class ControllerTests(unittest.TestCase):
    @staticmethod
    def codex_profile() -> CredentialProfile:
        return CredentialProfile(
            id="codex",
            provider_id="openai-codex",
            auth_kind="delegated",
            billing_kind="subscription",
            metadata={"delegate": "codex-sdk"},
        )

    @staticmethod
    def atlas_support(model) -> str | None:
        if model.provider_id == "openai-codex":
            return None
        return "This application has no executor for this provider yet."

    def test_initial_view_is_provider_first_and_scopes_model_preview(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            [self.codex_profile()],
            route_support=self.atlas_support,
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        view = controller.view()

        self.assertEqual("providers", view.focus)
        self.assertFalse(view.provider_chosen)
        self.assertEqual("openai-codex", view.active_provider)
        self.assertEqual("openai-codex", view.providers[0].id)
        self.assertEqual("ready", view.providers[0].state)
        self.assertEqual(1, view.providers[0].runnable_model_count)
        self.assertTrue(
            all(item.provider == "openai-codex" for item in view.candidates)
        )
        self.assertEqual("openai-codex/gpt-5.6-luna", view.preview_model)
        self.assertIsNone(view.selected_model)

    def test_provider_readiness_orders_ready_connect_and_catalog(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            [self.codex_profile()],
            route_support=lambda model: (
                None
                if model.provider_id in {"openai-codex", "openai"}
                else "catalog only"
            ),
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        states = {
            provider.id: provider.state for provider in controller.view().providers
        }
        ordered = [provider.id for provider in controller.view().providers]

        self.assertEqual("ready", states["openai-codex"])
        self.assertEqual("connect", states["openai"])
        self.assertEqual("catalog", states["acme"])
        self.assertLess(ordered.index("openai-codex"), ordered.index("openai"))
        self.assertLess(ordered.index("openai"), ordered.index("acme"))

    def test_equal_state_providers_follow_shared_popularity_then_name(self) -> None:
        controller = SelectionController(fixture_catalog())

        connect = [
            provider.id
            for provider in controller.view().providers
            if provider.state == "connect"
        ]

        self.assertLess(connect.index("openai"), connect.index("acme"))

    def test_provider_activation_and_back_are_explicit_transitions(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            [self.codex_profile()],
            route_support=self.atlas_support,
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        controller.activate_provider()
        self.assertEqual("models", controller.view().focus)
        self.assertTrue(controller.view().provider_chosen)

        controller.focus_providers()
        self.assertEqual("providers", controller.view().focus)
        self.assertFalse(controller.view().provider_chosen)
        self.assertEqual("openai-codex", controller.view().active_provider)

    def test_global_search_reports_provider_match_counts_without_flattening(
        self,
    ) -> None:
        controller = SelectionController(
            fixture_catalog(),
            [self.codex_profile()],
            route_support=self.atlas_support,
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        controller.search("luna")
        view = controller.view()
        matches = {provider.id: provider.match_count for provider in view.providers}

        self.assertGreater(matches["openai-codex"], 0)
        self.assertGreater(matches["openai"], 0)
        self.assertGreater(matches["acme"], 0)
        self.assertTrue(
            all(item.provider == view.active_provider for item in view.candidates)
        )

        controller.search("")
        self.assertEqual("providers", controller.view().focus)
        self.assertTrue(
            all(
                provider.match_count == provider.model_count
                for provider in controller.view().providers
            )
        )

    def test_model_search_cannot_escape_the_active_provider(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            [self.codex_profile()],
            route_support=self.atlas_support,
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )
        controller.activate_provider()

        controller.search("luna")
        view = controller.view()

        self.assertEqual("models", view.focus)
        self.assertEqual("openai-codex", view.active_provider)
        self.assertTrue(view.candidates)
        self.assertTrue(
            all(item.provider == "openai-codex" for item in view.candidates)
        )

    def test_catalog_browsing_is_separate_from_application_route_support(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            [self.codex_profile()],
            route_support=self.atlas_support,
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        view = controller.view()
        self.assertEqual(4, view.provider_count)
        self.assertEqual(4, view.model_count)
        self.assertEqual(1, view.runnable_provider_count)
        self.assertEqual(1, view.runnable_model_count)
        self.assertEqual("openai-codex/gpt-5.6-luna", view.candidates[0].id)
        self.assertTrue(view.candidates[0].route_supported)
        self.assertNotIn("acme/gpt-5.6-luna", {item.id for item in view.candidates})

        acme = next(
            index for index, item in enumerate(view.providers) if item.id == "acme"
        )
        controller.choose_provider(acme)
        controller.choose()
        unsupported_view = controller.view()
        self.assertFalse(unsupported_view.route_supported)
        self.assertFalse(unsupported_view.route_ready)
        self.assertIn("no executor", unsupported_view.route_support_reason or "")
        with self.assertRaisesRegex(ValueError, "no executor"):
            controller.resolve()

    def test_runnable_scope_filters_without_hiding_global_catalog_totals(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            route_support=lambda model: (
                None if model.provider_id == "openai-codex" else "catalog only"
            ),
        )

        controller.browse(runnable_only=True)
        view = controller.view()

        self.assertEqual("runnable", view.scope)
        self.assertEqual(4, view.provider_count)
        self.assertEqual(1, len(view.candidates))
        self.assertEqual("openai-codex", view.candidates[0].provider)

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
        self.assertEqual(
            len(controller.view().providers) - 1, controller.view().provider_cursor
        )

        controller.search("does-not-exist-xyz")
        self.assertEqual((), controller.view().candidates)
        self.assertIn("No providers or models", controller.view().error or "")

    def test_authenticated_provider_without_profile_is_not_marked_ready(self) -> None:
        controller = SelectionController(fixture_catalog())
        controller.search("luna", provider="openai")
        controller.choose()

        self.assertTrue(controller.view().auth_required)
        self.assertFalse(controller.view().route_ready)

    def test_multiple_access_routes_require_a_route_and_derive_billing(self) -> None:
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
        self.assertFalse(controller.view().route_ready)
        controller.cycle_profile()
        self.assertEqual("api", controller.view().billing_kind)
        self.assertEqual("api", controller.view().credential_profile)
        self.assertEqual("api_key", controller.view().auth_kind)
        self.assertTrue(controller.view().route_ready)


if __name__ == "__main__":
    unittest.main()
