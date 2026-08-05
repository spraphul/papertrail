from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from papertrail.citations import refresh_citations, scholarly_id
from papertrail.config import Settings
from papertrail.service import PaperTrail


class FakeCitationClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def papers(self, identifiers: list[str]) -> list[dict]:
        self.calls.append(identifiers)
        return [
            {
                "paperId": f"s2-{index}",
                "citationCount": 12 + index,
                "influentialCitationCount": 2,
                "referenceCount": 30,
            }
            for index, _ in enumerate(identifiers)
        ]


class CitationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.service = PaperTrail(Settings(Path(self.temporary.name) / "papertrail"))
        self.service.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_identifier_normalization_and_cached_refresh(self) -> None:
        self.assertEqual(
            scholarly_id("https://arxiv.org/abs/2508.01525v3"),
            ("ARXIV:2508.01525", "arxiv"),
        )
        paper_id = self.service.ingest_text(
            "Citation metadata fixture.",
            title="Citation-Aware Agents",
            source_url="https://arxiv.org/abs/2508.01525v3",
        )["paper_id"]
        client = FakeCitationClient()
        first = refresh_citations(self.service, paper_ids=[paper_id], client=client)
        self.assertEqual(first["refreshed"], 1)
        self.assertEqual(client.calls, [["ARXIV:2508.01525"]])
        second = refresh_citations(self.service, paper_ids=[paper_id], client=client)
        self.assertEqual(second["skipped_fresh"], 1)
        self.assertEqual(len(client.calls), 1)
        paper = self.service.get_paper(paper_id)["results"][0]
        self.assertEqual(paper["citation_count"], 12)

    def test_provider_failure_is_non_blocking(self) -> None:
        paper_id = self.service.ingest_text(
            "Failure fixture.",
            title="A Paper",
            source_url="https://arxiv.org/abs/2508.00001",
        )["paper_id"]

        class BrokenClient:
            def papers(self, identifiers: list[str]) -> list[dict]:
                raise RuntimeError("temporarily unavailable")

        result = refresh_citations(self.service, paper_ids=[paper_id], client=BrokenClient())
        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(result["warnings"], ["temporarily unavailable"])
