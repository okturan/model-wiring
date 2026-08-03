from __future__ import annotations

import json
import os
from pathlib import Path

from model_wiring import Catalog

FIXTURE = Path(__file__).parent / "fixtures" / "models-dev.json"


def fixture_raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def fixture_catalog(**kwargs: object) -> Catalog:
    return Catalog.from_models_dev(
        fixture_raw(), fetched_at="2026-08-02T00:00:00Z", **kwargs
    )


OVERLAY = (
    Path(__file__).parents[1]
    / "src"
    / "model_wiring"
    / "data"
    / "default-overlays.json"
)


def shipped_overlay() -> dict:
    return json.loads(OVERLAY.read_text(encoding="utf-8"))


def shipped_catalog() -> Catalog:
    """The catalogue the shipped overlay alone produces, with no network.

    Every provider the overlay names or builds on is stubbed in, because an
    overlay whose base is absent is skipped by design. Asserting against this
    checks the routes users really get, not the JSON they were declared in.
    """

    providers = shipped_overlay()["providers"]
    ids = set(providers)
    for provider in providers.values():
        base = provider.get("extends") or provider.get("models_from")
        if base:
            ids.add(str(base))
    return Catalog.from_models_dev(
        {provider_id: {"id": provider_id} for provider_id in sorted(ids)},
        fetched_at="2026-08-02T00:00:00Z",
    )


# Windows exposes only a read-only bit, so 0o600 is unattainable there. The
# library still restricts what the platform allows; the assertion does not.
POSIX_PERMISSIONS = os.name == "posix"
