from __future__ import annotations

import hashlib
import json
import re
import shutil
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import LocalArtifactStore
from .config import Settings
from .db import connect, initialize, transaction
from .parsing import extract_pdf, figure_captions, passages, render_pdf_page, split_sections


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: str, size: int = 20) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:size]
    return f"{prefix}_{digest}"


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PaperTrail:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = LocalArtifactStore(settings.artifacts_path)

    def initialize(self) -> None:
        self.settings.ensure()
        initialize(self.settings.database_path)

    def ingest_pdf(
        self,
        path: Path,
        *,
        title: str,
        authors: list[str] | None = None,
        abstract: str = "",
        published_date: str | None = None,
        source_url: str | None = None,
        source_class: str = "preprint",
    ) -> dict[str, Any]:
        content = path.read_bytes()
        pages = extract_pdf(path)
        result = self._ingest(
            content=content,
            suffix=".pdf",
            sections=split_sections(pages),
            title=title,
            authors=authors or [],
            abstract=abstract,
            published_date=published_date,
            source_url=source_url or path.resolve().as_uri(),
            source_class=source_class,
        )
        result["visual_evidence_count"] = self.index_visual_evidence(
            path,
            pages=pages,
            paper_id=result["paper_id"],
            paper_version_id=result["paper_version_id"],
            title=title,
        )
        return result

    def index_visual_evidence(
        self,
        path: Path,
        *,
        pages: list[tuple[int, str]],
        paper_id: str,
        paper_version_id: str,
        title: str,
    ) -> int:
        captions = figure_captions(pages)
        rendered: dict[int, Any] = {}
        created = utc_now()
        indexed = 0
        for item in captions:
            page = int(item["page"])
            if page not in rendered:
                rendered[page] = self.store.put_bytes(
                    render_pdf_page(path, page), namespace="figures", suffix=".png"
                )
            artifact = rendered[page]
            figure_id = stable_id(
                "figure", paper_version_id, str(page), str(item["label"])
            )
            with transaction(self.settings.database_path) as db:
                nearby = db.execute(
                    """
                    SELECT id, text FROM evidence_passages
                    WHERE paper_version_id = ?
                      AND page_start <= ? AND page_end >= ?
                    ORDER BY id LIMIT 8
                    """,
                    (paper_version_id, page, page),
                ).fetchall()
                evidence_ids = [row["id"] for row in nearby]
                nearby_text = " ".join(row["text"][:500] for row in nearby)
                db.execute(
                    """
                    INSERT INTO visual_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'page_render', ?, ?, ?)
                    ON CONFLICT(paper_version_id, page_number, label) DO UPDATE SET
                        caption = excluded.caption,
                        artifact_uri = excluded.artifact_uri,
                        content_hash = excluded.content_hash,
                        nearby_evidence_ids_json = excluded.nearby_evidence_ids_json,
                        extraction_method = excluded.extraction_method
                    """,
                    (
                        figure_id,
                        paper_id,
                        paper_version_id,
                        page,
                        item["label"],
                        item["caption"],
                        artifact.uri,
                        artifact.content_hash,
                        _json(evidence_ids),
                        "caption_regex+ghostscript_page_render:v1",
                        created,
                    ),
                )
                db.execute(
                    "DELETE FROM visual_evidence_fts WHERE visual_evidence_id = ?", (figure_id,)
                )
                db.execute(
                    "INSERT INTO visual_evidence_fts VALUES (?, ?, ?, ?, ?)",
                    (figure_id, title, item["label"], item["caption"], nearby_text),
                )
            indexed += 1
        return indexed

    def ingest_text(
        self,
        text: str,
        *,
        title: str,
        authors: list[str] | None = None,
        abstract: str = "",
        published_date: str | None = None,
        source_url: str = "local://text",
        source_class: str = "preprint",
    ) -> dict[str, Any]:
        text = text.encode("utf-8", errors="replace").decode("utf-8")
        content = text.encode()
        return self._ingest(
            content=content,
            suffix=".txt",
            sections=split_sections([(1, text)]),
            title=title,
            authors=authors or [],
            abstract=abstract,
            published_date=published_date,
            source_url=source_url,
            source_class=source_class,
        )

    def _ingest(
        self,
        *,
        content: bytes,
        suffix: str,
        sections: list[Any],
        title: str,
        authors: list[str],
        abstract: str,
        published_date: str | None,
        source_url: str,
        source_class: str,
    ) -> dict[str, Any]:
        self.initialize()
        normalized = normalize_title(title)
        paper_id = stable_id("paper", normalized)
        artifact = self.store.put_bytes(content, namespace="papers", suffix=suffix)
        version_id = stable_id("version", paper_id, artifact.content_hash)
        created = utc_now()
        passage_count = 0

        with transaction(self.settings.database_path) as db:
            existing = db.execute(
                "SELECT id FROM paper_versions WHERE paper_id = ? AND content_hash = ?",
                (paper_id, artifact.content_hash),
            ).fetchone()
            if existing:
                return {
                    "paper_id": paper_id,
                    "paper_version_id": existing["id"],
                    "status": "already_ingested",
                }

            db.execute(
                """
                INSERT INTO papers (
                    id, canonical_title, normalized_title, abstract, authors_json,
                    published_date, source_url, source_class, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    abstract = CASE WHEN excluded.abstract != '' THEN excluded.abstract ELSE papers.abstract END,
                    authors_json = CASE WHEN excluded.authors_json != '[]' THEN excluded.authors_json ELSE papers.authors_json END,
                    published_date = COALESCE(excluded.published_date, papers.published_date),
                    source_url = excluded.source_url,
                    source_class = excluded.source_class
                """,
                (
                    paper_id,
                    title.strip(),
                    normalized,
                    abstract.strip(),
                    _json(authors),
                    published_date,
                    source_url,
                    source_class,
                    created,
                ),
            )
            db.execute("UPDATE paper_versions SET is_current = 0 WHERE paper_id = ?", (paper_id,))
            db.execute(
                "INSERT INTO paper_versions VALUES (?, ?, ?, ?, ?, 1)",
                (version_id, paper_id, artifact.content_hash, artifact.uri, created),
            )

            for ordinal, section in enumerate(sections):
                section_id = stable_id("section", version_id, str(ordinal), section.heading)
                db.execute(
                    "INSERT INTO sections VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        section_id,
                        version_id,
                        section.heading,
                        ordinal,
                        section.page_start,
                        section.page_end,
                        section.text,
                    ),
                )
                for passage_ordinal, passage in enumerate(passages(section.text)):
                    content_hash = hashlib.sha256(passage.encode()).hexdigest()
                    evidence_id = stable_id(
                        "ev",
                        version_id,
                        section_id,
                        str(passage_ordinal),
                        content_hash,
                    )
                    db.execute(
                        "INSERT INTO evidence_passages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.8, ?)",
                        (
                            evidence_id,
                            paper_id,
                            version_id,
                            section_id,
                            section.page_start,
                            section.page_end,
                            _json([section.heading]),
                            passage,
                            content_hash,
                            created,
                        ),
                    )
                    db.execute(
                        "INSERT INTO evidence_fts VALUES (?, ?, ?, ?, ?)",
                        (evidence_id, title, abstract, section.heading, passage),
                    )
                    passage_count += 1

        return {
            "paper_id": paper_id,
            "paper_version_id": version_id,
            "passage_count": passage_count,
            "artifact_hash": artifact.content_hash,
            "status": "ingested",
        }

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = re.findall(r"[\w-]+", query.casefold(), re.UNICODE)
        if not terms:
            raise ValueError("Search query must contain at least one word")
        return " OR ".join(f'"{term}"' for term in terms[:20])

    def search(
        self,
        query: str,
        *,
        snapshot_id: str | None = None,
        include_synthetic: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        self.initialize()
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with closing(connect(self.settings.database_path)) as db:
            snapshot = self._resolve_snapshot(db, snapshot_id)
            snapshot_join = ""
            parameters: list[Any] = []
            if snapshot:
                snapshot_join = """
                JOIN snapshot_papers sp
                  ON sp.paper_id = e.paper_id
                 AND sp.paper_version_id = e.paper_version_id
                 AND sp.snapshot_id = ?
                """
                parameters.append(snapshot["id"])
            else:
                snapshot_join = """
                JOIN paper_versions current_version
                  ON current_version.id = e.paper_version_id
                 AND current_version.is_current = 1
                """
            parameters.append(self._fts_query(query))
            parameters.append(limit)
            rows = db.execute(
                f"""
                SELECT e.id AS evidence_id, e.paper_id, p.canonical_title AS paper_title,
                       p.source_url, p.source_class, p.published_date,
                       s.heading AS section, e.page_start, e.page_end, e.text,
                       e.paper_version_id AS document_version,
                       e.extraction_confidence, bm25(evidence_fts, 0, 5, 3, 2, 1) AS rank
                FROM evidence_fts
                JOIN evidence_passages e ON e.id = evidence_fts.evidence_id
                JOIN papers p ON p.id = e.paper_id
                JOIN sections s ON s.id = e.section_id
                {snapshot_join}
                WHERE evidence_fts MATCH ?
                  {'' if include_synthetic else "AND p.source_class != 'synthetic'"}
                ORDER BY rank
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            results = [dict(row) for row in rows]
            for result in results:
                result["score"] = round(-result.pop("rank"), 6)
                result["match_reason"] = f"Full-text match in {result['section']}"
            return self._response(db, query, snapshot, results)

    def search_figures(
        self, query: str, *, snapshot_id: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        self.initialize()
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with closing(connect(self.settings.database_path)) as db:
            snapshot_join = ""
            current_version = "AND pv.is_current = 1"
            parameters: list[Any] = []
            if snapshot_id:
                current_version = ""
                snapshot_join = """
                JOIN snapshot_papers sp
                  ON sp.paper_id = v.paper_id
                 AND sp.paper_version_id = v.paper_version_id
                 AND sp.snapshot_id = ?
                """
                parameters.append(snapshot_id)
            parameters.extend((self._fts_query(query), limit))
            rows = db.execute(
                f"""
                SELECT v.id AS figure_id, v.paper_id, p.canonical_title AS paper_title,
                       p.source_url, v.page_number, v.label, v.caption, v.artifact_uri,
                       v.artifact_kind, v.nearby_evidence_ids_json,
                       bm25(visual_evidence_fts, 0, 4, 2, 3, 1) AS rank
                FROM visual_evidence_fts
                JOIN visual_evidence v ON v.id = visual_evidence_fts.visual_evidence_id
                JOIN papers p ON p.id = v.paper_id
                JOIN paper_versions pv ON pv.id = v.paper_version_id {current_version}
                {snapshot_join}
                WHERE visual_evidence_fts MATCH ?
                ORDER BY rank LIMIT ?
                """,
                parameters,
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["nearby_evidence_ids"] = json.loads(item.pop("nearby_evidence_ids_json"))
            item["score"] = round(-item.pop("rank"), 6)
            results.append(item)
        return {"query": query, "results": results, "index": "visual-evidence-fts5:v1"}

    def search_catalog(
        self,
        query: str,
        *,
        category: str | None = None,
        primary_only: bool = False,
        from_date: str | None = None,
        to_date: str | None = None,
        snapshot_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        self.initialize()
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        filters = ["discovery_fts MATCH ?"]
        parameters: list[Any] = [self._fts_query(query)]
        if category:
            filters.append(
                "d.primary_category = ?" if primary_only else "d.categories_json LIKE ?"
            )
            parameters.append(category if primary_only else f'%"{category}"%')
        if from_date:
            filters.append("d.published_date >= ?")
            parameters.append(from_date)
        if to_date:
            filters.append("d.published_date <= ?")
            parameters.append(to_date)
        if snapshot_id:
            filters.append(
                "EXISTS (SELECT 1 FROM snapshot_papers sp WHERE sp.snapshot_id = ? AND sp.paper_id = d.paper_id)"
            )
            parameters.append(snapshot_id)
        parameters.append(limit)
        with closing(connect(self.settings.database_path)) as db:
            rows = db.execute(
                f"""
                WITH canonical AS MATERIALIZED (
                    SELECT id FROM (
                        SELECT id, row_number() OVER (
                            PARTITION BY source_id
                            ORDER BY CASE status WHEN 'acquired' THEN 1 ELSE 0 END DESC,
                                     updated_at DESC, rowid DESC
                        ) AS canonical_rank
                        FROM discovery_records
                    ) WHERE canonical_rank = 1
                )
                SELECT d.*, bm25(discovery_fts, 0, 5, 2, 0.5, 0.2) AS rank
                FROM discovery_fts
                JOIN discovery_records d ON d.id = discovery_fts.discovery_id
                JOIN canonical c ON c.id = d.id
                WHERE {' AND '.join(filters)}
                ORDER BY rank, published_date DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return {
            "query": query,
            "results": [
                {
                    "source_id": row["source_id"],
                    "title": row["title"],
                    "abstract": row["abstract"],
                    "authors": json.loads(row["authors_json"]),
                    "primary_category": row["primary_category"],
                    "categories": json.loads(row["categories_json"]),
                    "published_date": row["published_date"],
                    "abstract_url": row["abstract_url"],
                    "full_text_status": row["status"],
                    "paper_id": row["paper_id"],
                    "score": round(-row["rank"], 6),
                }
                for row in rows
            ],
            "coverage": self.corpus_status(compact=True),
            "index": "arxiv-metadata-fts5:v1",
        }

    def get_scientific_records(
        self,
        *,
        paper_id: str | None = None,
        record_types: list[str] | None = None,
        snapshot_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self.initialize()
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        filters = ["1 = 1"]
        parameters: list[Any] = []
        if paper_id:
            filters.append("r.paper_id = ?")
            parameters.append(paper_id)
        if record_types:
            marks = ",".join("?" for _ in record_types)
            filters.append(f"r.record_type IN ({marks})")
            parameters.extend(record_types)
        if snapshot_id:
            filters.append(
                "EXISTS (SELECT 1 FROM snapshot_records sr WHERE sr.snapshot_id = ? AND sr.record_id = r.id)"
            )
            parameters.append(snapshot_id)
        parameters.append(limit)
        with closing(connect(self.settings.database_path)) as db:
            rows = db.execute(
                f"""
                SELECT r.*, p.canonical_title AS paper_title, p.source_url
                FROM scientific_records r JOIN papers p ON p.id = r.paper_id
                WHERE {' AND '.join(filters)}
                ORDER BY r.created_at DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["conditions"] = json.loads(item.pop("conditions_json"))
                item["numeric_values"] = json.loads(item.pop("numeric_values_json"))
                item["evidence_ids"] = [
                    value[0]
                    for value in db.execute(
                        "SELECT evidence_id FROM record_evidence WHERE record_id = ?",
                        (item["id"],),
                    )
                ]
                results.append(item)
        return {"results": results, "count": len(results)}

    def get_figure(self, figure_id: str) -> dict[str, Any]:
        self.initialize()
        with closing(connect(self.settings.database_path)) as db:
            row = db.execute(
                """
                SELECT v.*, p.canonical_title AS paper_title, p.source_url
                FROM visual_evidence v JOIN papers p ON p.id = v.paper_id
                WHERE v.id = ?
                """,
                (figure_id,),
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown figure: {figure_id}")
        result = dict(row)
        result["nearby_evidence_ids"] = json.loads(result.pop("nearby_evidence_ids_json"))
        return result

    def related_papers(
        self,
        paper_id: str,
        *,
        snapshot_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        paper = self.get_paper(paper_id, snapshot_id=snapshot_id)["results"][0]
        query = f"{paper['canonical_title']} {paper['abstract']}"
        terms = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", query)
        stop = {"this", "that", "with", "from", "using", "paper", "based", "their"}
        selected = list(dict.fromkeys(term.casefold() for term in terms if term.casefold() not in stop))[:12]
        result = self.search_catalog(
            " ".join(selected),
            snapshot_id=snapshot_id,
            limit=min(100, limit + 5),
        )
        result["results"] = [
            item for item in result["results"] if item.get("paper_id") != paper_id
        ][:limit]
        result["paper_id"] = paper_id
        return result

    def corpus_status(self, *, compact: bool = False) -> dict[str, Any]:
        self.initialize()
        with closing(connect(self.settings.database_path)) as db:
            counts = db.execute(
                """
                SELECT
                  (SELECT count(DISTINCT source_id) FROM discovery_records) AS catalog_papers,
                  (SELECT count(*) FROM papers) AS full_text_papers,
                  (SELECT count(*) FROM evidence_passages) AS evidence_passages,
                  (SELECT count(*) FROM embeddings) AS embeddings,
                  (SELECT count(*) FROM scientific_records) AS scientific_records,
                  (SELECT count(*) FROM visual_evidence) AS visual_evidence
                """
            ).fetchone()
            models = {
                row["model"]: row["count"]
                for row in db.execute(
                    "SELECT model, count(*) AS count FROM embeddings GROUP BY model"
                )
            }
            record_types = {
                row["record_type"]: row["count"]
                for row in db.execute(
                    "SELECT record_type, count(*) AS count FROM scientific_records GROUP BY record_type"
                )
            }
            recent_groups = []
            for row in db.execute(
                """
                SELECT g.id, g.status, g.query_json,
                       count(d.id) AS discovered,
                       sum(CASE WHEN d.status = 'acquired' THEN 1 ELSE 0 END) AS acquired,
                       sum(CASE WHEN d.status = 'failed' THEN 1 ELSE 0 END) AS failed,
                       sum(CASE WHEN d.primary_category = json_extract(g.query_json, '$.category') THEN 1 ELSE 0 END) AS primary_total,
                       sum(CASE WHEN d.primary_category = json_extract(g.query_json, '$.category') AND d.status = 'acquired' THEN 1 ELSE 0 END) AS primary_acquired,
                       sum(CASE WHEN d.primary_category = json_extract(g.query_json, '$.category') AND d.status = 'failed' THEN 1 ELSE 0 END) AS primary_failed
                FROM ingestion_groups g
                LEFT JOIN ingestion_group_runs gr ON gr.group_id = g.id
                LEFT JOIN discovery_records d ON d.run_id = gr.run_id
                GROUP BY g.id ORDER BY g.created_at DESC LIMIT 5
                """
            ):
                item = dict(row)
                item["query"] = json.loads(item.pop("query_json"))
                item["primary_pending"] = (
                    item["primary_total"]
                    - item["primary_acquired"]
                    - item["primary_failed"]
                )
                recent_groups.append(item)
        result = dict(counts)
        result["embedding_models"] = models
        result["scientific_record_types"] = record_types
        result["recent_ingestion_groups"] = recent_groups
        if not compact:
            disk = shutil.disk_usage(self.settings.home)
            result["storage"] = {
                "home": str(self.settings.home),
                "free_bytes": disk.free,
                "total_bytes": disk.total,
            }
            result["known_gaps"] = [
                "Catalog-only papers do not support evidence-grounded conclusions.",
                "Figure page renders are not equivalent to model-interpreted diagrams.",
                "Novelty claims are bounded by indexed coverage and source quality.",
            ]
        return result

    def get_paper(self, paper_id: str, *, snapshot_id: str | None = None) -> dict[str, Any]:
        self.initialize()
        with closing(connect(self.settings.database_path)) as db:
            snapshot = self._resolve_snapshot(db, snapshot_id)
            if snapshot and not db.execute(
                "SELECT 1 FROM snapshot_papers WHERE snapshot_id = ? AND paper_id = ?",
                (snapshot["id"], paper_id),
            ).fetchone():
                raise KeyError(f"Paper {paper_id} is not in snapshot {snapshot['id']}")
            if snapshot:
                row = db.execute(
                    """
                    SELECT p.*, v.id AS document_version, v.content_hash, v.artifact_uri,
                           c.citation_count, c.influential_citation_count, c.reference_count,
                           c.fetched_at AS citation_fetched_at
                    FROM papers p
                    JOIN snapshot_papers sp ON sp.paper_id = p.id AND sp.snapshot_id = ?
                    JOIN paper_versions v ON v.id = sp.paper_version_id
                    LEFT JOIN paper_citation_metrics c ON c.paper_id = p.id
                      AND c.provider = 'semantic_scholar'
                    WHERE p.id = ?
                    """,
                    (snapshot["id"], paper_id),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    SELECT p.*, v.id AS document_version, v.content_hash, v.artifact_uri,
                           c.citation_count, c.influential_citation_count, c.reference_count,
                           c.fetched_at AS citation_fetched_at
                    FROM papers p JOIN paper_versions v ON v.paper_id = p.id AND v.is_current = 1
                    LEFT JOIN paper_citation_metrics c ON c.paper_id = p.id
                      AND c.provider = 'semantic_scholar'
                    WHERE p.id = ?
                    """,
                    (paper_id,),
                ).fetchone()
            if not row:
                raise KeyError(f"Unknown paper: {paper_id}")
            result = dict(row)
            artifact_uri = str(result.get("artifact_uri") or "")
            result["artifact_available"] = bool(artifact_uri)
            result["artifact_kind"] = "pdf" if artifact_uri.casefold().endswith(".pdf") else "text"
            result["authors"] = json.loads(result.pop("authors_json"))
            result["favorite"] = bool(
                db.execute(
                    "SELECT 1 FROM paper_favorites WHERE paper_id = ?", (paper_id,)
                ).fetchone()
            )
            result["sections"] = [
                dict(item)
                for item in db.execute(
                    "SELECT heading, page_start, page_end FROM sections WHERE paper_version_id = ? ORDER BY ordinal",
                    (result["document_version"],),
                )
            ]
            result["figures"] = [
                {
                    **dict(item),
                    "nearby_evidence_ids": json.loads(item["nearby_evidence_ids_json"]),
                }
                for item in db.execute(
                    """
                    SELECT id AS figure_id, page_number, label, caption, artifact_uri,
                           artifact_kind, nearby_evidence_ids_json
                    FROM visual_evidence WHERE paper_version_id = ?
                    ORDER BY page_number, label
                    """,
                    (result["document_version"],),
                )
            ]
            for figure in result["figures"]:
                figure.pop("nearby_evidence_ids_json")
            return self._response(db, paper_id, snapshot, [result])

    def set_favorite(self, paper_id: str, favorite: bool) -> dict[str, Any]:
        self.initialize()
        with transaction(self.settings.database_path) as db:
            paper = db.execute(
                "SELECT canonical_title FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if not paper:
                raise KeyError(f"Unknown paper: {paper_id}")
            if favorite:
                db.execute(
                    "INSERT INTO paper_favorites VALUES (?, ?) "
                    "ON CONFLICT(paper_id) DO NOTHING",
                    (paper_id, utc_now()),
                )
            else:
                db.execute("DELETE FROM paper_favorites WHERE paper_id = ?", (paper_id,))
        return {
            "paper_id": paper_id,
            "title": paper["canonical_title"],
            "favorite": favorite,
        }

    def list_favorites(self) -> dict[str, Any]:
        self.initialize()
        with closing(connect(self.settings.database_path)) as db:
            rows = db.execute(
                """
                SELECT p.id AS paper_id, p.canonical_title AS title, p.abstract,
                       p.authors_json, p.published_date, p.source_url, p.source_class,
                       f.created_at AS favorited_at,
                       CASE WHEN v.id IS NULL THEN 0 ELSE 1 END AS artifact_available,
                       c.citation_count, c.influential_citation_count, c.reference_count,
                       c.fetched_at AS citation_fetched_at
                FROM paper_favorites f
                JOIN papers p ON p.id = f.paper_id
                LEFT JOIN paper_versions v ON v.paper_id = p.id AND v.is_current = 1
                LEFT JOIN paper_citation_metrics c ON c.paper_id = p.id
                  AND c.provider = 'semantic_scholar'
                ORDER BY f.created_at DESC, p.canonical_title
                """
            ).fetchall()
        favorites = []
        for row in rows:
            item = dict(row)
            item["authors"] = json.loads(item.pop("authors_json"))
            item["artifact_available"] = bool(item["artifact_available"])
            item["favorite"] = True
            favorites.append(item)
        if favorites:
            from .preferences import aggregate_profile
            from .ranking import rank_group_papers

            profile = aggregate_profile(self, persist=False)
            ranked = rank_group_papers(
                [{**item, "similarity": 0.0, "is_new": False} for item in favorites],
                profile,
            )
            by_id = {item["paper_id"]: item["ranking"] for item in ranked}
            for item in favorites:
                item["ranking"] = by_id[item["paper_id"]]
        return {"favorites": favorites, "count": len(favorites)}

    def get_evidence(self, evidence_ids: list[str]) -> dict[str, Any]:
        self.initialize()
        if not evidence_ids:
            raise ValueError("At least one evidence ID is required")
        placeholders = ",".join("?" for _ in evidence_ids)
        with closing(connect(self.settings.database_path)) as db:
            rows = db.execute(
                f"""
                SELECT e.id AS evidence_id, e.paper_id, p.canonical_title AS paper_title,
                       s.heading AS section, e.page_start, e.page_end, e.text,
                       e.paper_version_id AS document_version, p.source_url, p.source_class,
                       e.extraction_confidence
                FROM evidence_passages e
                JOIN papers p ON p.id = e.paper_id
                JOIN sections s ON s.id = e.section_id
                WHERE e.id IN ({placeholders})
                """,
                evidence_ids,
            ).fetchall()
            found = {row["evidence_id"] for row in rows}
            missing = sorted(set(evidence_ids) - found)
            response = self._response(db, "evidence lookup", None, [dict(row) for row in rows])
            if missing:
                response["warnings"].append(f"Unknown evidence IDs: {', '.join(missing)}")
            return response

    def create_snapshot(
        self,
        snapshot_id: str,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        include_synthetic: bool = False,
        category: str | None = None,
        primary_only: bool = False,
    ) -> dict[str, Any]:
        self.initialize()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", snapshot_id):
            raise ValueError("Snapshot ID may contain letters, numbers, dots, dashes, and underscores")
        with transaction(self.settings.database_path) as db:
            clauses = ["v.is_current = 1"]
            if not include_synthetic:
                clauses.append("p.source_class != 'synthetic'")
            params: list[Any] = []
            if from_date:
                clauses.append("p.published_date >= ?")
                params.append(from_date)
            if to_date:
                clauses.append("p.published_date <= ?")
                params.append(to_date)
            if category:
                clauses.append(
                    """
                    EXISTS (
                        SELECT 1 FROM discovery_records d
                        WHERE d.paper_id = p.id
                          AND (d.primary_category = ? OR (? = 0 AND d.categories_json LIKE ?))
                    )
                    """
                )
                params.extend((category, int(primary_only), f'%"{category}"%'))
            rows = db.execute(
                f"""SELECT p.id AS paper_id, v.id AS paper_version_id, p.source_class
                    FROM papers p JOIN paper_versions v ON v.paper_id = p.id
                    WHERE {' AND '.join(clauses)}""",
                params,
            ).fetchall()
            version_ids = [item["paper_version_id"] for item in rows]
            if version_ids:
                version_marks = ",".join("?" for _ in version_ids)
                record_ids = [
                    item[0]
                    for item in db.execute(
                        f"SELECT id FROM scientific_records WHERE paper_version_id IN ({version_marks})",
                        version_ids,
                    )
                ]
                embedding_rows = db.execute(
                    f"SELECT record_id, model FROM embeddings WHERE paper_version_id IN ({version_marks})",
                    version_ids,
                ).fetchall()
            else:
                record_ids = []
                embedding_rows = []
            existing = db.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            if existing:
                existing_versions = {
                    (item["paper_id"], item["paper_version_id"])
                    for item in db.execute(
                        "SELECT paper_id, paper_version_id FROM snapshot_papers WHERE snapshot_id = ?",
                        (snapshot_id,),
                    )
                }
                requested_versions = {
                    (item["paper_id"], item["paper_version_id"]) for item in rows
                }
                if (
                    existing["from_date"] == from_date
                    and existing["to_date"] == to_date
                    and existing_versions == requested_versions
                ):
                    return {
                        "snapshot_id": snapshot_id,
                        "paper_count": existing["paper_count"],
                        "created_at": existing["created_at"],
                        "coverage": json.loads(existing["coverage_json"]),
                        "known_gaps": json.loads(existing["known_gaps_json"]),
                        "scientific_record_count": db.execute(
                            "SELECT count(*) FROM snapshot_records WHERE snapshot_id = ?",
                            (snapshot_id,),
                        ).fetchone()[0],
                        "embedding_count": db.execute(
                            "SELECT count(*) FROM snapshot_embeddings WHERE snapshot_id = ?",
                            (snapshot_id,),
                        ).fetchone()[0],
                        "status": "already_published",
                    }
                raise ValueError(
                    f"Snapshot {snapshot_id} already exists with different contents; snapshots are immutable"
                )
            created = utc_now()
            coverage = {
                "from_date": from_date,
                "to_date": to_date,
                "category": category,
                "primary_only": primary_only,
                "source_classes": [
                    source_class
                    for source_class in sorted({row["source_class"] for row in rows})
                ],
            }
            if category:
                category_clause = (
                    "d.primary_category = ?"
                    if primary_only
                    else "EXISTS (SELECT 1 FROM json_each(d.categories_json) WHERE value = ?)"
                )
                catalog = db.execute(
                    f"""
                    WITH ranked AS (
                        SELECT d.status, row_number() OVER (
                            PARTITION BY d.source_id
                            ORDER BY CASE d.status WHEN 'acquired' THEN 1 ELSE 0 END DESC,
                                d.updated_at DESC, d.rowid DESC
                        ) AS canonical_rank
                        FROM discovery_records d
                        WHERE d.published_date >= ? AND d.published_date <= ?
                          AND {category_clause}
                    )
                    SELECT count(*) AS total,
                           sum(CASE WHEN status = 'acquired' THEN 1 ELSE 0 END) AS acquired,
                           sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS unavailable
                    FROM ranked WHERE canonical_rank = 1
                    """,
                    (from_date, to_date, category),
                ).fetchone()
                coverage["catalog_papers"] = catalog["total"]
                coverage["full_text_papers"] = catalog["acquired"] or 0
                coverage["unavailable_papers"] = catalog["unavailable"] or 0
            known_gaps = [
                "Only explicitly imported papers are included.",
                "Novelty and opportunity analysis are bounded by this corpus.",
            ]
            if coverage.get("unavailable_papers"):
                known_gaps.append(
                    f"{coverage['unavailable_papers']} catalog papers had no retrievable full text."
                )
            if not embedding_rows:
                known_gaps.append("No semantic embeddings were published in this snapshot.")
            if not record_ids:
                known_gaps.append("No structured scientific records were published in this snapshot.")
            versions = {
                "schema": "2",
                "parser": "papertrail-local:0.5.0",
                "lexical_index": "sqlite-fts5",
                "embedding_provider": self.settings.embedding_provider,
                "reasoning_provider": self.settings.reasoning_provider,
                "embedding_model": self.settings.embedding_model,
                "reasoning_model": self.settings.reasoning_model,
            }
            db.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published')",
                (
                    snapshot_id,
                    from_date,
                    to_date,
                    created,
                    len(rows),
                    _json(versions),
                    _json(coverage),
                    _json(known_gaps),
                ),
            )
            db.executemany(
                "INSERT INTO snapshot_papers VALUES (?, ?, ?)",
                [(snapshot_id, row["paper_id"], row["paper_version_id"]) for row in rows],
            )
            db.executemany(
                "INSERT INTO snapshot_records VALUES (?, ?)",
                [(snapshot_id, record_id) for record_id in record_ids],
            )
            db.executemany(
                "INSERT INTO snapshot_embeddings VALUES (?, ?, ?)",
                [(snapshot_id, item["record_id"], item["model"]) for item in embedding_rows],
            )
            return {
                "snapshot_id": snapshot_id,
                "paper_count": len(rows),
                "created_at": created,
                "coverage": coverage,
                "known_gaps": known_gaps,
                "scientific_record_count": len(record_ids),
                "embedding_count": len(embedding_rows),
                "status": "published",
            }

    def snapshot_info(self, snapshot_id: str | None = None) -> dict[str, Any]:
        self.initialize()
        with closing(connect(self.settings.database_path)) as db:
            snapshot = self._resolve_snapshot(db, snapshot_id)
            if snapshot_id is None:
                snapshot = db.execute(
                    "SELECT * FROM snapshots WHERE status = 'published' ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            if not snapshot:
                raise KeyError("No published snapshot exists")
            result = dict(snapshot)
            for field in ("versions_json", "coverage_json", "known_gaps_json"):
                result[field.removesuffix("_json")] = json.loads(result.pop(field))
            result["scientific_record_count"] = db.execute(
                "SELECT count(*) FROM snapshot_records WHERE snapshot_id = ?", (snapshot["id"],)
            ).fetchone()[0]
            result["embedding_count"] = db.execute(
                "SELECT count(*) FROM snapshot_embeddings WHERE snapshot_id = ?", (snapshot["id"],)
            ).fetchone()[0]
            return result

    @staticmethod
    def _resolve_snapshot(db: Any, snapshot_id: str | None) -> Any:
        if snapshot_id:
            row = db.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            if not row:
                raise KeyError(f"Unknown snapshot: {snapshot_id}")
            return row
        return None

    @staticmethod
    def _response(db: Any, query: str, snapshot: Any, results: list[dict[str, Any]]) -> dict[str, Any]:
        warnings: list[str] = []
        if not snapshot:
            warnings.append("No snapshot selected; results use the current mutable working set.")
            coverage: dict[str, Any] = {"paper_count": db.execute("SELECT count(*) FROM papers").fetchone()[0]}
            snapshot_id = None
        else:
            coverage = json.loads(snapshot["coverage_json"])
            coverage["paper_count"] = snapshot["paper_count"]
            warnings.extend(json.loads(snapshot["known_gaps_json"]))
            snapshot_id = snapshot["id"]
        if not results:
            warnings.append("No matching evidence was found; no fallback results were invented.")
        return {
            "query_id": stable_id("query", query, snapshot_id or "working", utc_now()),
            "results": results,
            "coverage": coverage,
            "provenance": {
                "snapshot_id": snapshot_id,
                "retriever_version": "sqlite-fts5",
                "index_version": "1",
                "generated_at": utc_now(),
            },
            "warnings": warnings,
        }
