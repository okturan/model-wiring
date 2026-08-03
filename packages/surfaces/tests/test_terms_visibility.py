from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from helpers import fixture_catalog
from model_wiring import Catalog
from model_wiring_surfaces import ProviderView, SelectionController, render_screen

WEB_PICKER = (
    Path(__file__).parents[1]
    / "src"
    / "model_wiring_surfaces"
    / "web"
    / "model-wiring-picker.js"
)


def route(posture: str | None, **overrides: Any) -> dict[str, Any]:
    declared: dict[str, Any] = {
        "id": "subscription",
        "kind": "oauth",
        "billing_kind": "subscription",
        "label": "Acme subscription",
        **overrides,
    }
    if posture is not None:
        declared["terms_posture"] = posture
    return declared


def catalog_with(*routes: dict[str, Any]) -> Catalog:
    return Catalog.from_models_dev(
        {
            "acme": {
                "name": "Acme Models",
                "doc": "https://acme.example/docs",
                "models": {"a-1": {"name": "Acme One"}},
                "access_routes": list(routes),
            }
        },
        fetched_at="2026-08-02T00:00:00Z",
        include_default_overlays=False,
    )


def provider_view(controller: SelectionController, provider_id: str) -> ProviderView:
    for view in controller.view().providers:
        if view.id == provider_id:
            return view
    raise AssertionError(f"provider not listed: {provider_id}")


class ProviderPostureTests(unittest.TestCase):
    def test_a_declared_posture_reaches_the_provider_view(self) -> None:
        controller = SelectionController(catalog_with(route("third_party_permitted")))

        self.assertEqual(
            "third_party_permitted", provider_view(controller, "acme").terms_posture
        )

    def test_a_provider_that_declares_nothing_reports_unverified(self) -> None:
        controller = SelectionController(fixture_catalog())

        self.assertEqual("unverified", provider_view(controller, "acme").terms_posture)

    def test_a_provider_needing_no_credential_reports_no_posture(self) -> None:
        controller = SelectionController(fixture_catalog())

        self.assertIsNone(provider_view(controller, "local").terms_posture)

    def test_the_most_conservative_route_posture_is_the_one_reported(self) -> None:
        controller = SelectionController(
            catalog_with(
                route("third_party_permitted"),
                route(None, id="second", label="Acme second route"),
            )
        )

        self.assertEqual("unverified", provider_view(controller, "acme").terms_posture)

    def test_the_posture_is_serializable_for_non_python_surfaces(self) -> None:
        payload = provider_view(
            SelectionController(catalog_with(route("first_party_only"))), "acme"
        ).to_dict()

        self.assertEqual("first_party_only", payload["terms_posture"])
        self.assertEqual(
            "first_party_only",
            json.loads(json.dumps(payload))["access_routes"][0]["terms_posture"],
        )


class AnsiConnectViewTests(unittest.TestCase):
    def screen(self, catalog: Catalog) -> str:
        controller = SelectionController(catalog)
        controller.activate_provider()
        return render_screen(controller.view(), width=132, height=32, color=False)

    def test_the_connect_view_states_an_unverified_posture_and_what_it_means(
        self,
    ) -> None:
        screen = self.screen(catalog_with(route(None)))

        self.assertIn("terms not verified", screen)
        self.assertIn("only an existing sign-in from the provider's own tool", screen)

    def test_an_unverified_provider_reads_exactly_like_a_first_party_only_one(
        self,
    ) -> None:
        unverified = self.screen(catalog_with(route(None)))
        first_party = self.screen(catalog_with(route("first_party_only")))

        self.assertIn(
            "only an existing sign-in from the provider's own tool", unverified
        )
        self.assertIn(
            "only an existing sign-in from the provider's own tool", first_party
        )

    def test_a_permitting_provider_is_not_described_as_delegated_only(self) -> None:
        screen = self.screen(catalog_with(route("third_party_permitted")))

        self.assertIn("third-party clients permitted", screen)
        self.assertNotIn(
            "only an existing sign-in from the provider's own tool", screen
        )

    def test_a_provider_needing_no_credential_states_no_terms(self) -> None:
        controller = SelectionController(fixture_catalog())
        controller.search("local")
        controller.activate_provider()

        screen = render_screen(controller.view(), width=132, height=32, color=False)

        self.assertNotIn("terms", screen.lower())


NODE = shutil.which("node")

# The picker is a custom element, so the element registry and its base class
# are stubbed; the posture logic under test touches neither.
PICKER_PROBE = """
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {} };
const { ModelWiringPicker } = await import(process.argv[2]);
const picker = Object.create(ModelWiringPicker.prototype);
const oauth = (posture) => ({
  kind: "oauth",
  needs_credential: true,
  ...(posture ? { terms_posture: posture } : {}),
});
const posture = (...routes) => picker.termsPosture({ access_routes: routes });
console.log(JSON.stringify({
  undeclared: posture(oauth(null)),
  permitted: posture(oauth("third_party_permitted")),
  conservative: posture(oauth("third_party_permitted"), oauth("first_party_only")),
  anonymous: posture({ kind: "anonymous", needs_credential: false }),
  unverified_label: picker.termsLabel("unverified"),
  first_party_label: picker.termsLabel("first_party_only"),
  permitted_label: picker.termsLabel("third_party_permitted"),
  absent_label: picker.termsLabel(null),
}));
"""


@unittest.skipUnless(NODE, "node is not installed")
class WebPickerPostureBehaviourTests(unittest.TestCase):
    def probe(self) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "probe.mjs"
            script.write_text(PICKER_PROBE, encoding="utf-8")
            result = subprocess.run(
                [str(NODE), str(script), WEB_PICKER.as_uri()],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_an_undeclared_route_reads_as_unverified(self) -> None:
        self.assertEqual("unverified", self.probe()["undeclared"])

    def test_the_least_permitting_route_decides_what_the_row_states(self) -> None:
        probed = self.probe()

        self.assertEqual("third_party_permitted", probed["permitted"])
        self.assertEqual("first_party_only", probed["conservative"])

    def test_a_provider_needing_no_credential_states_no_terms(self) -> None:
        self.assertIsNone(self.probe()["anonymous"])

    def test_an_unverified_row_says_what_a_first_party_only_row_says(self) -> None:
        probed = self.probe()

        self.assertIn(
            probed["first_party_label"].lower(), probed["unverified_label"].lower()
        )
        self.assertNotEqual(probed["permitted_label"], probed["unverified_label"])
        self.assertEqual("", probed["absent_label"])


class WebPickerPostureTests(unittest.TestCase):
    def test_the_picker_shows_the_posture_on_its_provider_rows(self) -> None:
        source = WEB_PICKER.read_text(encoding="utf-8")

        self.assertTrue("terms_posture" in source, "no terms_posture use")
        self.assertTrue("termsPosture" in source, "no posture derived per provider")
        self.assertTrue(
            "Third-party sign-in permitted" in source, "permitting posture unlabelled"
        )
        self.assertTrue(
            "existing provider sign-in only" in source, "delegated-only left unsaid"
        )


if __name__ == "__main__":
    unittest.main()
