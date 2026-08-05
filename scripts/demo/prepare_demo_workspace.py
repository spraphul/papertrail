from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from scripts.demo.model import DemoCounts, DemoManifest


SAFE_PROFILE = {
    "profile": "local",
    "providers": {
        "embedding_provider": "ollama",
        "reasoning_provider": "ollama",
        "embedding_model": "embeddinggemma",
        "reasoning_model": "qwen2.5:7b",
    },
    "preferences": {"chat_learning": False, "sources": {}},
}


def _rewrite_artifact_paths(db: sqlite3.Connection, source: Path, target: Path) -> None:
    for table in ("paper_versions", "visual_evidence"):
        for rowid, uri in db.execute(f"SELECT rowid, artifact_uri FROM {table}"):
            parsed = urlparse(uri)
            path = Path(unquote(parsed.path)) if parsed.scheme == "file" else Path(uri)
            if not path.is_absolute():
                continue
            try:
                relative = path.relative_to(source)
            except ValueError as error:
                raise ValueError(f"artifact escapes source home: {path}") from error
            db.execute(
                f"UPDATE {table} SET artifact_uri = ? WHERE rowid = ?",
                ((target / relative).as_uri(), rowid),
            )


def prepare_workspace(source: Path, target: Path, interests: str) -> None:
    source, target = source.resolve(), target.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite {target}")
    if not (source / "papertrail.db").is_file():
        raise FileNotFoundError(source / "papertrail.db")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("*.log", "*.pid", "*-error.log"),
    )
    (target / "profile.json").write_text(json.dumps(SAFE_PROFILE, indent=2) + "\n")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(target / "papertrail.db") as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("DELETE FROM preference_events")
        db.execute("DELETE FROM preference_sessions")
        db.execute("DELETE FROM preference_sources")
        db.execute("DELETE FROM paper_favorites")
        db.execute("DELETE FROM explicit_interest_profile")
        db.execute(
            "INSERT INTO explicit_interest_profile VALUES (1, ?, 'ready', NULL, ?, ?)",
            (interests, now, now),
        )
        summary = json.dumps(
            {"concepts": [{"label": interests, "polarity": "positive", "weight": 1.0}]}
        )
        db.execute("UPDATE preference_profile_versions SET summary_json = ?", (summary,))
        db.execute(
            "UPDATE daily_blog_personalization SET matched_favorite_ids_json='[]', "
            "matched_preference_labels_json='[]', selection_reason = CASE selection_mode "
            "WHEN 'preference' THEN 'Matched the public demo profile on reliable adaptive agents.' "
            "WHEN 'exploration' THEN 'Selected to preserve deliberate exploration beyond the core profile.' "
            "ELSE 'Selected for evidence quality, recency, and a surprising result.' END"
        )
        db.execute(
            "UPDATE paper_priority_scores SET explanation = CASE lane "
            "WHEN 'preference' THEN 'Strong match to the public demo profile.' "
            "WHEN 'frontier' THEN 'Adjacent to the profile with a distinct mechanism.' "
            "ELSE 'Exploration lane with recent, well-supported evidence.' END"
        )
        _rewrite_artifact_paths(db, source, target)
        db.commit()


def inspect_workspace(home: Path, expected: DemoCounts) -> dict[str, object]:
    with sqlite3.connect(home / "papertrail.db") as db:
        db.row_factory = sqlite3.Row
        run = db.execute(
            "SELECT id FROM organization_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if run is None:
            raise ValueError("demo index has no organization run")
        counts = {
            "papers": db.execute("SELECT count(*) FROM papers").fetchone()[0],
            "evidence": db.execute("SELECT count(*) FROM evidence_passages").fetchone()[0],
            "figures": db.execute("SELECT count(*) FROM visual_evidence").fetchone()[0],
            "groups": db.execute(
                "SELECT count(*) FROM paper_clusters WHERE organization_run_id=?",
                (run["id"],),
            ).fetchone()[0],
            "blogs": db.execute("SELECT count(*) FROM daily_blogs").fetchone()[0],
        }
        if counts != vars(expected):
            raise ValueError(f"demo counts changed: expected {vars(expected)}, got {counts}")
        selected = db.execute(
            """
            SELECT c.id AS cluster_id, p.id AS paper_id
            FROM paper_clusters c
            JOIN paper_cluster_members m ON m.cluster_id=c.id
            JOIN papers p ON p.id=m.paper_id
            WHERE c.organization_run_id=?
              AND EXISTS (
                SELECT 1 FROM paper_versions v WHERE v.paper_id=p.id AND v.is_current=1
              )
              AND EXISTS (SELECT 1 FROM visual_evidence f WHERE f.paper_id=p.id)
            ORDER BY c.paper_count DESC, m.position ASC LIMIT 1
            """,
            (run["id"],),
        ).fetchone()
        blog = db.execute(
            "SELECT slug FROM daily_blogs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if selected is None or blog is None:
            raise ValueError("demo routes require a group paper with figures and a complete blog")
        sources = [row[0] for row in db.execute("SELECT source_url FROM papers")]
        if any(not value.startswith("https://arxiv.org/abs/") for value in sources):
            raise ValueError("all recorded papers need public arXiv source URLs")
        artifact_uris = [row[0] for row in db.execute("SELECT artifact_uri FROM paper_versions")]
        artifact_uris += [row[0] for row in db.execute("SELECT artifact_uri FROM visual_evidence")]
        artifacts = [Path(unquote(urlparse(uri).path)) for uri in artifact_uris]
        missing = [str(path) for path in artifacts if not path.is_file()]
        if missing:
            raise ValueError("missing copied artifacts: " + ", ".join(missing[:5]))
    return {
        "counts": counts,
        "routes": {
            "group": selected["cluster_id"],
            "paper": selected["paper_id"],
            "blog": blog["slug"],
        },
        "source_urls": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-home", type=Path, required=True)
    parser.add_argument("--output-home", type=Path, required=True)
    parser.add_argument("--interests-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("scripts/demo/demo.json"))
    parser.add_argument(
        "--prepared-manifest",
        type=Path,
        default=Path(".demo-work/prepared-manifest.json"),
    )
    args = parser.parse_args()
    manifest = DemoManifest.load(args.manifest)
    prepare_workspace(
        args.source_home,
        args.output_home,
        args.interests_file.read_text().strip(),
    )
    prepared = inspect_workspace(args.output_home.resolve(), manifest.counts)
    args.prepared_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.prepared_manifest.write_text(json.dumps(prepared, indent=2) + "\n")


if __name__ == "__main__":
    main()
