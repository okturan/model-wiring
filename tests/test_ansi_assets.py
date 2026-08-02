from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import fixture_catalog

from model_provider_surfaces import (
    SelectionController,
    copy_web_assets,
    render_pipeline,
    render_screen,
    safe_text,
)


class AnsiAndAssetTests(unittest.TestCase):
    def test_plain_renderer_is_human_readable_and_has_no_escape_codes(self) -> None:
        controller = SelectionController(fixture_catalog())
        controller.search("luna", provider="openai-codex")
        controller.choose()

        screen = render_screen(controller.view(), width=90, height=26, color=False)

        self.assertIn("DISCOVER", screen)
        self.assertIn("BILLING", screen)
        self.assertIn("openai-codex/gpt-5.6-luna", screen)
        self.assertNotIn("\x1b", screen)

    def test_catalog_text_cannot_inject_terminal_control_sequences(self) -> None:
        self.assertEqual("red", safe_text("\x1b[31mred\x1b[0m"))
        self.assertEqual("one two", safe_text("one\ntwo"))
        self.assertEqual("hidden", safe_text("\x9b31mhidden"))

    def test_pipeline_uses_color_only_when_requested(self) -> None:
        view = SelectionController(fixture_catalog()).view()
        self.assertIn("\x1b", render_pipeline(view, color=True))
        self.assertNotIn("\x1b", render_pipeline(view, color=False))

    def test_web_assets_copy_as_a_complete_framework_free_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            written = copy_web_assets(Path(directory))
            self.assertEqual(3, len(written))
            for path in written:
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
