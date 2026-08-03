"""Mechanical guard on the promise that no first-party client identity ships.

A borrowed client id would put a revocable shared identity onto the end users
of every application built on this kit, so the rule is enforced by scanning
what is actually packaged rather than by discipline.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from model_wiring.access import AccessRoute
from model_wiring.oauth_login import route_oauth_config

# Everything setuptools packages lives under a workspace member's src tree.
PACKAGES = Path(__file__).parents[2]
SHIPPED_TREES = tuple(sorted(PACKAGES.glob("*/src")))

# A literal bound to a client-id name: the shape an embedded identity takes
# when someone pastes one in. A value read from data never matches, because
# the right-hand side is then an expression rather than a quoted string.
CLIENT_ID_BINDING = re.compile(
    r"""(?ix)
    ["']? (?: oauth [_-]? )? client [_-]? id ["']?
    \s* [:=] \s*
    (?P<quote>["'])(?P<value>[^"']{6,})(?P=quote)
    """
)

# Shapes the providers themselves issue, caught wherever they appear and
# whatever they are called.
ISSUED_SHAPES = (
    ("google", re.compile(r"\d{6,}-[0-9a-z]{12,}\.apps\.googleusercontent\.com")),
    ("github-app", re.compile(r"\bIv1\.[0-9a-f]{16}\b")),
    ("github-oauth-app", re.compile(r"\bOv23[0-9A-Za-z]{14,}\b")),
    ("prefixed", re.compile(r"\b(?:app|client|oauth)_[0-9A-Za-z]{20,}\b")),
    (
        "uuid",
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    ),
)


def scan_source(text: str, *, where: str) -> list[str]:
    """Report every client identifier ``text`` embeds, named by where it is."""

    findings = [
        f"{where}: client_id literal {match.group('value')!r}"
        for match in CLIENT_ID_BINDING.finditer(text)
    ]
    findings.extend(
        f"{where}: {shape} client identifier {match.group(0)!r}"
        for shape, pattern in ISSUED_SHAPES
        for match in pattern.finditer(text)
    )
    return findings


def scan_tree(root: Path) -> tuple[list[str], int]:
    """Scan every readable file under ``root``; return findings and file count."""

    findings: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        findings.extend(scan_source(text, where=str(path.relative_to(root))))
    return findings, scanned


PLANTED = {
    "settings.py": 'CLIENT_ID = "1234567890-abcdefghijklmnopqrst.apps.googleusercontent.com"\n',
    "routes.json": '{"oauth": {"client_id": "Iv1.0123456789abcdef"}}\n',
    "widget.js": 'const identity = "app_0123456789abcdefghijklmn";\n',
    "config.toml": 'audience = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"\n',
}

READS_FROM_DATA = """
config = OAuthProviderConfig(
    client_id=str(declared["client_id"]),
    scopes=tuple(declared.get("scopes", ())),
)
missing = [name for name in ("client_id", "token_endpoint") if not declared.get(name)]
parameters = {"client_id": config.client_id}
"""


class ScannerTests(unittest.TestCase):
    """The guard is only worth running if it can fail."""

    def test_every_planted_identifier_shape_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            for name, body in PLANTED.items():
                (tree / name).write_text(body, encoding="utf-8")

            findings, scanned = scan_tree(tree)

        self.assertEqual(len(PLANTED), scanned)
        for name in PLANTED:
            self.assertTrue(
                any(finding.startswith(name) for finding in findings),
                f"{name} was not detected: {findings}",
            )

    def test_a_client_identity_read_from_data_is_not_flagged(self) -> None:
        self.assertEqual([], scan_source(READS_FROM_DATA, where="oauth_login.py"))


class ShippedPackageTests(unittest.TestCase):
    def test_no_provider_oauth_client_identifier_ships_in_the_package(self) -> None:
        for tree in SHIPPED_TREES:
            with self.subTest(tree=tree.parent.name):
                findings, scanned = scan_tree(tree)

                self.assertEqual([], findings)
                self.assertGreater(scanned, 5, "the scan reached almost nothing")

    def test_the_scan_covers_every_shipped_package(self) -> None:
        self.assertEqual(
            {"core", "surfaces"}, {tree.parent.name for tree in SHIPPED_TREES}
        )


class SuppliedClientIdentityTests(unittest.TestCase):
    def test_an_oauth_route_takes_its_client_identity_from_supplied_data(self) -> None:
        route = AccessRoute(
            id="subscription",
            kind="oauth",
            billing_kind="subscription",
            label="Acme subscription",
            terms_posture="third_party_permitted",
            metadata={
                "oauth": {
                    "client_id": "application-registered-client",
                    "authorization_endpoint": "https://acme.example/authorize",
                    "token_endpoint": "https://acme.example/token",
                }
            },
        )

        self.assertEqual(
            "application-registered-client", route_oauth_config(route).client_id
        )

    def test_a_route_supplying_no_client_identity_says_so(self) -> None:
        route = AccessRoute(
            id="subscription",
            kind="oauth",
            billing_kind="subscription",
            label="Acme subscription",
            terms_posture="third_party_permitted",
        )

        with self.assertRaises(ValueError) as caught:
            route_oauth_config(route)

        self.assertIn("client_id", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
