from __future__ import annotations

import json
from pathlib import Path

from model_provider import Catalog

FIXTURE = (
    Path(__file__).parents[2]
    / "model-provider-kit"
    / "tests"
    / "fixtures"
    / "models-dev.json"
)


def fixture_catalog() -> Catalog:
    return Catalog.from_models_dev(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        fetched_at="2026-08-02T00:00:00Z",
    )
