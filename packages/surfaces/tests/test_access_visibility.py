from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import fixture_catalog
from model_wiring import Catalog, CredentialProfile
from model_wiring_surfaces import ProviderView, SelectionController, render_screen

WEB_PICKER = (
    Path(__file__).parents[1]
    / "src"
    / "model_wiring_surfaces"
    / "web"
    / "model-wiring-picker.js"
)


def controller_for(provider_id: str, **kwargs: object) -> SelectionController:
    controller = SelectionController(fixture_catalog(), **kwargs)
    controller.search(provider_id)
    return controller


def provider_view(controller: SelectionController, provider_id: str) -> ProviderView:
    for view in controller.view().providers:
        if view.id == provider_id:
            return view
    raise AssertionError(f"provider not listed: {provider_id}")


class ProviderAccessVisibilityTests(unittest.TestCase):
    def test_provider_view_names_the_variable_needed_to_connect(self) -> None:
        view = provider_view(controller_for("openai"), "openai")

        self.assertIn("OPENAI_API_KEY", view.required_variables)

    def test_provider_view_exposes_each_route_with_its_billing_account(self) -> None:
        view = provider_view(controller_for("openai"), "openai")

        self.assertTrue(view.access_routes)
        route = view.access_routes[0]
        self.assertEqual("api_key", route.kind)
        self.assertEqual("api", route.billing_kind)
        self.assertTrue(route.label)

    def test_credential_state_is_reported_separately_from_execution_support(
        self,
    ) -> None:
        view = provider_view(controller_for("openai"), "openai")

        self.assertEqual("connectable", view.credential_state)

    def test_configuring_a_credential_changes_only_the_credential_state(self) -> None:
        profile = CredentialProfile(
            id="openai-main",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="api-key:openai:main",
            secret_store="memory",
        )
        controller = controller_for("openai", profiles=(profile,))

        view = provider_view(controller, "openai")

        self.assertEqual("configured", view.credential_state)
        self.assertEqual("ready", view.state)

    def test_route_detail_is_serializable_for_non_python_surfaces(self) -> None:
        payload = provider_view(controller_for("openai"), "openai").to_dict()

        encoded = json.dumps(payload)

        self.assertIn("OPENAI_API_KEY", encoded)
        self.assertIn("access_routes", payload)
        self.assertIn("credential_state", payload)


class PreviewHonestyTests(unittest.TestCase):
    def test_model_is_not_called_ready_when_no_credential_is_configured(self) -> None:
        controller = controller_for("openai")
        controller.activate_provider()

        screen = render_screen(controller.view(), width=100, height=30, color=False)

        self.assertNotIn("ready in this app", screen)

    def test_uncredentialed_model_screen_names_the_missing_variable(self) -> None:
        controller = controller_for("openai")
        controller.activate_provider()

        screen = render_screen(controller.view(), width=100, height=30, color=False)

        self.assertIn("OPENAI_API_KEY", screen)

    def test_model_is_called_ready_once_a_credential_exists(self) -> None:
        profile = CredentialProfile(
            id="openai-main",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="api-key:openai:main",
            secret_store="memory",
        )
        controller = controller_for("openai", profiles=(profile,))
        controller.activate_provider()

        screen = render_screen(controller.view(), width=100, height=30, color=False)

        self.assertIn("ready in this app", screen)

    def test_local_provider_needing_no_credential_is_ready_immediately(self) -> None:
        controller = controller_for("local")
        controller.activate_provider()

        screen = render_screen(controller.view(), width=100, height=30, color=False)

        self.assertIn("ready in this app", screen)


class WebPickerAccessTests(unittest.TestCase):
    def test_web_picker_consumes_served_route_requirements(self) -> None:
        source = WEB_PICKER.read_text(encoding="utf-8")

        self.assertTrue("required_variables" in source, "no required_variables use")
        self.assertTrue("credential_state" in source, "no credential_state use")
        self.assertTrue("access_routes" in source, "no access_routes use")


class AlibabaRegressionTests(unittest.TestCase):
    """The reported defect: a real provider presenting as a dead end."""

    def setUp(self) -> None:
        self.catalog = Catalog.from_models_dev(
            {
                "alibaba": {
                    "name": "Alibaba",
                    "env": ["DASHSCOPE_API_KEY"],
                    "doc": "https://www.alibabacloud.com/help/en/model-studio/models",
                    "models": {"qwen-max": {"name": "Qwen Max"}},
                }
            },
            fetched_at="2026-08-02T00:00:00Z",
            include_default_overlays=False,
        )

    def test_alibaba_explains_that_it_needs_dashscope_api_key(self) -> None:
        controller = SelectionController(self.catalog)

        view = next(
            item for item in controller.view().providers if item.id == "alibaba"
        )

        self.assertEqual("connectable", view.credential_state)
        self.assertIn("DASHSCOPE_API_KEY", view.required_variables)

    def test_alibaba_is_never_described_as_a_credential_bundle(self) -> None:
        controller = SelectionController(self.catalog)

        view = next(
            item for item in controller.view().providers if item.id == "alibaba"
        )

        self.assertEqual(("api_key",), tuple(view.auth_methods))

    def test_alibaba_offers_the_documentation_that_issues_the_key(self) -> None:
        controller = SelectionController(self.catalog)

        view = next(
            item for item in controller.view().providers if item.id == "alibaba"
        )

        self.assertTrue(view.access_routes[0].doc_url)


if __name__ == "__main__":
    unittest.main()
