from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from papertrail.cli import main, parser
from papertrail.config import Settings, settings
from papertrail.intelligence import ResearchIntelligence
from papertrail.organization import organize_snapshot
from papertrail.profile import configure_runtime, load_profile, save_profile
from papertrail.providers import OpenAIProvider
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


class ClusteringProvider:
    provider_name = "fake-llm"
    embedding_model = "fake-shared-space"
    reasoning_model = "fake-taxonomist"

    def __init__(self) -> None:
        self.cluster_dossiers: list[dict] = []

    def health(self) -> dict:
        return {"available": True, "provider": self.provider_name}

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.5, 0.25] for _ in texts]

    def structured(self, *, system: str, prompt: str, schema: dict) -> dict:
        if "groups" in schema.get("properties", {}):
            papers = json.loads(prompt.split("\n\n", 1)[1])["papers"]
            self.cluster_dossiers = papers
            agent_ids = [
                paper["paper_id"] for paper in papers if "Protein" not in paper["title"]
            ]
            protein_ids = [paper["paper_id"] for paper in papers if "Protein" in paper["title"]]
            return {
                "groups": [
                    {
                        "title": "Recovering Tool-Using Agents",
                        "description": "Agents that recover when external tool interfaces change.",
                        "shared_problem": "Maintaining agent reliability under tool-interface drift.",
                        "paper_ids": agent_ids,
                        "membership_confidence": 0.94,
                    },
                    {
                        "title": "Protein Structure Generation",
                        "description": "A distinct singleton concerned with protein generation.",
                        "shared_problem": "Generating stable protein structures.",
                        "paper_ids": protein_ids,
                        "membership_confidence": 0.99,
                    },
                ]
            }
        evidence_ids = re.findall(r"ev_[a-f0-9]+", prompt)
        return {
            "records": [
                {
                    "title": "Primary contribution",
                    "statement": "The paper introduces a mechanism tailored to its stated problem.",
                    "conditions": [],
                    "numeric_values": [],
                    "evidence_ids": evidence_ids[:1],
                    "confidence": 0.9,
                }
            ]
        }


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

    def test_openai_embeddings_are_batched_and_ordered_by_index(self) -> None:
        provider = OpenAIProvider(
            api_key="test-secret",
            base_url="https://models.example.test/v1",
            embedding_model="my-embedding-model",
            reasoning_model="my-reasoning-model",
        )
        response = {
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ]
        }
        with patch.object(provider, "_request", return_value=response) as request:
            vectors = provider.embed(["first paper", "second paper"])
        self.assertEqual(vectors, [[0.1, 0.2], [0.3, 0.4]])
        path, payload = request.call_args.args
        self.assertEqual(path, "/embeddings")
        self.assertEqual(payload["input"], ["first paper", "second paper"])
        self.assertEqual(payload["model"], "my-embedding-model")

    def test_openai_reasoning_uses_responses_structured_outputs(self) -> None:
        provider = OpenAIProvider(
            api_key="test-secret",
            reasoning_model="bring-your-own-model",
        )
        response = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"records": []}'}],
                }
            ],
        }
        schema = {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"claim": {"type": "string"}},
                        "required": ["claim"],
                    },
                }
            },
            "required": ["records"],
        }
        with patch.object(provider, "_request", return_value=response) as request:
            result = provider.structured(system="Extract evidence", prompt="Paper", schema=schema)
        self.assertEqual(result, {"records": []})
        path, payload = request.call_args.args
        self.assertEqual(path, "/responses")
        self.assertEqual(payload["model"], "bring-your-own-model")
        self.assertEqual([item["role"] for item in payload["input"]], ["system", "user"])
        self.assertFalse(payload["store"])
        strict = payload["text"]["format"]
        self.assertTrue(strict["strict"])
        self.assertFalse(strict["schema"]["additionalProperties"])
        self.assertFalse(
            strict["schema"]["properties"]["records"]["items"]["additionalProperties"]
        )
        self.assertNotIn("additionalProperties", schema)

    def test_openai_key_file_is_loaded_without_entering_profile(self) -> None:
        key_file = self.home / "openai-key"
        key_file.write_text("test-secret\n")
        save_profile(
            self.home,
            {
                "profile": "local",
                "providers": {
                    "embedding_provider": "openai",
                    "reasoning_provider": "openai",
                    "embedding_model": "arbitrary-embedding-model",
                    "reasoning_model": "arbitrary-reasoning-model",
                    "openai_base_url": "https://api.openai.com/v1",
                    "openai_api_key_file": str(key_file),
                },
            },
        )
        with patch.dict(os.environ, {}, clear=True):
            configure_runtime(self.home)
            configured = settings(self.home)
        self.assertEqual(configured.openai_api_key, "test-secret")
        self.assertEqual(configured.embedding_model, "arbitrary-embedding-model")
        self.assertNotIn("test-secret", (self.home / "profile.json").read_text())

    def test_openai_health_never_returns_the_api_key(self) -> None:
        provider = OpenAIProvider(api_key="test-secret")
        with patch.object(provider, "_request", return_value={"data": []}):
            result = provider.health()
        self.assertTrue(result["available"])
        self.assertNotIn("test-secret", json.dumps(result))

    def test_setup_accepts_arbitrary_openai_models_without_storing_key(self) -> None:
        output = StringIO()
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}, clear=True),
            patch("papertrail.cli.connect_agent", return_value={"status": "connected"}),
            patch("papertrail.cli.shutil.which", return_value="/usr/local/bin/codex"),
            patch("sys.stdout", output),
        ):
            main(
                [
                    "--home",
                    str(self.home),
                    "setup",
                    "--embedding-provider",
                    "openai",
                    "--embedding-model",
                    "account-embedding-model",
                    "--reasoning-provider",
                    "openai",
                    "--reasoning-model",
                    "account-reasoning-model",
                    "--no-schedule",
                    "--no-dashboard",
                ]
            )
        configured_text = (self.home / "profile.json").read_text()
        configured = json.loads(configured_text)
        self.assertEqual(
            configured["providers"]["reasoning_model"], "account-reasoning-model"
        )
        self.assertNotIn("test-secret", configured_text)

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

    def test_llm_clustering_splits_topical_false_positive_with_rich_context(self) -> None:
        provider = ClusteringProvider()
        self.service = PaperTrail(
            Settings(
                self.home,
                intelligence_provider=provider.provider_name,
                embedding_provider=provider.provider_name,
                reasoning_provider=provider.provider_name,
                embedding_model=provider.embedding_model,
                reasoning_model=provider.reasoning_model,
            )
        )
        self.service.initialize()
        papers = [
            (
                "Tool Agent Recovery",
                "We recover autonomous agents after tool schemas change during deployment.",
            ),
            (
                "Agent Interface Adaptation",
                "We adapt tool-using agents to renamed arguments and delayed responses.",
            ),
            (
                "Protein Agent Generation",
                "We use a generative agent to design stable protein structures.",
            ),
        ]
        for index, (title, abstract) in enumerate(papers):
            self.service.ingest_text(
                f"Abstract\n{abstract}\nMethod\nA specialized mechanism solves this problem.",
                title=title,
                abstract=abstract,
                published_date=f"2026-07-{20 + index:02d}",
                source_url=f"https://example.org/paper-{index}",
            )
        ResearchIntelligence(self.service, provider).enrich(extraction_types=("contribution",))
        snapshot = self.service.create_snapshot("llm-organized")
        result = organize_snapshot(
            self.service,
            snapshot["snapshot_id"],
            max_clusters=2,
            similarity_threshold=0.5,
            label_provider=provider,
        )
        groups = {group["label"]: group for group in result["groups"]}
        self.assertEqual(result["cluster_count"], 2)
        self.assertIn("Recovering Tool-Using Agents", groups, result)
        self.assertEqual(groups["Recovering Tool-Using Agents"]["paper_count"], 2)
        self.assertEqual(groups["Protein Structure Generation"]["paper_count"], 1)
        self.assertEqual(result["configuration"]["llm_refinement"]["calls"], 1)
        self.assertTrue(all(paper["abstract"] for paper in provider.cluster_dossiers))
        self.assertTrue(
            all(paper["scientific_records"] for paper in provider.cluster_dossiers)
        )


if __name__ == "__main__":
    unittest.main()
