"""Reference CLI over the shared surface controller."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from model_provider import Catalog, ModelsDevSource, ProfileRegistry, load_overlay

from .ansi import render_screen
from .assets import copy_web_assets
from .controller import SelectionController
from .tui import print_plan, run_tui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-provider-ui")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--profiles", type=Path)
    parser.add_argument("--overlay", action="append", default=[], type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("pick", help="interactive ANSI selector")
    pick.add_argument("--query", default="")
    pick.add_argument("--no-color", action="store_true")
    render = commands.add_parser("render", help="render one non-interactive screen")
    render.add_argument("--query", default="")
    render.add_argument("--model")
    render.add_argument("--width", type=int, default=100)
    render.add_argument("--height", type=int, default=30)
    render.add_argument("--no-color", action="store_true")
    assets = commands.add_parser("web-assets", help="copy the Web Component assets")
    assets.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "web-assets":
        paths = copy_web_assets(args.output)
        print(json.dumps({"files": [str(path) for path in paths]}, sort_keys=True))
        return 0
    source = ModelsDevSource(cache_path=args.cache) if args.cache else ModelsDevSource()
    overlays = [load_overlay(path) for path in args.overlay]
    catalog = Catalog.from_cache_or_sync(source=source, overlays=overlays)
    profiles = ProfileRegistry(args.profiles) if args.profiles else ProfileRegistry()
    controller = SelectionController(catalog, profiles)
    controller.search(args.query)
    if args.command == "render":
        if args.model:
            for index, candidate in enumerate(controller.view().candidates):
                if candidate.id == args.model:
                    controller.choose(index)
                    break
        print(
            render_screen(
                controller.view(),
                width=args.width,
                height=args.height,
                color=not args.no_color,
            )
        )
        return 0
    if args.command == "pick":
        try:
            plan = run_tui(controller, color=not args.no_color)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if plan is not None:
            print_plan(plan)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
