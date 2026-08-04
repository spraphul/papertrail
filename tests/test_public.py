from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from papertrail.cli import main, parser
from papertrail.config import Settings
from papertrail.intelligence import ResearchIntelligence
from papertrail.organization import organize_snapshot
from papertrail.profile import configure_runtime, load_profile, save_profile
from papertrail.service import PaperTrail


PAPER = """Abstract

We test reliable agents under changing tool interfaces and delayed responses.

Experiments

The recovery controller completes 72 percent of tasks after a tool failure. The
verifier-only baseline completes 58 percent under the same compute budget.

Limitations

The tool failures are simulated and the benchmark contains only five interfaces.
Performance on real services remains unknown.
"""


class FakeProvider:
    provider_name = "fake-local"
    embedding_model = "fake-embedding"
    reasoning_model = "fake-reasoning"

    def health(self) -> dict:
        return {"available": True, "provider": self.provider_name}

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [float("recovery" in text.casefold()), float("failure" in text.casefold()), 1.0]
            for text in texts
        ]

    def structured(self, *, system: str, prompt: str, schema: dict) -> dict:
        if "candidate" in json.dumps(schema).casefold():
            return {"candidates": []}
        return {"records": []}


class PublicPaperTrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.service = PaperTrail(Settings(self.home))
        self.service.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ingest(self) -> dict:
        return self.service.ingest_text(
            PAPER,
            title="Recovery-Aware Agents",
            authors=["Ada Example"],
            published_date="2026-07-15",
            source_url="https://example.org/recovery-aware",
        )

    def test_ingestion_is_idempotent_and_search_is_evidence_bound(self) -> None:
        first = self.ingest()
        second = self.ingest()
        self.assertEqual(first["paper_id"], second["paper_id"])
        result = self.service.search("tool failure")
        self.assertTrue(result["results"])
        self.assertEqual(
            {item["paper_id"] for item in result["results"]}, {first["paper_id"]}
        )
        self.assertTrue(result["results"][0]["evidence_id"].startswith("ev_"))

    def test_snapshot_excludes_later_papers(self) -> None:
        first = self.ingest()
        snapshot = self.service.create_snapshot("public-v1")
        self.service.ingest_text(
            "A later unrelated paper.",
            title="Later Paper",
            published_date="2026-07-16",
            source_url="https://example.org/later",
        )
        result = self.service.search("recovery", snapshot_id=snapshot["snapshot_id"])
        self.assertEqual({item["paper_id"] for item in result["results"]}, {first["paper_id"]})

    def test_local_intelligence_embeds_and_hybrid_searches(self) -> None:
        self.ingest()
        intelligence = ResearchIntelligence(self.service, FakeProvider())
        enriched = intelligence.enrich(extraction_types=())
        self.assertGreater(enriched["embedded_passages"], 0)
        result = intelligence.hybrid_search("recovery after failure", limit=5)
        self.assertTrue(result["results"])

    def test_local_profile_configures_ollama_models(self) -> None:
        profile_path = save_profile(
            self.home,
            {
                "profile": "local",
                "providers": {
                    "embedding_provider": "ollama",
                    "reasoning_provider": "ollama",
                    "embedding_model": "embeddinggemma",
                    "reasoning_model": "qwen2.5:7b",
                },
            },
        )
        self.assertEqual(profile_path.stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {}, clear=True):
            result = configure_runtime(self.home)
            self.assertEqual(os.environ["PAPERTRAIL_REASONING_PROVIDER"], "ollama")
            self.assertEqual(os.environ["PAPERTRAIL_REASONING_MODEL"], "qwen2.5:7b")
        self.assertEqual(result, {"profile": "local"})

    def test_setup_writes_local_daily_profile(self) -> None:
        output = StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("papertrail.cli.connect_agent", return_value={"status": "connected"}),
            patch("papertrail.cli.shutil.which", return_value="/usr/local/bin/codex"),
            patch("sys.stdout", output),
        ):
            main(
                [
                    "--home",
                    str(self.home),
                    "setup",
                    "--client",
                    "codex",
                    "--no-schedule",
                    "--no-dashboard",
                ]
            )
        configured = load_profile(self.home)
        self.assertEqual(configured["providers"]["reasoning_provider"], "ollama")
        self.assertEqual(configured["daily"]["category"], "cs.AI")
        self.assertIn('"profile": "local"', output.getvalue())

    def test_corpus_ingest_requires_an_explicit_bound(self) -> None:
        arguments = parser().parse_args(
            [
                "arxiv",
                "ingest",
                "--from-date",
                "2026-07-01",
                "--to-date",
                "2026-07-31",
                "--limit",
                "10",
            ]
        )
        self.assertEqual(arguments.limit, 10)
        self.assertFalse(arguments.all)

    def test_organization_creates_problem_neighborhoods(self) -> None:
        self.ingest()
        intelligence = ResearchIntelligence(self.service, FakeProvider())
        intelligence.enrich(extraction_types=())
        snapshot = self.service.create_snapshot("organized")
        result = organize_snapshot(
            self.service,
            snapshot["snapshot_id"],
            max_clusters=4,
            label_provider=FakeProvider(),
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["paper_count"], 1)


if __name__ == "__main__":
    unittest.main()
