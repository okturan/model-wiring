"""Dependency-free full-screen ANSI selector."""

from __future__ import annotations

import json
import os
import select
import shutil
import sys
import termios
import tty
from typing import TextIO

from model_provider import SelectionPlan
from model_provider.errors import ModelProviderError

from .ansi import render_screen
from .controller import SelectionController

ENTER_ALT = "\x1b[?1049h\x1b[?25l"
EXIT_ALT = "\x1b[?25h\x1b[?1049l"
CLEAR = "\x1b[H\x1b[2J"


def enable_character_input(descriptor: int) -> None:
    """Read keys immediately without disabling terminal output processing.

    ``tty.setraw`` also clears ``OPOST``. That makes a bare line feed move down
    without returning to column zero, so multiline ANSI screens drift across
    real terminals. Cbreak mode gives the picker unbuffered, no-echo input while
    preserving normal newline rendering.
    """

    tty.setcbreak(descriptor)


def run_tui(
    controller: SelectionController,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    color: bool = True,
) -> SelectionPlan | None:
    if not input_stream.isatty() or not output_stream.isatty():
        raise RuntimeError("interactive picker requires a TTY")
    descriptor = input_stream.fileno()
    previous = termios.tcgetattr(descriptor)
    output_stream.write(ENTER_ALT)
    output_stream.flush()
    try:
        enable_character_input(descriptor)
        while True:
            size = shutil.get_terminal_size((100, 30))
            output_stream.write(
                CLEAR
                + render_screen(
                    controller.view(),
                    width=size.columns,
                    height=size.lines,
                    color=color,
                )
            )
            output_stream.flush()
            key = _read_key(input_stream)
            action, plan = handle_key(controller, key)
            if action == "quit":
                return None
            if action == "selected":
                return plan
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
        output_stream.write(EXIT_ALT)
        output_stream.flush()


def _read_key(stream: TextIO) -> str:
    descriptor = stream.fileno()
    first = os.read(descriptor, 1)
    value = _decode_character(descriptor, first)
    if value == "\x03":
        return "ctrl-c"
    if value in {"\r", "\n"}:
        return "enter"
    if value == "\t":
        return "tab"
    if value in {"\x7f", "\b"}:
        return "backspace"
    if value != "\x1b":
        return value
    if not select.select([descriptor], [], [], 0.04)[0]:
        return "escape"
    second = os.read(descriptor, 1)
    if second != b"[":
        return "escape"
    if not select.select([descriptor], [], [], 0.04)[0]:
        return "escape"
    third = os.read(descriptor, 1).decode("ascii", errors="ignore")
    return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(third, "escape")


def _decode_character(descriptor: int, first: bytes) -> str:
    if not first:
        return ""
    lead = first[0]
    expected = (
        1
        if lead < 0x80
        else 2
        if lead & 0xE0 == 0xC0
        else 3
        if lead & 0xF0 == 0xE0
        else 4
        if lead & 0xF8 == 0xF0
        else 1
    )
    value = bytearray(first)
    while len(value) < expected:
        if not select.select([descriptor], [], [], 0.04)[0]:
            break
        value.extend(os.read(descriptor, 1))
    return bytes(value).decode("utf-8", errors="replace")


def handle_key(
    controller: SelectionController, key: str
) -> tuple[str, SelectionPlan | None]:
    """Apply one decoded key without hiding search behind a modal input."""

    view = controller.view()
    if key == "ctrl-c":
        return "quit", None
    if key == "up":
        controller.move(-1)
    elif key == "down":
        controller.move(1)
    elif key in {"right", "tab"}:
        if view.focus == "providers":
            controller.activate_provider()
    elif key == "left":
        if view.focus == "models":
            controller.focus_providers()
    elif key == "escape":
        if view.query:
            _apply_query(controller, "")
        elif view.focus == "models":
            controller.focus_providers()
        else:
            return "quit", None
    elif key == "backspace":
        if view.query:
            _apply_query(controller, view.query[:-1])
    elif key == "E" and view.selected_model is not None:
        controller.cycle_effort()
    elif key == "V" and view.selected_model is not None:
        controller.cycle_variant()
    elif key == "T" and view.selected_model is not None:
        controller.cycle_tier()
    elif key == "P" and view.selected_model is not None:
        controller.cycle_profile()
    elif key == "enter":
        if view.focus == "providers":
            controller.activate_provider()
        elif view.candidates:
            candidate = view.candidates[controller.cursor]
            if (
                controller.selected is None
                or controller.selected.qualified_id != candidate.id
            ):
                controller.choose()
            else:
                try:
                    return "selected", controller.resolve()
                except (ModelProviderError, ValueError):
                    pass
    elif len(key) == 1 and key.isprintable():
        _apply_query(controller, view.query + key)
    return "continue", None


def _apply_query(controller: SelectionController, query: str) -> None:
    view = controller.view()
    if view.focus == "models" and view.active_provider is not None:
        controller.search(query, provider=view.active_provider)
    else:
        controller.search(query)


def print_plan(plan: SelectionPlan, stream: TextIO = sys.stdout) -> None:
    stream.write(
        json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":")) + os.linesep
    )
