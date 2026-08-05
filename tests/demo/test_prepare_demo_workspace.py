from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from papertrail.db import initialize
from scripts.demo.prepare_demo_workspace import prepare_workspace


def test_prepare_workspace_scrubs_profile_paths_and_history(tmp_path: Path) -> None:
    source = tmp_path / "private-home"
    source.mkdir()
    initialize(source / "papertrail.db")
    (source / "artifacts").mkdir()
    artifact = source / "artifacts" / "paper.pdf"
    artifact.write_bytes(b"%PDF-1.4 demo")
    (source / "profile.json").write_text(
        json.dumps(
            {
                "providers": {"reasoning_provider": "private-provider"},
                "secret": "token",
            }
        )
    )
    with sqlite3.connect(source / "papertrail.db") as db:
        db.execute(
            "INSERT INTO explicit_interest_profile VALUES (1, ?, 'ready', NULL, ?, ?)",
            (
                "private personal interest",
                "2026-08-04T00:00:00Z",
                "2026-08-04T00:00:00Z",
            ),
        )
        db.execute(
            "INSERT INTO papers VALUES (?, ?, ?, '', '[]', NULL, ?, 'preprint', ?)",
            ("paper_demo", "Demo", "demo", "https://arxiv.org/abs/2608.00001", "now"),
        )
        db.execute(
            "INSERT INTO paper_versions VALUES (?, ?, ?, ?, ?, 1)",
            ("version_demo", "paper_demo", "hash", artifact.as_uri(), "now"),
        )
        db.commit()

    target = tmp_path / "public-home"
    prepare_workspace(source, target, "Reliable agents under changing tools")

    profile = json.loads((target / "profile.json").read_text())
    assert profile["providers"]["reasoning_provider"] == "ollama"
    assert "token" not in json.dumps(profile).casefold()
    with sqlite3.connect(target / "papertrail.db") as db:
        explicit = db.execute("SELECT text FROM explicit_interest_profile").fetchone()[0]
        artifact_uri = db.execute("SELECT artifact_uri FROM paper_versions").fetchone()[0]
        dump = "\n".join(db.iterdump())
    assert explicit == "Reliable agents under changing tools"
    assert "private personal interest" not in dump
    assert artifact_uri == (target / "artifacts" / "paper.pdf").as_uri()
