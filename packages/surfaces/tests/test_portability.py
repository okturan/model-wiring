from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest

BLOCK_POSIX_TERMINAL = textwrap.dedent(
    """
    import sys


    class PosixTerminalBlocker:
        \"\"\"Simulate native Windows, where termios and tty do not exist.\"\"\"

        blocked = {"termios", "tty"}

        def find_spec(self, name, path=None, target=None):
            if name in self.blocked:
                raise ImportError(f"No module named {name!r}")
            return None


    for name in PosixTerminalBlocker.blocked:
        sys.modules.pop(name, None)
    sys.meta_path.insert(0, PosixTerminalBlocker())
    """
)


def run_without_posix_terminal(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", BLOCK_POSIX_TERMINAL + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        check=False,
    )


class WindowsPortabilityTests(unittest.TestCase):
    def test_package_imports_without_posix_terminal_modules(self) -> None:
        result = run_without_posix_terminal(
            """
            import model_wiring_surfaces

            print(model_wiring_surfaces.SelectionController.__name__)
            """
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("SelectionController", result.stdout.strip())

    def test_web_assets_are_copyable_without_posix_terminal_modules(self) -> None:
        result = run_without_posix_terminal(
            """
            import tempfile
            from pathlib import Path

            from model_wiring_surfaces import copy_web_assets

            with tempfile.TemporaryDirectory() as directory:
                written = copy_web_assets(Path(directory))
                print(sorted(path.name for path in written))
            """
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("model-wiring-picker.js", result.stdout)

    def test_running_the_picker_without_a_posix_terminal_explains_why(self) -> None:
        result = run_without_posix_terminal(
            """
            from model_wiring_surfaces import run_tui

            try:
                run_tui(None)
            except RuntimeError as exc:
                print(type(exc).__name__, exc)
            """
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("RuntimeError", result.stdout)
        self.assertIn("termios", result.stdout)


if __name__ == "__main__":
    unittest.main()
