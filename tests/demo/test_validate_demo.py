from pathlib import Path

import pytest

from scripts.demo.validate_demo import scan_text_tree, validate_evidence_ids


def test_text_scan_reports_private_paths_and_secrets(tmp_path: Path) -> None:
    private_path = "/" + "Users/alice/private"
    token = "sk" + "-abcdefghijk"
    (tmp_path / "bad.md").write_text(f"{private_path} {token}")
    with pytest.raises(ValueError, match="bad.md"):
        scan_text_tree(tmp_path)


def test_visible_evidence_must_exist_in_database(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "papertrail.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE evidence_passages (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO evidence_passages VALUES ('ev_present')")
    validate_evidence_ids(database, "Uses ev_present.")
    with pytest.raises(ValueError, match="ev_missing"):
        validate_evidence_ids(database, "Uses ev_missing.")
