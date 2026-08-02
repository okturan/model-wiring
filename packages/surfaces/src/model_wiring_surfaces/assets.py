"""Access and copy the framework-free browser primitive."""

from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

WEB_ASSETS = (
    "model-wiring-picker.js",
    "model-wiring-picker.css",
    "demo.html",
)


def copy_web_assets(destination: Path) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    source = files("model_wiring_surfaces").joinpath("web")
    written: list[Path] = []
    for name in WEB_ASSETS:
        target = destination / name
        with (
            source.joinpath(name).open("rb") as source_handle,
            target.open("wb") as target_handle,
        ):
            shutil.copyfileobj(source_handle, target_handle)
        written.append(target)
    return tuple(written)
