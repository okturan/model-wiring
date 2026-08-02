from __future__ import annotations

import os
import pty
import tempfile
import termios
import time
import unittest
from pathlib import Path

from helpers import fixture_catalog

from model_provider_surfaces import (
    SelectionController,
    copy_web_assets,
    enable_character_input,
    render_pipeline,
    render_screen,
    safe_text,
)
from model_provider_surfaces.tui import _read_key, handle_key


class AnsiAndAssetTests(unittest.TestCase):
    def test_direct_typing_is_search_and_escape_clears_before_quitting(self) -> None:
        controller = SelectionController(fixture_catalog())

        action, plan = handle_key(controller, "k")
        self.assertEqual("continue", action)
        self.assertIsNone(plan)
        self.assertEqual("k", controller.view().query)

        action, _ = handle_key(controller, "q")
        self.assertEqual("continue", action)
        self.assertEqual("kq", controller.view().query)

        action, _ = handle_key(controller, "backspace")
        self.assertEqual("continue", action)
        self.assertEqual("k", controller.view().query)

        handle_key(controller, "backspace")
        handle_key(controller, "/")
        self.assertEqual("/", controller.view().query)
        action, _ = handle_key(controller, "escape")
        self.assertEqual("continue", action)
        self.assertEqual("", controller.view().query)

        action, _ = handle_key(controller, "escape")
        self.assertEqual("quit", action)

    def test_standalone_escape_does_not_wait_for_an_arrow_sequence(self) -> None:
        master, slave = pty.openpty()
        stream = None
        try:
            enable_character_input(slave)
            stream = os.fdopen(os.dup(slave), "r", encoding="utf-8", buffering=1)
            os.write(master, b"\x1b")
            started = time.monotonic()

            self.assertEqual("escape", _read_key(stream))
            self.assertLess(time.monotonic() - started, 0.2)

            os.write(master, b"\x1b[A")
            self.assertEqual("up", _read_key(stream))
            os.write(master, "é".encode())
            self.assertEqual("é", _read_key(stream))
        finally:
            if stream is not None:
                stream.close()
            os.close(master)
            os.close(slave)

    def test_character_input_preserves_terminal_output_processing(self) -> None:
        master, slave = pty.openpty()
        try:
            enable_character_input(slave)
            attributes = termios.tcgetattr(slave)
        finally:
            os.close(master)
            os.close(slave)

        self.assertFalse(attributes[3] & termios.ICANON)
        self.assertFalse(attributes[3] & termios.ECHO)
        self.assertTrue(attributes[1] & termios.OPOST)

    def test_plain_renderer_is_human_readable_and_has_no_escape_codes(self) -> None:
        controller = SelectionController(fixture_catalog())
        controller.search("luna", provider="openai-codex")
        controller.choose()

        screen = render_screen(controller.view(), width=90, height=26, color=False)

        self.assertIn("PROVIDER", screen)
        self.assertIn("ACCESS", screen)
        self.assertIn("4 providers / 4 models", screen)
        self.assertIn("openai-codex/gpt-5.6-luna", screen)
        self.assertNotIn("unresolved", screen.lower())
        self.assertNotIn("\x1b", screen)

    def test_opening_screen_is_provider_first_contextual_and_border_free(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            route_support=lambda model: (
                None if model.provider_id == "openai-codex" else "catalog only"
            ),
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        screen = render_screen(controller.view(), width=132, height=32, color=False)

        self.assertIn("PROVIDERS", screen)
        self.assertIn("MODELS · OpenAI Codex", screen)
        self.assertIn("OpenAI Codex", screen)
        self.assertNotIn("acme/gpt-5.6-luna", screen)
        self.assertNotIn("│", screen)
        self.assertIn("Enter models", screen)
        self.assertIn("type to search", screen)
        self.assertNotIn("/ search", screen)
        self.assertNotIn("q quit", screen)
        self.assertNotIn("e effort", screen)
        self.assertNotIn("t tier", screen)

    def test_narrow_renderer_uses_sequential_provider_then_model_stages(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            route_support=lambda model: (
                None if model.provider_id == "openai-codex" else "catalog only"
            ),
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        provider_screen = render_screen(
            controller.view(), width=72, height=24, color=False
        )
        self.assertIn("CHOOSE PROVIDER", provider_screen)
        self.assertNotIn("openai-codex/gpt-5.6-luna", provider_screen)

        controller.activate_provider()
        model_screen = render_screen(
            controller.view(), width=72, height=24, color=False
        )
        self.assertIn("MODELS · OpenAI Codex", model_screen)
        self.assertIn("gpt-5.6-luna", model_screen)
        self.assertIn("← provider", model_screen)

    def test_colored_layout_never_wraps_or_shifts_the_model_column(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            route_support=lambda model: (
                None if model.provider_id == "openai-codex" else "catalog only"
            ),
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )

        narrow = render_screen(controller.view(), width=72, height=24, color=True)
        self.assertTrue(all(len(safe_text(line)) <= 72 for line in narrow.splitlines()))

        wide = render_screen(controller.view(), width=132, height=30, color=True)
        visible = [safe_text(line) for line in wide.splitlines()]
        heading = next(
            line for line in visible if "PROVIDERS" in line and "MODELS ·" in line
        )
        first_model = next(line for line in visible if "gpt-5.6-luna" in line)
        self.assertEqual(heading.index("MODELS ·"), first_model.rfind("▶"))
        self.assertTrue(all(len(line) <= 132 for line in visible))

    def test_renderer_explains_catalog_scope_and_catalog_only_routes(self) -> None:
        controller = SelectionController(
            fixture_catalog(),
            route_support=lambda model: (
                None
                if model.provider_id == "openai-codex"
                else "This application has no executor for this provider yet."
            ),
            limit=2,
            preferred_models=("openai-codex/gpt-5.6-luna",),
        )
        provider_index = next(
            index
            for index, provider in enumerate(controller.view().providers)
            if provider.id == "acme"
        )
        controller.choose_provider(provider_index)
        controller.choose()

        screen = render_screen(controller.view(), width=110, height=28, color=False)

        self.assertIn("4 providers / 4 models", screen)
        self.assertIn("1 provider / 1 model runnable here", screen)
        self.assertIn("BROWSE CATALOGUE", screen)
        self.assertIn("catalogue only", screen)
        self.assertIn("no executor", screen)
        self.assertNotIn("a all", screen)
        self.assertNotIn("r runnable", screen)
        self.assertNotIn("│", screen)

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

    def test_web_picker_supports_application_owned_recommended_defaults(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "src/model_provider_surfaces/web/model-provider-picker.js"
        ).read_text()

        for attribute in (
            "default-provider",
            "default-query",
            "default-model",
            "default-effort",
            "default-tier",
            "default-profile",
            "recommended-provider",
            "recommended-model",
            "supported-providers",
        ):
            self.assertIn(attribute, source)
        self.assertIn("set endpoint(value)", source)
        self.assertIn("model-provider-picker-ready", source)
        self.assertIn("Access route", source)
        self.assertIn("data-providers", source)
        self.assertIn("activateProvider", source)
        self.assertIn('params.set("provider"', source)
        self.assertIn("popularity_rank", source)
        self.assertIn("catalog", source)
        self.assertNotIn("<select data-provider", source)
        self.assertNotIn("data-billing", source)
        self.assertNotIn("Unresolved", source)
        stylesheet = (
            Path(__file__).parents[1]
            / "src/model_provider_surfaces/web/model-provider-picker.css"
        ).read_text()
        self.assertIn(":host([compact])", stylesheet)
        self.assertNotIn("border-left", stylesheet)


if __name__ == "__main__":
    unittest.main()
