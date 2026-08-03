from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from model_wiring_surfaces.cli import build_parser

WORKSPACE = Path(__file__).parents[3]


class DefaultCommandTests(unittest.TestCase):
    def test_running_the_command_with_no_arguments_opens_the_picker(self) -> None:
        """A first-time user should not have to learn a subcommand name."""

        args = build_parser().parse_args([])

        self.assertEqual("pick", args.command)

    def test_global_options_still_work_without_a_subcommand(self) -> None:
        args = build_parser().parse_args(["--no-color"])

        self.assertEqual("pick", args.command)
        self.assertTrue(args.no_color)

    def test_naming_the_subcommand_explicitly_still_works(self) -> None:
        args = build_parser().parse_args(["render", "--width", "80"])

        self.assertEqual("render", args.command)
        self.assertEqual(80, args.width)


class RootDistributionTests(unittest.TestCase):
    def test_the_workspace_root_installs_both_packages(self) -> None:
        """`pip install <repo>` must be enough — no package directory to find."""

        root = tomllib.loads((WORKSPACE / "pyproject.toml").read_text("utf-8"))

        self.assertEqual("model-wiring", root["project"]["name"])
        dependencies = " ".join(root["project"]["dependencies"])
        self.assertIn("model-wiring-core", dependencies)
        self.assertIn("model-wiring-surfaces", dependencies)

    def test_the_root_distribution_exposes_a_single_launch_command(self) -> None:
        root = tomllib.loads((WORKSPACE / "pyproject.toml").read_text("utf-8"))

        scripts = root["project"]["scripts"]

        self.assertIn("model-wiring-pick", scripts)
        self.assertEqual("model_wiring_surfaces.cli:main", scripts["model-wiring-pick"])


if __name__ == "__main__":
    unittest.main()
