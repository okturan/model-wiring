from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURE
from model_wiring.catalog import content_digest
from model_wiring.cli import main
from model_wiring.contracts import utc_now


def run(*argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    text = (out.getvalue() or err.getvalue()).strip()
    return code, (json.loads(text) if text.startswith(("{", "[")) else {"text": text})


class CliFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp())
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cache = self.workspace / "models-dev.json"
        cache.write_text(
            json.dumps(
                {
                    "source": "test://fixture",
                    # A current stamp keeps the cache fresh, so the command
                    # under test never falls through to a network sync.
                    "fetched_at": utc_now(),
                    "raw_digest": content_digest(raw),
                    "catalog": raw,
                }
            ),
            encoding="utf-8",
        )
        self.common = (
            "--cache",
            str(cache),
            "--profiles",
            str(self.workspace / "profiles.sqlite3"),
        )

    def login_openai(self) -> tuple[int, dict]:
        return run(
            *self.common,
            "login",
            "openai",
            "--value",
            "OPENAI_API_KEY=sk-not-a-real-key",
            "--store",
            "memory",
            "--json",
        )


class OfflineTests(CliFixture):
    def test_access_commands_never_reach_the_network(self) -> None:
        """A fresh cache must be enough; CI and offline machines have no egress."""

        original = socket.socket

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("the command attempted a network connection")

        socket.socket = refuse  # type: ignore[assignment]
        try:
            code, payload = run(*self.common, "access", "list", "--json")
        finally:
            socket.socket = original  # type: ignore[assignment]

        self.assertEqual(0, code)
        self.assertTrue(payload["items"])


class AccessCommandTests(CliFixture):
    def test_access_lists_how_each_provider_can_be_connected(self) -> None:
        code, payload = run(*self.common, "access", "list", "--json")

        self.assertEqual(0, code)
        openai = next(item for item in payload["items"] if item["id"] == "openai")
        self.assertEqual("connectable", openai["credential_state"])
        self.assertEqual(["OPENAI_API_KEY"], openai["required_variables"])

    def test_access_show_explains_one_provider(self) -> None:
        code, payload = run(*self.common, "access", "show", "openai", "--json")

        self.assertEqual(0, code)
        self.assertEqual("openai", payload["provider_id"])
        self.assertEqual("api_key", payload["routes"][0]["kind"])

    def test_access_status_reports_nothing_configured_initially(self) -> None:
        code, payload = run(*self.common, "access", "status", "--json")

        self.assertEqual(0, code)
        self.assertEqual([], payload["items"])


class LoginCommandTests(CliFixture):
    def test_login_with_an_inline_value_stores_a_profile(self) -> None:
        code, payload = self.login_openai()

        self.assertEqual(0, code)
        self.assertEqual("openai", payload["profile"]["provider_id"])
        self.assertEqual("api_key", payload["profile"]["auth_kind"])

    def test_login_never_echoes_the_secret(self) -> None:
        code, payload = self.login_openai()

        self.assertEqual(0, code)
        self.assertNotIn("sk-not-a-real-key", json.dumps(payload))

    def test_login_without_a_value_describes_the_prompt_instead_of_guessing(
        self,
    ) -> None:
        code, payload = run(*self.common, "login", "openai", "--describe", "--json")

        self.assertEqual(0, code)
        self.assertEqual("secret", payload["prompt"]["kind"])
        self.assertEqual("OPENAI_API_KEY", payload["prompt"]["fields"][0]["name"])

    def test_status_reports_the_profile_after_logging_in(self) -> None:
        self.login_openai()

        code, payload = run(*self.common, "access", "status", "--json")

        self.assertEqual(0, code)
        self.assertEqual(["openai"], [item["provider_id"] for item in payload["items"]])

    def test_logging_in_then_out_leaves_no_profile(self) -> None:
        self.login_openai()

        code, _ = run(*self.common, "logout", "openai-api_key", "--json")
        _, status = run(*self.common, "access", "status", "--json")

        self.assertEqual(0, code)
        self.assertEqual([], status["items"])

    def test_unknown_provider_fails_with_a_useful_message(self) -> None:
        code, payload = run(*self.common, "login", "nope", "--describe", "--json")

        self.assertEqual(1, code)
        self.assertIn("nope", json.dumps(payload).lower())


if __name__ == "__main__":
    unittest.main()
