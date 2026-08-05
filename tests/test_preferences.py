from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from papertrail.arxiv_batch import ArxivBatchIngestor
from papertrail.config import Settings
from papertrail.daily_digest import (
    _personalization_profile,
    _upsert_run,
    _validate_and_store,
    get_blog,
)
from papertrail.db import initialize, transaction
from papertrail.preferences import (
    configure_preference_sources,
    forget_preferences,
    inspect_preferences,
    prioritize_discoveries,
    read_research_turns,
    set_preference_source,
    replace_explicit_interests,
    sync_preferences,
)
from papertrail.service import PaperTrail, stable_id, utc_now


class HistoryPreferenceProvider:
    provider_name = "preference-test"
    embedding_model = "preference-space"
    reasoning_model = "preference-extractor"

    def __init__(self) -> None:
        self.extraction_calls = 0
        self.relevance_calls = 0
        self.prompts: list[str] = []

    def health(self) -> dict:
        return {"available": True}

    def structured(self, *, system: str, prompt: str, schema: dict) -> dict:
        if "scores" in schema.get("properties", {}):
            self.relevance_calls += 1
            papers = json.loads(prompt)["papers"]
            return {
                "scores": [
                    {
                        "discovery_id": paper["discovery_id"],
                        "relevance": 0.95 if "agent" in paper["abstract"].casefold() else 0.1,
                        "reason": "Matches reliable adaptive agent mechanisms.",
                    }
                    for paper in papers
                ]
            }
        self.extraction_calls += 1
        self.prompts.append(prompt)
        return {
            "events": [
                {
                    "kind": "positive",
                    "label": "adaptive tool interfaces",
                    "context": "Research on agents that remain reliable as tools change.",
                    "confidence": 0.94,
                    "explicitness": "explicit",
                }
            ]
        }

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [0.0, 1.0] if "protein" in text.casefold() else [1.0, 0.0]
            for text in texts
        ]


class PreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.service = PaperTrail(Settings(self.home / "papertrail"))
        self.service.initialize()
        self.provider = HistoryPreferenceProvider()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_codex_history_sync_is_private_incremental_and_replaceable(self) -> None:
        history = self.home / "codex-history"
        history.mkdir()
        session = history / "session.jsonl"
        secret = "sk-super-secret-value-1234567890"
        session.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            "Research adaptive agents under changing tool "
                                            f"interfaces. My api_key={secret}"
                                        ),
                                    }
                                ],
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "The assistant suggests unrelated medicine.",
                                    }
                                ],
                            },
                        }
                    ),
                ]
            )
            + "\n"
        )
        configure_preference_sources(
            self.service,
            ["codex"],
            history_paths={"codex": str(history)},
        )

        first = sync_preferences(self.service, self.provider)
        self.assertEqual(first["processed_sessions"], 1)
        self.assertEqual(first["accepted_events"], 1)
        self.assertEqual(self.provider.extraction_calls, 1)
        self.assertIn("[REDACTED]", self.provider.prompts[0])
        self.assertNotIn(secret, self.provider.prompts[0])
        self.assertNotIn(secret.encode(), self.service.settings.database_path.read_bytes())

        second = sync_preferences(self.service, self.provider)
        self.assertEqual(second["processed_sessions"], 0)
        self.assertEqual(second["unchanged_sessions"], 1)
        self.assertEqual(self.provider.extraction_calls, 1)

        session.write_text(
            session.read_text()
            + json.dumps(
                {
                    "session_id": "session-1",
                    "text": "I also research robust agent evaluation and benchmark design.",
                }
            )
            + "\n"
        )
        third = sync_preferences(self.service, self.provider)
        self.assertEqual(third["processed_sessions"], 1)
        self.assertEqual(self.provider.extraction_calls, 2)
        with closing(sqlite3.connect(self.service.settings.database_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM preference_events").fetchone()[0], 1)

        profile = _personalization_profile(
            self.service, organization=None, enabled=True
        )
        self.assertTrue(profile["active"])
        self.assertEqual(profile["favorite_ids"], [])
        self.assertIn("adaptive tool interfaces", profile["preference_labels"])

    def test_dashboard_interests_are_editable_high_authority_and_clearable(self) -> None:
        first = replace_explicit_interests(
            self.service,
            "I care about adaptive tool interfaces and reliable agent evaluation.",
            self.provider,
        )
        self.assertEqual(first["explicit"]["extraction_status"], "ready")
        self.assertTrue(first["profile"]["active_for_ingestion"])
        self.assertTrue(first["profile"]["explicit_note_active"])
        self.assertEqual(
            inspect_preferences(self.service)["explicit"]["text"],
            "I care about adaptive tool interfaces and reliable agent evaluation.",
        )

        edited = replace_explicit_interests(
            self.service,
            "I now prefer protein language model research.",
            None,
        )
        self.assertEqual(edited["explicit"]["extraction_status"], "pending")
        self.assertIn("protein language model", " ".join(edited["profile"]["positive_labels"]))

        cleared = replace_explicit_interests(self.service, "", self.provider)
        self.assertEqual(cleared["explicit"]["extraction_status"], "empty")
        self.assertFalse(cleared["profile"]["explicit_note_active"])

    def test_claude_adapter_reads_user_turns_only(self) -> None:
        path = self.home / "claude.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Find research papers about protein language models.",
                                    }
                                ],
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {"type": "text", "text": "An assistant-only preference."}
                                ],
                            },
                        }
                    ),
                ]
            )
            + "\n"
        )
        self.assertEqual(
            read_research_turns("claude", path),
            ["Find research papers about protein language models."],
        )

    def test_existing_daily_blog_database_adds_preference_label_provenance(self) -> None:
        database = self.home / "legacy.db"
        with closing(sqlite3.connect(database)) as db:
            db.execute(
                """
                CREATE TABLE daily_blog_personalization (
                    blog_id TEXT PRIMARY KEY,
                    selection_mode TEXT NOT NULL,
                    selection_reason TEXT NOT NULL,
                    matched_favorite_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            db.commit()
        initialize(database)
        with closing(sqlite3.connect(database)) as db:
            columns = {
                row[1]
                for row in db.execute(
                    "PRAGMA table_info(daily_blog_personalization)"
                ).fetchall()
            }
        self.assertIn("matched_preference_labels_json", columns)

    def test_chat_interest_can_explain_a_preference_deep_dive(self) -> None:
        history = self.home / "codex.jsonl"
        history.write_text(
            json.dumps(
                {
                    "session_id": "deep-dive",
                    "text": "I research adaptive agents under changing tool interfaces.",
                }
            )
            + "\n"
        )
        configure_preference_sources(
            self.service,
            ["codex"],
            history_paths={"codex": str(history)},
        )
        sync_preferences(self.service, self.provider)
        paper = self.service.ingest_text(
            "Agents repair calls after tools rename required arguments.",
            title="Repairing Calls Under Tool Drift",
            abstract="Adaptive tool interfaces for reliable agents.",
            source_url="https://example.test/tool-drift",
        )
        evidence_id = self.service.search("tools rename")["results"][0]["evidence_id"]
        profile = _personalization_profile(self.service, organization=None, enabled=True)
        run_id = "chat-personalized-digest"
        _upsert_run(
            self.service,
            run_id,
            run_date=date.today().isoformat(),
            snapshot_id="chat-profile-snapshot",
            agent_client="codex",
            agent_model=None,
            status="running",
            candidate_ids=[paper["paper_id"]],
        )
        output = {
            "headline": "Adaptive systems",
            "synthesis": "A preference-aligned reading.",
            "trends": ["Tool adaptation"],
            "blogs": [
                {
                    "paper_id": paper["paper_id"],
                    "title": "Repairing Calls Under Tool Drift",
                    "dek": "An evidence-grounded deep dive.",
                    "surprise": "Repair can occur without retraining the full agent.",
                    "selection_mode": "preference",
                    "selection_reason": (
                        "Matches the durable chat interest in adaptive tool interfaces."
                    ),
                    "matched_favorite_ids": [],
                    "matched_preference_labels": ["adaptive tool interfaces"],
                    "markdown": " ".join(["analysis"] * 700) + f" [{evidence_id}]",
                    "evidence_ids": [evidence_id],
                    "figure_ids": [],
                    "themes": ["agents"],
                    "related_paper_ids": [],
                }
            ],
        }
        saved = _validate_and_store(
            self.service,
            run_id,
            output,
            candidate_ids={paper["paper_id"]},
            required_blog_count=1,
            personalization=profile,
        )
        blog = get_blog(self.service, saved["blogs"][0]["slug"])
        self.assertEqual(blog["matched_favorite_ids"], [])
        self.assertEqual(blog["matched_preference_labels"], ["adaptive tool interfaces"])

    def test_forget_removes_derived_signals_and_consent(self) -> None:
        history = self.home / "history.jsonl"
        history.write_text(
            json.dumps(
                {
                    "session_id": "one",
                    "text": "I research reliable agents and changing tool interfaces.",
                }
            )
            + "\n"
        )
        configure_preference_sources(
            self.service,
            ["codex"],
            history_paths={"codex": str(history)},
        )
        sync_preferences(self.service, self.provider)
        set_preference_source(self.service, "codex", False)
        self.assertTrue(inspect_preferences(self.service)["profile"]["active"])
        result = forget_preferences(self.service, "codex")
        self.assertEqual(result["deleted_events"], 1)
        inspected = inspect_preferences(self.service)
        self.assertFalse(inspected["profile"]["active"])
        source = next(item for item in inspected["sources"] if item["source"] == "codex")
        self.assertFalse(source["enabled"])
        self.assertIsNone(source["consented_at"])

    def test_personalized_priority_preserves_frontier_and_exploration(self) -> None:
        for index in range(3):
            paper = self.service.ingest_text(
                "Agents adapt tool calls when external interfaces change.",
                title=f"Adaptive Tool Agent {index}",
                abstract="Reliable agents under changing tool schemas.",
                source_url=f"https://example.test/favorite-{index}",
            )
            self.service.set_favorite(paper["paper_id"], True)

        created = utc_now()
        group_id = "group-priority"
        run_id = "run-priority"
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "INSERT INTO ingestion_runs (id, source, query_json, status, created_at, updated_at) "
                "VALUES (?, 'arxiv', ?, 'discovered', ?, ?)",
                (
                    run_id,
                    json.dumps({"category": "cs.AI"}),
                    created,
                    created,
                ),
            )
            db.execute(
                "INSERT INTO ingestion_groups VALUES (?, 'arxiv', ?, 'discovered', ?, ?)",
                (
                    group_id,
                    json.dumps({"category": "cs.AI"}),
                    created,
                    created,
                ),
            )
            db.execute("INSERT INTO ingestion_group_runs VALUES (?, ?, 0)", (group_id, run_id))
            abstracts = [
                "Adaptive agents repair calls after tool schemas change.",
                "Tool-interface recovery for reliable language model agents.",
                "Agents learn renamed API arguments during deployment.",
                "Protein language models generate stable enzyme structures.",
                "A novel theorem for distributed optimization convergence.",
                "Robot policies transfer across tactile sensor hardware.",
            ]
            for index, abstract in enumerate(abstracts):
                discovery_id = stable_id("discovery", run_id, str(index))
                db.execute(
                    """
                    INSERT INTO discovery_records (
                        id, run_id, source, source_id, title, abstract, authors_json,
                        categories_json, primary_category, published_date, updated_at,
                        abstract_url, pdf_url, status, discovered_at
                    ) VALUES (?, ?, 'arxiv', ?, ?, ?, '[]', '["cs.AI"]', 'cs.AI', ?, ?, ?, ?,
                              'discovered', ?)
                    """,
                    (
                        discovery_id,
                        run_id,
                        f"2608.{index:05d}",
                        f"Candidate {index}",
                        abstract,
                        date.today().isoformat(),
                        created,
                        f"https://arxiv.org/abs/2608.{index:05d}",
                        f"https://arxiv.org/pdf/2608.{index:05d}",
                        created,
                    ),
                )

        result = prioritize_discoveries(
            self.service,
            self.provider,
            group_id=group_id,
            budget=5,
        )
        self.assertTrue(result["profile"]["active_for_ingestion"])
        self.assertEqual(len(result["selected_discovery_ids"]), 5)
        self.assertGreaterEqual(result["lane_counts"].get("preference", 0), 1)
        self.assertGreaterEqual(result["lane_counts"].get("frontier", 0), 1)
        self.assertGreaterEqual(result["lane_counts"].get("exploration", 0), 1)
        self.assertEqual(result["llm_relevance_count"], 6)
        self.assertEqual(self.provider.relevance_calls, 1)
        with closing(sqlite3.connect(self.service.settings.database_path)) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM discovery_records").fetchone()[0], 6
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM paper_priority_scores").fetchone()[0], 6
            )
        selected_id = result["selected_discovery_ids"][0]
        ingestor = ArxivBatchIngestor(self.service)
        with (
            patch.object(ingestor, "_download_pdf", return_value=b"%PDF-test"),
            patch.object(
                ingestor,
                "_ingest_downloaded_pdf",
                return_value={"paper_id": "paper-selected", "status": "ingested"},
            ),
        ):
            acquisition = ingestor.acquire(
                run_id,
                discovery_ids=[selected_id],
                retry_failed=True,
                min_free_gb=0,
                workers=1,
            )
        self.assertEqual(acquisition["acquired_count"], 1)
        with closing(sqlite3.connect(self.service.settings.database_path)) as db:
            statuses = dict(
                db.execute(
                    "SELECT status, count(*) FROM discovery_records GROUP BY status"
                ).fetchall()
            )
        self.assertEqual(statuses, {"acquired": 1, "discovered": 5})


if __name__ == "__main__":
    unittest.main()
