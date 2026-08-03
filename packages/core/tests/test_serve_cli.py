from __future__ import annotations

import json
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_login_cli import CliFixture, run


def gateway_overlay_file(directory: Path) -> Path:
    """Route data pointing at a port nothing is listening on: never called."""

    path = directory / "gateway-overlay.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "acme": {
                        "metadata": {
                            "access_routes": [
                                {
                                    "id": "api",
                                    "kind": "api_key",
                                    "billing_kind": "api",
                                    "label": "Acme API key",
                                    "env": ["ACME_KEY"],
                                    "metadata": {
                                        "gateway": {
                                            "base_url": "http://127.0.0.1:9/acme",
                                            "mount": "/acme",
                                            "paths": ["/v1/chat/completions"],
                                        }
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


class LoopbackServiceTestCase(CliFixture):
    """Runs a serving command to the point of listening, then stops it."""

    def start(self, *argv: str) -> tuple[int, dict, int]:
        with patch.object(ThreadingHTTPServer, "serve_forever", autospec=True) as loop:
            code, payload = run(*argv)
        return code, payload, loop.call_count


class ServeCommandTests(LoopbackServiceTestCase):
    def test_serve_prints_its_base_url_and_token_before_serving(self) -> None:
        code, payload, served = self.start(
            *self.common, "serve", "--port", "0", "--json"
        )

        self.assertEqual(0, code)
        self.assertEqual(1, served)
        self.assertTrue(payload["base_url"].startswith("http://127.0.0.1:"))
        self.assertGreater(int(payload["base_url"].rsplit(":", 1)[1]), 0)
        self.assertGreaterEqual(len(payload["token"]), 32)

    def test_the_token_is_printed_once(self) -> None:
        _, payload, _ = self.start(*self.common, "serve", "--port", "0", "--json")
        _, text, _ = self.start(*self.common, "serve", "--port", "0")

        printed = text["text"]

        self.assertEqual(2, len(printed.splitlines()))
        self.assertIn("base_url: http://127.0.0.1:", printed)
        self.assertEqual(1, printed.count("token: "))
        self.assertNotIn(payload["token"], printed)

    def test_each_start_issues_its_own_token(self) -> None:
        _, first, _ = self.start(*self.common, "serve", "--port", "0", "--json")
        _, second, _ = self.start(*self.common, "serve", "--port", "0", "--json")

        self.assertNotEqual(first["token"], second["token"])


class GatewayCommandTests(LoopbackServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.overlay = ("--overlay", str(gateway_overlay_file(self.workspace)))

    def test_the_gateway_prints_its_base_url_token_and_declared_routes(self) -> None:
        code, payload, served = self.start(
            *self.common,
            *self.overlay,
            "gateway",
            "--port",
            "0",
            "--provider",
            "acme",
            "--json",
        )

        self.assertEqual(0, code)
        self.assertEqual(1, served)
        base_url = payload["base_url"]
        self.assertTrue(base_url.startswith("http://127.0.0.1:"))
        self.assertGreaterEqual(len(payload["token"]), 32)
        self.assertEqual(
            [
                {
                    "provider_id": "acme",
                    "route_id": "api",
                    "url": f"{base_url}/acme",
                    "methods": ["POST"],
                    "paths": ["/v1/chat/completions"],
                }
            ],
            payload["routes"],
        )

    def test_the_text_view_names_the_url_an_sdk_should_point_at(self) -> None:
        _, text, _ = self.start(*self.common, *self.overlay, "gateway", "--port", "0")

        printed = text["text"]

        self.assertIn("base_url: http://127.0.0.1:", printed)
        self.assertEqual(1, printed.count("token: "))
        self.assertRegex(printed, r"route acme api: http://127\.0\.0\.1:\d+/acme")

    def test_a_catalogue_declaring_no_route_still_starts_and_says_so(self) -> None:
        # "local" is catalogued but ships no gateway data, so this stays a
        # zero-route start however much provider data the overlays grow.
        code, payload, served = self.start(
            *self.common, "gateway", "--port", "0", "--provider", "local", "--json"
        )

        self.assertEqual(0, code)
        self.assertEqual(1, served)
        self.assertEqual([], payload["routes"])

    def test_only_the_requested_providers_are_exposed(self) -> None:
        _, payload, _ = self.start(
            *self.common,
            *self.overlay,
            "gateway",
            "--port",
            "0",
            "--provider",
            "local",
            "--json",
        )

        self.assertEqual([], payload["routes"])

    def test_the_gateway_refuses_a_non_loopback_bind(self) -> None:
        code, payload, served = self.start(
            *self.common, *self.overlay, "gateway", "--host", "0.0.0.0", "--port", "0"
        )

        self.assertEqual(1, code)
        self.assertEqual(0, served)
        self.assertIn("loopback", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
