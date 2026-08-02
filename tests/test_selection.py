from __future__ import annotations

import json
import unittest

from helpers import fixture_catalog

from model_provider import CredentialProfile, SelectionIntent, Selector
from model_provider.errors import (
    AmbiguousSelection,
    IncompatibleSelection,
    ProfileError,
)


class SelectionTests(unittest.TestCase):
    def test_exact_selection_validates_capabilities_variant_effort_and_tier(
        self,
    ) -> None:
        catalog = fixture_catalog()
        profile = CredentialProfile(
            id="codex-personal",
            provider_id="openai-codex",
            auth_kind="delegated",
            billing_kind="subscription",
            metadata={"delegate": "codex"},
        )
        selector = Selector(catalog, [profile])

        plan = selector.select(
            SelectionIntent(
                model="openai-codex/gpt-5.6-luna",
                effort="high",
                variant="fast",
                tier="fast",
                credential_profile="codex-personal",
                billing_kind="subscription",
                required_capabilities=("tool_call", "structured_output"),
                input_modalities=("image",),
                minimum_limits={"context": 900000},
            )
        )

        self.assertEqual("openai-codex/gpt-5.6-luna", plan.qualified_model)
        self.assertEqual("subscription", plan.billing_kind)
        self.assertEqual("fast", plan.provider_options["service_tier"])
        self.assertEqual("priority", plan.provider_options["body"]["service_tier"])
        self.assertNotIn("secret", json.dumps(plan.to_dict()).lower())

    def test_plan_id_is_content_derived_and_stable(self) -> None:
        selector = Selector(fixture_catalog())
        intent = SelectionIntent(
            model="local/small", required_capabilities=("tool_call",)
        )

        first = selector.select(intent)
        second = selector.select(intent)

        self.assertEqual(first.id, second.id)
        self.assertTrue(first.id.startswith("sel_"))

    def test_bare_duplicate_and_fuzzy_route_are_ambiguous(self) -> None:
        selector = Selector(fixture_catalog())

        with self.assertRaises(AmbiguousSelection):
            selector.select(SelectionIntent(model="gpt-5.6-luna"))
        with self.assertRaises(AmbiguousSelection):
            selector.select(SelectionIntent(query="luna"))

    def test_hard_constraints_fail_closed(self) -> None:
        selector = Selector(fixture_catalog())

        with self.assertRaises(IncompatibleSelection):
            selector.select(
                SelectionIntent(
                    model="local/small", required_capabilities=("reasoning",)
                )
            )
        with self.assertRaises(IncompatibleSelection):
            selector.select(SelectionIntent(model="local/small", effort="high"))
        with self.assertRaises(IncompatibleSelection):
            selector.select(
                SelectionIntent(model="local/small", minimum_limits={"context": 9000})
            )

    def test_billing_route_never_silently_crosses(self) -> None:
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
                id="marketplace",
                provider_id="openai",
                auth_kind="api_key",
                billing_kind="marketplace",
                secret_ref="some-ref",
                secret_store="keyring",
            ),
        ]
        selector = Selector(fixture_catalog(), profiles)

        with self.assertRaises(ProfileError):
            selector.select(SelectionIntent(model="openai/gpt-5.6-luna"))

        plan = selector.select(
            SelectionIntent(model="openai/gpt-5.6-luna", billing_kind="api")
        )
        self.assertEqual("api", plan.credential_profile)


if __name__ == "__main__":
    unittest.main()
