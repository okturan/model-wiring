from __future__ import annotations

import json
from pathlib import Path

from model_wiring import Catalog

FIXTURE = Path(__file__).parent / "fixtures" / "models-dev.json"


def fixture_raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def fixture_catalog(**kwargs: object) -> Catalog:
    return Catalog.from_models_dev(
        fixture_raw(), fetched_at="2026-08-02T00:00:00Z", **kwargs
    )
