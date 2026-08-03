from __future__ import annotations

import json
import unittest

from helpers import fixture_catalog
from model_wiring import CredentialProfile
from model_wiring_surfaces import ProviderView, SelectionController, render_screen


def profile(**overrides: object) -> CredentialProfile:
    fields: dict = {
        "id": "openai-main",
        "provider_id": "openai",
        "auth_kind": "api_key",
        "billing_kind": "api",
        "secret_ref": "openai:api_key",
        "secret_store": "memory",
    }
    fields.update(overrides)
    return CredentialProfile(**fields)


def provider_view(controller: SelectionController, provider_id: str) -> ProviderView:
    for view in controller.view().providers:
        if view.id == provider_id:
            return view
    raise AssertionError(f"provider not listed: {provider_id}")


class ProbeVisibilityTests(unittest.TestCase):
    def test_a_configured_provider_reports_its_last_probe(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            profiles=(
                profile(metadata={"last_probe_state": "ready", "last_probe_at": 1.0}),
            ),
        )

        view = provider_view(controller, "openai")

        self.assertEqual("ready", view.probe_state)

    def test_an_expired_credential_is_not_presented_as_ready(self) -> None:
        """A stored-but-dead credential must not read the same as a live one."""

        controller = SelectionController(
            fixture_catalog(),
            profiles=(profile(metadata={"last_probe_state": "expired"}),),
        )

        view = provider_view(controller, "openai")

        self.assertEqual("expired", view.probe_state)
        self.assertEqual("connect", view.state)

    def test_a_policy_denied_credential_is_distinct_from_a_missing_one(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            profiles=(profile(metadata={"last_probe_state": "policy_denied"}),),
        )

        view = provider_view(controller, "openai")

        self.assertEqual("policy_denied", view.probe_state)
        self.assertIn("denied", (view.support_reason or "").lower())

    def test_an_unprobed_credential_reports_no_probe_state(self) -> None:
        controller = SelectionController(fixture_catalog(), profiles=(profile(),))

        view = provider_view(controller, "openai")

        self.assertIsNone(view.probe_state)
        self.assertEqual("ready", view.state)

    def test_the_entitlement_a_credential_carries_is_shown(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            profiles=(
                profile(
                    billing_kind="subscription",
                    metadata={
                        "last_probe_state": "ready",
                        "entitlement_class": "subscription",
                    },
                ),
            ),
        )

        view = provider_view(controller, "openai")

        self.assertEqual("subscription", view.entitlement_class)

    def test_probe_detail_is_serializable_for_non_python_surfaces(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            profiles=(profile(metadata={"last_probe_state": "expired"}),),
        )

        payload = provider_view(controller, "openai").to_dict()

        json.dumps(payload)
        self.assertEqual("expired", payload["probe_state"])
        self.assertIn("entitlement_class", payload)

    def test_the_screen_tells_the_user_a_credential_stopped_working(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            profiles=(profile(metadata={"last_probe_state": "expired"}),),
        )
        controller.search("openai")

        screen = render_screen(controller.view(), width=100, height=30, color=False)

        self.assertIn("expired", screen.lower())


if __name__ == "__main__":
    unittest.main()
