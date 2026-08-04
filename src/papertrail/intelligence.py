from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from typing import Any, Callable

from .db import connect, transaction
from .providers import IntelligenceProvider, provider_from_settings
from .service import PaperTrail, stable_id, utc_now


EXTRACTION_TYPES = (
    "contribution",
    "method",
    "assumption",
    "empirical_result",
    "limitation",
    "future_work",
)

RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "statement": {"type": "string"},
                    "conditions": {"type": "array", "items": {"type": "string"}},
                    "numeric_values": {"type": "array", "items": {"type": "string"}},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "title",
                    "statement",
                    "conditions",
                    "numeric_values",
                    "evidence_ids",
                    "confidence",
                ],
            },
        }
    },
    "required": ["records"],
}

NOVELTY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "overlap_dimensions": {"type": "array", "items": {"type": "string"}},
        "differentiating_dimensions": {"type": "array", "items": {"type": "string"}},
        "unresolved_limitations": {"type": "array", "items": {"type": "string"}},
        "potential_counterevidence": {"type": "array", "items": {"type": "string"}},
        "recommended_experiments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "design": {"type": "string"},
                    "falsifies": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "design", "falsifies", "evidence_ids"],
            },
        },
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "summary",
        "overlap_dimensions",
        "differentiating_dimensions",
        "unresolved_limitations",
        "potential_counterevidence",
        "recommended_experiments",
        "evidence_ids",
        "confidence",
    ],
}

DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "gap": {"type": "string"},
                    "falsifying_experiment": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "risk": {"type": "string"},
                },
                "required": [
                    "title",
                    "hypothesis",
                    "mechanism",
                    "gap",
                    "falsifying_experiment",
                    "evidence_ids",
                    "risk",
                ],
            },
        }
    },
    "required": ["candidates"],
}


class ResearchIntelligence:
    def __init__(
        self,
        service: PaperTrail,
        provider: IntelligenceProvider,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.service = service
        self.provider = provider
        self.progress = progress or (lambda event: None)
        self.service.initialize()

    @property
    def embedding_key(self) -> str:
        provider = getattr(
            self.provider, "embedding_provider_name", self.provider.provider_name
        )
        return f"{provider}:{self.provider.embedding_model}"

    @property
    def reasoning_key(self) -> str:
        provider = getattr(
            self.provider, "reasoning_provider_name", self.provider.provider_name
        )
        return f"{provider}:{self.provider.reasoning_model}"

    @classmethod
    def from_settings(
        cls,
        service: PaperTrail,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> "ResearchIntelligence":
        return cls(service, provider_from_settings(service.settings), progress)

    def enrich(
        self,
        *,
        paper_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int | None = None,
        extraction_types: tuple[str, ...] = EXTRACTION_TYPES,
        skip_completed: bool = False,
    ) -> dict[str, Any]:
        invalid = set(extraction_types) - set(EXTRACTION_TYPES)
        if invalid:
            raise ValueError(f"Unknown extraction types: {', '.join(sorted(invalid))}")
        with closing(connect(self.service.settings.database_path)) as db:
            clauses = ["v.is_current = 1"]
            params: list[Any] = []
            if paper_id:
                clauses.append("p.id = ?")
                params.append(paper_id)
            if from_date:
                clauses.append("p.published_date >= ?")
                params.append(from_date)
            if to_date:
                clauses.append("p.published_date <= ?")
                params.append(to_date)
            limit_sql = ""
            if limit is not None:
                if limit < 1:
                    raise ValueError("limit must be at least 1")
                limit_sql = " LIMIT ?"
                params.append(limit)
            papers = db.execute(
                f"""
                SELECT p.id, p.canonical_title, p.abstract, v.id AS paper_version_id
                FROM papers p JOIN paper_versions v ON v.paper_id = p.id
                WHERE {' AND '.join(clauses)} ORDER BY p.published_date, p.created_at
                {limit_sql}
                """,
                params,
            ).fetchall()
        if paper_id and not papers:
            raise KeyError(f"Unknown paper: {paper_id}")

        embedded = 0
        extracted = 0
        rejected = 0
        passages_by_version = {
            paper["paper_version_id"]: self._passages_for_version(
                paper["paper_version_id"]
            )
            for paper in papers
        }
        if len(papers) > 1:
            embedded = self._embed_missing_bulk(passages_by_version)
        for paper in papers:
            passages = passages_by_version[paper["paper_version_id"]]
            if len(papers) == 1:
                embedded += self._embed_missing(passages, paper["paper_version_id"])
            for record_type in extraction_types:
                if skip_completed and self._extraction_complete(
                    paper["paper_version_id"], record_type
                ):
                    continue
                accepted, invalid_count = self._extract_type(dict(paper), passages, record_type)
                extracted += accepted
                rejected += invalid_count
        return {
            "status": "enriched",
            "paper_count": len(papers),
            "embedded_passages": embedded,
            "accepted_records": extracted,
            "rejected_records": rejected,
            "embedding_model": self.provider.embedding_model,
            "reasoning_model": self.provider.reasoning_model,
            "provider": self.provider.provider_name,
            "extraction_types": list(extraction_types),
        }

    def _extraction_complete(self, paper_version_id: str, record_type: str) -> bool:
        with closing(connect(self.service.settings.database_path)) as db:
            return db.execute(
                """
                SELECT 1 FROM scientific_extractions
                WHERE paper_version_id = ? AND record_type = ? AND extractor_version = ?
                """,
                (paper_version_id, record_type, self.reasoning_key),
            ).fetchone() is not None

    def _passages_for_version(self, version_id: str) -> list[dict[str, Any]]:
        with closing(connect(self.service.settings.database_path)) as db:
            return [
                dict(row)
                for row in db.execute(
                    """
                    SELECT e.id AS evidence_id, e.text, s.heading AS section
                    FROM evidence_passages e JOIN sections s ON s.id = e.section_id
                    WHERE e.paper_version_id = ? ORDER BY s.ordinal, e.id
                    """,
                    (version_id,),
                )
            ]

    def _embed_missing(self, passages: list[dict[str, Any]], version_id: str) -> int:
        if not passages:
            return 0
        with closing(connect(self.service.settings.database_path)) as db:
            existing = {
                row[0]
                for row in db.execute(
                    "SELECT record_id FROM embeddings WHERE model = ? AND paper_version_id = ?",
                    (self.embedding_key, version_id),
                )
            }
        missing = [item for item in passages if item["evidence_id"] not in existing]
        for start in range(0, len(missing), 32):
            batch = missing[start : start + 32]
            vectors = self.provider.embed([item["text"] for item in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding batch size mismatch")
            with transaction(self.service.settings.database_path) as db:
                for item, vector in zip(batch, vectors, strict=True):
                    db.execute(
                        "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            item["evidence_id"],
                            version_id,
                            self.embedding_key,
                            len(vector),
                            json.dumps(vector, separators=(",", ":")),
                            utc_now(),
                        ),
                    )
        return len(missing)

    def _embed_missing_bulk(
        self, passages_by_version: dict[str, list[dict[str, Any]]]
    ) -> int:
        with closing(connect(self.service.settings.database_path)) as db:
            existing = {
                row[0]
                for row in db.execute(
                    "SELECT record_id FROM embeddings WHERE model = ?",
                    (self.embedding_key,),
                )
            }
        missing = [
            {**item, "paper_version_id": version_id}
            for version_id, passages in passages_by_version.items()
            for item in passages
            if item["evidence_id"] not in existing
        ]
        for start in range(0, len(missing), 128):
            batch = missing[start : start + 128]
            vectors = self.provider.embed([item["text"] for item in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding batch size mismatch")
            with transaction(self.service.settings.database_path) as db:
                for item, vector in zip(batch, vectors, strict=True):
                    db.execute(
                        "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            item["evidence_id"],
                            item["paper_version_id"],
                            self.embedding_key,
                            len(vector),
                            json.dumps(vector, separators=(",", ":")),
                            utc_now(),
                        ),
                    )
            self.progress(
                {
                    "stage": "embeddings",
                    "completed_passages": min(start + len(batch), len(missing)),
                    "total_passages": len(missing),
                    "model": self.embedding_key,
                }
            )
        return len(missing)

    def _extract_type(
        self,
        paper: dict[str, Any],
        passages: list[dict[str, Any]],
        record_type: str,
    ) -> tuple[int, int]:
        evidence = "\n\n".join(
            f"[{item['evidence_id']}] SECTION={item['section']}\n{item['text'][:2400]}"
            for item in passages[:80]
        )
        prompt = f"""
Paper: {paper['canonical_title']}
Abstract: {paper['abstract']}

Extract only {record_type.replace('_', ' ')} records directly supported by the evidence below.
Do not infer absent facts. Every record must cite one or more bracketed evidence IDs.
For empirical results, preserve conditions and every important numeric value exactly.
Return an empty records array when the evidence is insufficient.

EVIDENCE
{evidence}
""".strip()
        output = self.provider.structured(
            system=(
                "You extract scientific records. Source evidence and machine interpretation are different "
                "epistemic levels. Never create evidence IDs or upgrade an interpretation into a fact."
            ),
            prompt=prompt,
            schema=RECORD_SCHEMA,
        )
        allowed_evidence = {item["evidence_id"]: item["text"] for item in passages}
        accepted = 0
        rejected = 0
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "DELETE FROM record_evidence WHERE record_id IN (SELECT id FROM scientific_records WHERE paper_version_id = ? AND record_type = ? AND extractor_version = ?)",
                (paper["paper_version_id"], record_type, self.reasoning_key),
            )
            db.execute(
                "DELETE FROM scientific_records WHERE paper_version_id = ? AND record_type = ? AND extractor_version = ?",
                (paper["paper_version_id"], record_type, self.reasoning_key),
            )
            for candidate in output.get("records", []):
                if not isinstance(candidate, dict):
                    rejected += 1
                    continue
                evidence_ids = list(
                    dict.fromkeys(
                        str(item).strip().removeprefix("[").removesuffix("]")
                        for item in candidate.get("evidence_ids", [])
                    )
                )
                numeric_values = [str(item) for item in candidate.get("numeric_values", [])]
                evidence_text = " ".join(allowed_evidence.get(item, "") for item in evidence_ids)
                valid = (
                    bool(candidate.get("statement"))
                    and bool(evidence_ids)
                    and all(item in allowed_evidence for item in evidence_ids)
                    and all(value in evidence_text for value in numeric_values)
                )
                if not valid:
                    rejected += 1
                    continue
                confidence = max(0.0, min(1.0, float(candidate.get("confidence", 0.0))))
                record_id = stable_id(
                    "record",
                    paper["paper_version_id"],
                    record_type,
                    candidate["statement"],
                    *sorted(evidence_ids),
                )
                db.execute(
                    "INSERT INTO scientific_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record_id,
                        paper["id"],
                        paper["paper_version_id"],
                        record_type,
                        candidate.get("title", record_type.replace("_", " ").title()),
                        candidate["statement"],
                        json.dumps(candidate.get("conditions", {}), sort_keys=True),
                        json.dumps(numeric_values),
                        confidence,
                        self.reasoning_key,
                        utc_now(),
                    ),
                )
                db.executemany(
                    "INSERT OR IGNORE INTO record_evidence VALUES (?, ?, 'supports')",
                    [(record_id, item) for item in evidence_ids],
                )
                accepted += 1
            db.execute(
                """
                INSERT INTO scientific_extractions VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_version_id, record_type, extractor_version) DO UPDATE SET
                    accepted_count = excluded.accepted_count,
                    rejected_count = excluded.rejected_count,
                    completed_at = excluded.completed_at
                """,
                (
                    paper["paper_version_id"],
                    record_type,
                    self.reasoning_key,
                    accepted,
                    rejected,
                    utc_now(),
                ),
            )
        return accepted, rejected

    def hybrid_search(
        self,
        query: str,
        *,
        snapshot_id: str | None = None,
        include_synthetic: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        lexical = self.service.search(
            query,
            snapshot_id=snapshot_id,
            include_synthetic=include_synthetic,
            limit=100,
        )
        lexical_results = lexical["results"]
        query_vector = self.provider.embed([query])[0]
        dense_scored, vector_accelerator = self._dense_search(
            query_vector, snapshot_id, include_synthetic, limit=100
        )
        embedded_passages = self._embedding_count(snapshot_id, include_synthetic)
        ranks: dict[str, float] = defaultdict(float)
        reasons: dict[str, list[str]] = defaultdict(list)
        for rank, item in enumerate(lexical_results, 1):
            ranks[item["evidence_id"]] += 1.0 / (60 + rank)
            reasons[item["evidence_id"]].append(f"lexical rank {rank}")
        for rank, (similarity, item) in enumerate(dense_scored, 1):
            evidence_id = item["record_id"]
            ranks[evidence_id] += 1.0 / (60 + rank)
            reasons[evidence_id].append(f"semantic similarity {similarity:.3f}")
        ordered_ids = [item[0] for item in sorted(ranks.items(), key=lambda item: item[1], reverse=True)[:limit]]
        evidence = self.service.get_evidence(ordered_ids)["results"] if ordered_ids else []
        by_id = {item["evidence_id"]: item for item in evidence}
        results = []
        for evidence_id in ordered_ids:
            if evidence_id not in by_id:
                continue
            item = by_id[evidence_id]
            item["fusion_score"] = round(ranks[evidence_id], 8)
            item["match_reason"] = reasons[evidence_id]
            results.append(item)
        lexical["results"] = results
        lexical["provenance"]["retriever_version"] = "fts5+dense+rrf:v1"
        lexical["provenance"]["intelligence_provider"] = self.provider.provider_name
        lexical["provenance"]["embedding_model"] = self.provider.embedding_model
        lexical["provenance"]["vector_accelerator"] = vector_accelerator
        eligible_passages = self._eligible_passage_count(snapshot_id, include_synthetic)
        lexical["coverage"]["semantic_index"] = {
            "model": self.embedding_key,
            "embedded_passages": embedded_passages,
            "eligible_passages": eligible_passages,
            "complete": embedded_passages == eligible_passages,
        }
        if not embedded_passages:
            lexical["warnings"].append("No stored embeddings were available; run `papertrail enrich`.")
        elif embedded_passages < eligible_passages:
            lexical["warnings"].append(
                f"Semantic coverage is partial: {embedded_passages} of {eligible_passages} "
                "eligible passages have embeddings for this model."
            )
        return lexical

    def _embedding_count(
        self, snapshot_id: str | None, include_synthetic: bool
    ) -> int:
        with closing(connect(self.service.settings.database_path)) as db:
            params: list[Any] = [self.embedding_key, int(include_synthetic)]
            if snapshot_id:
                join = """
                JOIN snapshot_embeddings se
                  ON se.record_id = em.record_id
                 AND se.model = em.model
                 AND se.snapshot_id = ?
                """
                params = [snapshot_id, *params]
            else:
                join = "JOIN paper_versions v ON v.id = e.paper_version_id AND v.is_current = 1"
            return db.execute(
                f"""
                SELECT count(*) FROM embeddings em
                JOIN evidence_passages e ON e.id = em.record_id
                JOIN papers p ON p.id = e.paper_id
                {join}
                WHERE em.model = ? AND (? OR p.source_class != 'synthetic')
                """,
                params,
            ).fetchone()[0]

    def _eligible_passage_count(
        self, snapshot_id: str | None, include_synthetic: bool
    ) -> int:
        with closing(connect(self.service.settings.database_path)) as db:
            if snapshot_id:
                return db.execute(
                    """
                    SELECT count(*) FROM evidence_passages e
                    JOIN papers p ON p.id = e.paper_id
                    JOIN snapshot_papers sp
                      ON sp.paper_id = e.paper_id
                     AND sp.paper_version_id = e.paper_version_id
                    WHERE sp.snapshot_id = ?
                      AND (? OR p.source_class != 'synthetic')
                    """,
                    (snapshot_id, int(include_synthetic)),
                ).fetchone()[0]
            return db.execute(
                """
                SELECT count(*) FROM evidence_passages e
                JOIN papers p ON p.id = e.paper_id
                JOIN paper_versions v ON v.id = e.paper_version_id AND v.is_current = 1
                WHERE (? OR p.source_class != 'synthetic')
                """,
                (int(include_synthetic),),
            ).fetchone()[0]

    def _dense_candidates(
        self, snapshot_id: str | None, include_synthetic: bool = False
    ) -> list[Any]:
        with closing(connect(self.service.settings.database_path)) as db:
            params: list[Any] = [self.embedding_key, int(include_synthetic)]
            if snapshot_id:
                join = """
                JOIN snapshot_embeddings se
                  ON se.record_id = em.record_id
                 AND se.model = em.model
                 AND se.snapshot_id = ?
                """
                params.append(snapshot_id)
            else:
                join = "JOIN paper_versions v ON v.id = e.paper_version_id AND v.is_current = 1"
            return db.execute(
                f"""
                SELECT em.record_id, em.vector_json
                FROM embeddings em
                JOIN evidence_passages e ON e.id = em.record_id
                JOIN papers p ON p.id = e.paper_id
                {join}
                WHERE em.model = ? AND (? OR p.source_class != 'synthetic')
                """,
                [params[2], params[0], params[1]] if snapshot_id else params,
            ).fetchall()

    def _dense_search(
        self,
        query_vector: list[float],
        snapshot_id: str | None,
        include_synthetic: bool,
        *,
        limit: int,
    ) -> tuple[list[tuple[float, dict[str, Any]]], str]:
        with closing(connect(self.service.settings.database_path)) as db:
            try:
                import sqlite_vec  # type: ignore[import-not-found]

                db.enable_load_extension(True)
                sqlite_vec.load(db)
                db.enable_load_extension(False)
            except (ImportError, AttributeError, OSError, sqlite3.Error):
                pass
            else:
                params: list[Any] = []
                if snapshot_id:
                    join = """
                    JOIN snapshot_embeddings se
                      ON se.record_id = em.record_id
                     AND se.model = em.model
                     AND se.snapshot_id = ?
                    """
                    params.append(snapshot_id)
                else:
                    join = "JOIN paper_versions v ON v.id = e.paper_version_id AND v.is_current = 1"
                params.extend((self.embedding_key, int(include_synthetic)))
                source_sql = f"""
                    FROM embeddings em
                    JOIN evidence_passages e ON e.id = em.record_id
                    JOIN papers p ON p.id = e.paper_id
                    {join}
                    WHERE em.model = ? AND (? OR p.source_class != 'synthetic')
                """
                source_params = (
                    [params[0], params[1], params[2]] if snapshot_id else params
                )
                source_state = db.execute(
                    f"SELECT count(*) AS count, coalesce(max(em.rowid), 0) AS max_rowid {source_sql}",
                    source_params,
                ).fetchone()
                scope = snapshot_id or "current"
                index_name = stable_id(
                    "papertrail_vec",
                    self.embedding_key,
                    scope,
                    str(int(include_synthetic)),
                    str(len(query_vector)),
                )
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS papertrail_vector_indexes (
                        index_name TEXT PRIMARY KEY,
                        source_count INTEGER NOT NULL,
                        source_max_rowid INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                indexed_state = db.execute(
                    "SELECT source_count, source_max_rowid FROM papertrail_vector_indexes "
                    "WHERE index_name = ?",
                    (index_name,),
                ).fetchone()
                current_state = (source_state["count"], source_state["max_rowid"])
                if indexed_state is None or tuple(indexed_state) != current_state:
                    db.execute(f"DROP TABLE IF EXISTS {index_name}")
                    db.execute(
                        f"""
                        CREATE VIRTUAL TABLE {index_name} USING vec0(
                            record_id TEXT PRIMARY KEY,
                            embedding FLOAT[{len(query_vector)}] distance_metric=cosine
                        )
                        """
                    )
                    db.execute(
                        f"INSERT INTO {index_name}(record_id, embedding) "
                        f"SELECT em.record_id, em.vector_json {source_sql}",
                        source_params,
                    )
                    db.execute(
                        """
                        INSERT INTO papertrail_vector_indexes VALUES (?, ?, ?, ?)
                        ON CONFLICT(index_name) DO UPDATE SET
                            source_count = excluded.source_count,
                            source_max_rowid = excluded.source_max_rowid,
                            updated_at = excluded.updated_at
                        """,
                        (index_name, *current_state, utc_now()),
                    )
                    db.commit()
                rows = db.execute(
                    f"""
                    SELECT record_id, distance FROM {index_name}
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance
                    """,
                    (json.dumps(query_vector, separators=(",", ":")), limit),
                ).fetchall()
                return [
                    (1.0 - float(row["distance"]), dict(row)) for row in rows
                ], "sqlite-vec:vec0-exact"
        rows = self._dense_candidates(snapshot_id, include_synthetic)
        scored = sorted(
            (
                (_cosine(query_vector, json.loads(row["vector_json"])), dict(row))
                for row in rows
            ),
            key=lambda item: item[0],
            reverse=True,
        )[:limit]
        return scored, "python-cosine:fallback"

    def novelty_check(
        self,
        idea: str,
        *,
        snapshot_id: str | None = None,
        nearest_papers: int = 12,
        persist: bool = True,
    ) -> dict[str, Any]:
        search = self.hybrid_search(idea, snapshot_id=snapshot_id, limit=max(20, nearest_papers * 3))
        selected: list[dict[str, Any]] = []
        seen_papers: set[str] = set()
        for item in search["results"]:
            if item["paper_id"] in seen_papers and len(seen_papers) >= nearest_papers:
                continue
            selected.append(item)
            seen_papers.add(item["paper_id"])
            if len(seen_papers) >= nearest_papers and len(selected) >= nearest_papers * 2:
                break
        records = self._records_for_papers(
            seen_papers, {"limitation", "empirical_result", "method"}, snapshot_id
        )
        evidence_text = "\n\n".join(
            f"[{item['evidence_id']}] {item['paper_title']} — {item['section']}\n{item['text']}"
            for item in selected
        )
        record_text = "\n".join(
            f"{item['record_type'].upper()}: {item['statement']} (evidence: {', '.join(item['evidence_ids'])})"
            for item in records
        )
        output = self.provider.structured(
            system=(
                "You are a skeptical research novelty analyst. You cannot establish global novelty. Compare "
                "mechanisms, assumptions, data, evaluation settings, and failure modes. Prefer counterevidence."
            ),
            prompt=f"""
IDEA
{idea}

NEAREST CORPUS EVIDENCE
{evidence_text}

STRUCTURED RECORDS
{record_text or 'No structured records available.'}

Assess overlap only inside this corpus. Distinguish a new mechanism from a new application or evaluation.
Recommend experiments that could falsify the claimed differentiation. Cite only supplied evidence IDs.
""".strip(),
            schema=NOVELTY_SCHEMA,
        )
        allowed = {item["evidence_id"] for item in selected}
        output["evidence_ids"] = [item for item in output.get("evidence_ids", []) if item in allowed]
        experiments = []
        for experiment in output.get("recommended_experiments", []):
            if not isinstance(experiment, dict):
                continue
            experiment["evidence_ids"] = [
                str(item).strip().removeprefix("[").removesuffix("]")
                for item in experiment.get("evidence_ids", [])
                if str(item).strip().removeprefix("[").removesuffix("]") in allowed
            ]
            if experiment.get("name") and experiment.get("design") and experiment.get("falsifies"):
                experiments.append(experiment)
        output["recommended_experiments"] = experiments
        output["confidence"] = max(0.0, min(1.0, float(output.get("confidence", 0.0))))
        result = {
            "idea": idea,
            "assessment": output,
            "nearest_papers": _group_nearest(selected, nearest_papers),
            "coverage": search["coverage"],
            "provenance": search["provenance"],
            "warnings": search["warnings"]
            + ["Novelty is not guaranteed; this assessment is bounded by the selected snapshot/corpus."],
            "epistemic_level": "system_synthesis",
        }
        if persist:
            result["artifact_id"] = self._save_artifact(
                "novelty_check", idea, snapshot_id, result
            )
        return result

    def discover_opportunities(
        self,
        topic: str,
        *,
        snapshot_id: str | None = None,
        limit: int = 3,
        persist: bool = True,
    ) -> dict[str, Any]:
        context = self.hybrid_search(topic, snapshot_id=snapshot_id, limit=60)
        evidence = context["results"]
        paper_ids = {item["paper_id"] for item in evidence}
        records = self._records_for_papers(
            paper_ids,
            {"limitation", "future_work", "assumption", "empirical_result"},
            snapshot_id,
        )
        record_text = "\n".join(
            f"[{','.join(item['evidence_ids'])}] {item['record_type'].upper()}: {item['statement']}"
            for item in records[:150]
        )
        evidence_text = "\n\n".join(
            f"[{item['evidence_id']}] {item['paper_title']} — {item['section']}\n{item['text']}"
            for item in evidence[:40]
        )
        generated = self.provider.structured(
            system=(
                "You propose falsifiable research opportunities from recurring limitations, incompatible "
                "assumptions, and missing evaluation intersections. Do not claim novelty or invent citations."
            ),
            prompt=f"""
TOPIC
{topic}

STRUCTURED SCIENTIFIC RECORDS
{record_text or 'No extracted records available; rely cautiously on passages.'}

SOURCE PASSAGES
{evidence_text}

Propose at most {limit} non-obvious, falsifiable candidates. Each must combine a concrete unresolved gap with
a mechanism and a discriminating experiment. Avoid merely applying an existing method to a new dataset.
Cite only supplied evidence IDs.
""".strip(),
            schema=DISCOVERY_SCHEMA,
        )
        allowed = {item["evidence_id"] for item in evidence}
        candidates: list[dict[str, Any]] = []
        for candidate in generated.get("candidates", [])[:limit]:
            cited = [
                normalized
                for item in candidate.get("evidence_ids", [])
                if (normalized := str(item).strip().removeprefix("[").removesuffix("]"))
                in allowed
            ]
            if not cited or not candidate.get("hypothesis") or not candidate.get("falsifying_experiment"):
                continue
            candidate["evidence_ids"] = cited
            candidate["novelty_challenge"] = self.novelty_check(
                candidate["hypothesis"],
                snapshot_id=snapshot_id,
                nearest_papers=8,
                persist=persist,
            )
            candidates.append(candidate)
        result = {
            "topic": topic,
            "candidates": candidates,
            "source_record_count": len(records),
            "coverage": context["coverage"],
            "provenance": context["provenance"],
            "warnings": context["warnings"]
            + [
                "Candidates are system synthesis, not established findings.",
                "Each candidate has been challenged only against the indexed corpus.",
            ],
            "epistemic_level": "system_synthesis",
        }
        if persist:
            result["artifact_id"] = self._save_artifact(
                "opportunity_discovery", topic, snapshot_id, result
            )
        return result

    def _records_for_papers(
        self,
        paper_ids: set[str],
        record_types: set[str],
        snapshot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not paper_ids or not record_types:
            return []
        paper_marks = ",".join("?" for _ in paper_ids)
        type_marks = ",".join("?" for _ in record_types)
        if snapshot_id:
            version_join = "JOIN snapshot_records sr ON sr.record_id = r.id AND sr.snapshot_id = ?"
            params = [snapshot_id, *paper_ids, *record_types]
        else:
            version_join = "JOIN paper_versions v ON v.id = r.paper_version_id AND v.is_current = 1"
            params = [*paper_ids, *record_types]
        with closing(connect(self.service.settings.database_path)) as db:
            rows = db.execute(
                f"""
                SELECT r.*, group_concat(re.evidence_id) AS evidence_ids_csv
                FROM scientific_records r
                JOIN record_evidence re ON re.record_id = r.id
                {version_join}
                WHERE r.paper_id IN ({paper_marks}) AND r.record_type IN ({type_marks})
                GROUP BY r.id ORDER BY r.confidence DESC
                """,
                params,
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["evidence_ids"] = item.pop("evidence_ids_csv").split(",")
            item["conditions"] = json.loads(item.pop("conditions_json"))
            item["numeric_values"] = json.loads(item.pop("numeric_values_json"))
            results.append(item)
        return results

    def _save_artifact(
        self,
        artifact_type: str,
        input_text: str,
        snapshot_id: str | None,
        value: dict[str, Any],
    ) -> str:
        evidence_ids = sorted(set(_collect_evidence_ids(value)))
        artifact_id = stable_id(
            "artifact", artifact_type, input_text, snapshot_id or "working", json.dumps(value, sort_keys=True)
        )
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO research_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    artifact_type,
                    input_text,
                    snapshot_id,
                    json.dumps(value, sort_keys=True),
                    json.dumps(evidence_ids),
                    self.reasoning_key,
                    "papertrail-intelligence:v1",
                    utc_now(),
                ),
            )
        return artifact_id


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0.0


def _collect_evidence_ids(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_id" and isinstance(item, str):
                found.append(item)
            elif key == "evidence_ids" and isinstance(item, list):
                found.extend(candidate for candidate in item if isinstance(candidate, str))
            else:
                found.extend(_collect_evidence_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_evidence_ids(item))
    return found


def _group_nearest(passages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in passages:
        paper = grouped.setdefault(
            item["paper_id"],
            {
                "paper_id": item["paper_id"],
                "paper_title": item["paper_title"],
                "source_url": item["source_url"],
                "evidence_ids": [],
                "match_reasons": [],
            },
        )
        paper["evidence_ids"].append(item["evidence_id"])
        paper["match_reasons"].extend(item.get("match_reason", []))
    return list(grouped.values())[:limit]
