from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import POSIX_PERMISSIONS, fixture_catalog, fixture_raw
from model_wiring import (
    POPULAR_PROVIDER_IDS,
    ModelsDevSource,
    provider_popularity_key,
)
from model_wiring.errors import AmbiguousSelection, CatalogError


class CatalogTests(unittest.TestCase):
    def test_catalog_ships_a_neutral_provider_popularity_order(self) -> None:
        catalog = fixture_catalog()

        self.assertEqual(
            ("anthropic", "github-copilot", "openai", "google", "openrouter"),
            POPULAR_PROVIDER_IDS[:5],
        )
        ordered = sorted(
            (catalog.provider("acme"), catalog.provider("openai")),
            key=provider_popularity_key,
        )

        self.assertEqual(["openai", "acme"], [provider.id for provider in ordered])
        self.assertIsInstance(
            catalog.provider("openai").metadata.get("popularity_rank"), int
        )

    def test_normalizes_models_dev_and_default_delegated_overlay(self) -> None:
        catalog = fixture_catalog()

        self.assertEqual(4, len(catalog.snapshot.providers))
        self.assertEqual(4, catalog.snapshot.model_count)
        model = catalog.model("openai/gpt-5.6-luna")
        self.assertTrue(model.capabilities["tool_call"])
        self.assertEqual(("low", "high"), model.reasoning_options)
        self.assertIn("fast", model.variants)
        delegated = catalog.provider("openai-codex")
        self.assertEqual("OpenAI Codex", delegated.name)
        self.assertEqual("openai-codex-sdk", delegated.adapter)
        self.assertEqual("delegated", delegated.auth_methods[0].kind)
        self.assertEqual("subscription", delegated.auth_methods[0].billing_kinds[0])

    def test_alias_and_ambiguous_bare_model(self) -> None:
        catalog = fixture_catalog()

        self.assertEqual("openai", catalog.model("openai/luna").provider_id)
        self.assertEqual("openai", catalog.model("OPENAI/LUNA").provider_id)
        with self.assertRaises(AmbiguousSelection) as raised:
            catalog.model("gpt-5.6-luna")
        self.assertIn("openai/gpt-5.6-luna", raised.exception.candidates)

    def test_search_is_stable_and_provider_filterable(self) -> None:
        catalog = fixture_catalog()

        hits = catalog.search("luna", provider="openai", limit=5)
        self.assertEqual(
            ["openai/gpt-5.6-luna"], [hit.model.qualified_id for hit in hits]
        )
        self.assertGreater(hits[0].score, 0.7)

        aliased = catalog.search("openai/luna", limit=3)
        self.assertEqual("openai/gpt-5.6-luna", aliased[0].model.qualified_id)
        self.assertEqual(1.0, aliased[0].score)

    def test_overlay_adds_role_without_rewriting_catalog(self) -> None:
        catalog = fixture_catalog(
            include_default_overlays=False,
            overlays=[{"roles": {"vision": "openai/gpt-5.6-luna"}}],
        )

        self.assertEqual("openai/gpt-5.6-luna", catalog.snapshot.roles["vision"])
        self.assertNotIn("openai-codex", catalog.snapshot.providers)

    def test_cache_is_atomic_private_and_digest_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "nested" / "catalog.json"
            payload = json.dumps(fixture_raw()).encode()
            source = ModelsDevSource(
                cache_path=cache, transport=lambda url, timeout: payload
            )

            raw, fetched_at = source.sync()
            self.assertEqual(fixture_raw(), raw)
            self.assertTrue(fetched_at.endswith("Z"))
            if POSIX_PERMISSIONS:
                self.assertEqual(0o600, os.stat(cache).st_mode & 0o777)
            cached, _ = source.load_cache(max_age=timedelta(days=1))
            self.assertEqual(raw, cached)

            envelope = json.loads(cache.read_text(encoding="utf-8"))
            envelope["catalog"]["openai"]["name"] = "tampered"
            cache.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaises(CatalogError):
                source.load_cache()


if __name__ == "__main__":
    unittest.main()
