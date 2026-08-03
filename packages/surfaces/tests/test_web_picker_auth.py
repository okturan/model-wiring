from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

NODE = shutil.which("node")

WEB_PICKER = (
    Path(__file__).parents[1]
    / "src"
    / "model_wiring_surfaces"
    / "web"
    / "model-wiring-picker.js"
)

# The loopback service refuses every request without the token it prints at
# startup, so the browser client has to carry it on each call it makes.
PICKER_PROBE = """
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {} };
const { ModelWiringPicker } = await import(process.argv[2]);
const picker = Object.create(ModelWiringPicker.prototype);
const attributes = { endpoint: "http://127.0.0.1:8765", token: "issued-token" };
picker.getAttribute = (name) => (name in attributes ? attributes[name] : null);
picker.hasAttribute = (name) => name in attributes;
const calls = [];
globalThis.fetch = async (url, options = {}) => {
  calls.push({
    url,
    method: options.method || "GET",
    headers: options.headers || {},
    body: options.body || null,
  });
  return { ok: true, json: async () => ({ items: [] }) };
};
await picker.request("/v1/providers");
await picker.request("/v1/select", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: "{}",
});
delete attributes.token;
await picker.request("/v1/providers");
console.log(JSON.stringify(calls));
"""


@unittest.skipUnless(NODE, "node is not installed")
class WebPickerAuthenticationTests(unittest.TestCase):
    def calls(self) -> list[dict[str, Any]]:
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

    def test_every_call_carries_the_token_the_page_was_given(self) -> None:
        calls = self.calls()

        self.assertEqual(
            ["Bearer issued-token", "Bearer issued-token"],
            [call["headers"].get("Authorization") for call in calls[:2]],
        )
        self.assertEqual("http://127.0.0.1:8765/v1/providers", calls[0]["url"])

    def test_a_call_keeps_the_headers_it_already_needed(self) -> None:
        select = self.calls()[1]

        self.assertEqual("POST", select["method"])
        self.assertEqual("application/json", select["headers"]["Content-Type"])
        self.assertEqual("{}", select["body"])

    def test_a_page_given_no_token_sends_no_empty_credential(self) -> None:
        without = self.calls()[2]

        self.assertNotIn("Authorization", without["headers"])


class WebPickerAuthSourceTests(unittest.TestCase):
    def test_the_token_is_an_attribute_a_page_can_set(self) -> None:
        source = WEB_PICKER.read_text(encoding="utf-8")

        self.assertIn("set token(value)", source)
        self.assertIn("get token()", source)


if __name__ == "__main__":
    unittest.main()
