"""Dependency-free full-screen ANSI selector."""

from __future__ import annotations

import json
import os
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
        tty.setraw(descriptor)
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
            if key in {"q", "ctrl-c"}:
                return None
            if key in {"up", "k"}:
                controller.move(-1)
            elif key in {"down", "j"}:
                controller.move(1)
            elif key == "/":
                query = _read_query(input_stream, output_stream, controller.query)
                controller.search(query)
            elif key == "e":
                controller.cycle_effort()
            elif key == "v":
                controller.cycle_variant()
            elif key == "t":
                controller.cycle_tier()
            elif key == "b":
                controller.cycle_billing()
            elif key == "p":
                controller.cycle_profile()
            elif key == "enter":
                highlighted = controller.view().candidates
                if not highlighted:
                    continue
                candidate = highlighted[controller.cursor]
                if (
                    controller.selected is None
                    or controller.selected.qualified_id != candidate.id
                ):
                    controller.choose()
                else:
                    try:
                        return controller.resolve()
                    except (ModelProviderError, ValueError):
                        pass
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
        output_stream.write(EXIT_ALT)
        output_stream.flush()


def _read_key(stream: TextIO) -> str:
    value = stream.read(1)
    if value == "\x03":
        return "ctrl-c"
    if value in {"\r", "\n"}:
        return "enter"
    if value != "\x1b":
        return value
    second = stream.read(1)
    if second != "[":
        return "escape"
    third = stream.read(1)
    return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(third, "escape")


def _read_query(stream: TextIO, output: TextIO, initial: str) -> str:
    value = list(initial)
    while True:
        output.write(CLEAR + "Search models: " + "".join(value))
        output.flush()
        char = stream.read(1)
        if char in {"\r", "\n"}:
            return "".join(value)
        if char in {"\x1b", "\x03"}:
            return initial
        if char in {"\x7f", "\b"}:
            if value:
                value.pop()
        elif char.isprintable():
            value.append(char)


def print_plan(plan: SelectionPlan, stream: TextIO = sys.stdout) -> None:
    stream.write(
        json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":")) + os.linesep
    )
