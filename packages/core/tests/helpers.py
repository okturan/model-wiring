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


# Windows exposes only a read-only bit, so 0o600 is unattainable there. The
# library still restricts what the platform allows; the assertion does not.
POSIX_PERMISSIONS = os.name == "posix"
