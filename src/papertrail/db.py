from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    authors_json TEXT NOT NULL DEFAULT '[]',
    published_date TEXT,
    source_url TEXT NOT NULL,
    source_class TEXT NOT NULL DEFAULT 'preprint',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_versions (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    content_hash TEXT NOT NULL,
    artifact_uri TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    UNIQUE(paper_id, content_hash)
);

CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(id),
    heading TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_passages (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(id),
    section_id TEXT NOT NULL REFERENCES sections(id),
    page_start INTEGER,
    page_end INTEGER,
    section_path_json TEXT NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extraction_confidence REAL NOT NULL DEFAULT 0.8,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS evidence_passages_paper_version_idx
ON evidence_passages(paper_id, paper_version_id);

CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
    evidence_id UNINDEXED,
    title,
    abstract,
    section_heading,
    passage,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS visual_evidence (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(id),
    page_number INTEGER NOT NULL,
    label TEXT NOT NULL,
    caption TEXT NOT NULL,
    artifact_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    artifact_kind TEXT NOT NULL DEFAULT 'page_render',
    nearby_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    extraction_method TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(paper_version_id, page_number, label)
);

CREATE VIRTUAL TABLE IF NOT EXISTS visual_evidence_fts USING fts5(
    visual_evidence_id UNINDEXED,
    title,
    label,
    caption,
    nearby_text,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    from_date TEXT,
    to_date TEXT,
    created_at TEXT NOT NULL,
    paper_count INTEGER NOT NULL,
    versions_json TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    known_gaps_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published'
);

CREATE TABLE IF NOT EXISTS snapshot_papers (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
    paper_id TEXT NOT NULL REFERENCES papers(id),
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(id),
    PRIMARY KEY(snapshot_id, paper_id)
);

CREATE TABLE IF NOT EXISTS embeddings (
    record_id TEXT NOT NULL REFERENCES evidence_passages(id),
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(id),
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(record_id, model)
);

CREATE TABLE IF NOT EXISTS scientific_records (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(id),
    record_type TEXT NOT NULL,
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    conditions_json TEXT NOT NULL DEFAULT '{}',
    numeric_values_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL,
    extractor_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_evidence (
    record_id TEXT NOT NULL REFERENCES scientific_records(id),
    evidence_id TEXT NOT NULL REFERENCES evidence_passages(id),
    relation TEXT NOT NULL DEFAULT 'supports',
    PRIMARY KEY(record_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS scientific_records_type_idx
ON scientific_records(record_type, paper_id);

CREATE TABLE IF NOT EXISTS scientific_extractions (
    paper_version_id TEXT NOT NULL REFERENCES paper_versions(id),
    record_type TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY(paper_version_id, record_type, extractor_version)
);

CREATE TABLE IF NOT EXISTS daily_digest_runs (
    id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    snapshot_id TEXT,
    agent_client TEXT NOT NULL,
    agent_model TEXT,
    status TEXT NOT NULL,
    candidate_paper_ids_json TEXT NOT NULL DEFAULT '[]',
    headline TEXT,
    synthesis TEXT,
    trends_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_blogs (
    id TEXT PRIMARY KEY,
    digest_run_id TEXT NOT NULL REFERENCES daily_digest_runs(id),
    paper_id TEXT NOT NULL REFERENCES papers(id),
    source_url TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    dek TEXT NOT NULL,
    surprise TEXT NOT NULL,
    markdown TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    figure_ids_json TEXT NOT NULL DEFAULT '[]',
    themes_json TEXT NOT NULL DEFAULT '[]',
    related_paper_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(digest_run_id, paper_id),
    UNIQUE(slug)
);

CREATE INDEX IF NOT EXISTS daily_blogs_created_idx ON daily_blogs(created_at DESC);

CREATE TABLE IF NOT EXISTS daily_blog_personalization (
    blog_id TEXT PRIMARY KEY REFERENCES daily_blogs(id) ON DELETE CASCADE,
    selection_mode TEXT NOT NULL,
    selection_reason TEXT NOT NULL,
    matched_favorite_ids_json TEXT NOT NULL DEFAULT '[]',
    matched_preference_labels_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_favorites (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS paper_favorites_created_idx
ON paper_favorites(created_at DESC);

CREATE TABLE IF NOT EXISTS preference_sources (
    source TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    consented_at TEXT,
    history_path TEXT,
    last_scanned_at TEXT,
    status TEXT NOT NULL DEFAULT 'disabled',
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS preference_sessions (
    fingerprint TEXT PRIMARY KEY,
    source TEXT NOT NULL REFERENCES preference_sources(source) ON DELETE CASCADE,
    content_digest TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS preference_sessions_source_idx
ON preference_sessions(source, processed_at DESC);

CREATE TABLE IF NOT EXISTS preference_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL REFERENCES preference_sources(source) ON DELETE CASCADE,
    session_fingerprint TEXT NOT NULL REFERENCES preference_sessions(fingerprint)
        ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL,
    explicitness TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS preference_events_source_label_idx
ON preference_events(source, label, observed_at DESC);

CREATE TABLE IF NOT EXISTS preference_profile_versions (
    id TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    active_for_ingestion INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS explicit_interest_profile (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    text TEXT NOT NULL DEFAULT '',
    extraction_status TEXT NOT NULL DEFAULT 'empty',
    error_summary TEXT,
    updated_at TEXT NOT NULL,
    extracted_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_citation_metrics (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_work_id TEXT,
    citation_count INTEGER,
    influential_citation_count INTEGER,
    reference_count INTEGER,
    match_method TEXT NOT NULL,
    match_confidence REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(paper_id, provider)
);

CREATE INDEX IF NOT EXISTS paper_citation_metrics_fetched_idx
ON paper_citation_metrics(provider, fetched_at);

CREATE TABLE IF NOT EXISTS discovery_citation_metrics (
    discovery_id TEXT NOT NULL REFERENCES discovery_records(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_work_id TEXT,
    citation_count INTEGER,
    influential_citation_count INTEGER,
    reference_count INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(discovery_id, provider)
);

CREATE TABLE IF NOT EXISTS paper_priority_scores (
    discovery_id TEXT NOT NULL REFERENCES discovery_records(id) ON DELETE CASCADE,
    profile_version_id TEXT NOT NULL REFERENCES preference_profile_versions(id),
    affinity_score REAL NOT NULL,
    frontier_score REAL NOT NULL,
    exploration_score REAL NOT NULL,
    final_score REAL NOT NULL,
    lane TEXT NOT NULL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(discovery_id, profile_version_id)
);

CREATE TABLE IF NOT EXISTS organization_runs (
    id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
    embedding_model TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    paper_count INTEGER NOT NULL,
    semantic_paper_count INTEGER NOT NULL,
    cluster_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_clusters (
    id TEXT PRIMARY KEY,
    organization_run_id TEXT NOT NULL REFERENCES organization_runs(id),
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    top_terms_json TEXT NOT NULL,
    paper_count INTEGER NOT NULL,
    new_paper_count INTEGER NOT NULL,
    average_similarity REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_cluster_members (
    cluster_id TEXT NOT NULL REFERENCES paper_clusters(id),
    paper_id TEXT NOT NULL REFERENCES papers(id),
    similarity REAL NOT NULL,
    is_new INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL,
    PRIMARY KEY(cluster_id, paper_id)
);

CREATE INDEX IF NOT EXISTS paper_clusters_run_idx
ON paper_clusters(organization_run_id, paper_count DESC);

CREATE TABLE IF NOT EXISTS research_artifacts (
    id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    input_text TEXT NOT NULL,
    snapshot_id TEXT,
    data_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_records (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
    record_id TEXT NOT NULL REFERENCES scientific_records(id),
    PRIMARY KEY(snapshot_id, record_id)
);

CREATE TABLE IF NOT EXISTS snapshot_embeddings (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
    record_id TEXT NOT NULL REFERENCES evidence_passages(id),
    model TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, record_id, model)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    query_json TEXT NOT NULL,
    status TEXT NOT NULL,
    total_expected INTEGER,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    acquired_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    next_cursor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(id),
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    primary_category TEXT,
    published_date TEXT,
    updated_at TEXT,
    abstract_url TEXT NOT NULL,
    pdf_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'discovered',
    paper_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    discovered_at TEXT NOT NULL,
    UNIQUE(run_id, source, source_id)
);

CREATE INDEX IF NOT EXISTS discovery_records_run_status_idx
ON discovery_records(run_id, status, published_date);

CREATE INDEX IF NOT EXISTS discovery_records_source_latest_idx
ON discovery_records(source_id, updated_at DESC, id);

CREATE VIRTUAL TABLE IF NOT EXISTS discovery_fts USING fts5(
    discovery_id UNINDEXED,
    title,
    abstract,
    authors,
    categories,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS ingestion_groups (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    query_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_group_runs (
    group_id TEXT NOT NULL REFERENCES ingestion_groups(id),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(id),
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(group_id, run_id)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize(path: Path) -> None:
    with closing(connect(path)) as connection:
        connection.executescript(SCHEMA)
        record_count = connection.execute("SELECT count(*) FROM discovery_records").fetchone()[0]
        index_count = connection.execute("SELECT count(*) FROM discovery_fts").fetchone()[0]
        if record_count != index_count:
            connection.execute("DELETE FROM discovery_fts")
            connection.execute(
                """
                INSERT INTO discovery_fts (discovery_id, title, abstract, authors, categories)
                SELECT id, title, abstract, authors_json, categories_json FROM discovery_records
                """
            )
            connection.commit()
        personalization_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(daily_blog_personalization)")
        }
        if "matched_preference_labels_json" not in personalization_columns:
            connection.execute(
                "ALTER TABLE daily_blog_personalization "
                "ADD COLUMN matched_preference_labels_json TEXT NOT NULL DEFAULT '[]'"
            )
            connection.commit()


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
