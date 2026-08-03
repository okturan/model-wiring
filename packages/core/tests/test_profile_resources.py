from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from model_wiring import CredentialProfile, ProfileRegistry

FD_DIRECTORY = Path("/proc/self/fd")


def open_handles(path: Path) -> int:
    """Count this process's open descriptors pointing at ``path``."""

    total = 0
    for entry in FD_DIRECTORY.iterdir():
        try:
            if os.readlink(entry) == str(path):
                total += 1
        except OSError:
            continue
    return total


@unittest.skipUnless(
    sys.platform.startswith("linux") and FD_DIRECTORY.is_dir(),
    "descriptor inspection needs /proc",
)
class ProfileRegistryResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp()) / "profiles.sqlite3"
        self.profile = CredentialProfile(
            id="openai-main",
            provider_id="openai",
            auth_kind="api_key",
            billing_kind="api",
            secret_ref="openai:api_key",
            secret_store="memory",
        )

    def test_repeated_reads_do_not_accumulate_database_connections(self) -> None:
        registry = ProfileRegistry(self.path)
        registry.upsert(self.profile)
        baseline = open_handles(self.path)

        for _ in range(20):
            registry.list()

        self.assertEqual(baseline, open_handles(self.path))

    def test_repeated_writes_do_not_accumulate_database_connections(self) -> None:
        registry = ProfileRegistry(self.path)
        registry.upsert(self.profile)
        baseline = open_handles(self.path)

        for index in range(20):
            registry.upsert(
                CredentialProfile(
                    id=f"openai-{index}",
                    provider_id="openai",
                    auth_kind="api_key",
                    billing_kind="api",
                    secret_ref=f"openai:{index}",
                    secret_store="memory",
                )
            )

        self.assertEqual(baseline, open_handles(self.path))

    def test_constructing_registries_does_not_accumulate_connections(self) -> None:
        ProfileRegistry(self.path)
        baseline = open_handles(self.path)

        for _ in range(20):
            ProfileRegistry(self.path)

        self.assertEqual(baseline, open_handles(self.path))


if __name__ == "__main__":
    unittest.main()
