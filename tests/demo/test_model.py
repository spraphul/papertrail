from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.demo.model import DemoManifest


def _write(path: Path, **overrides: object) -> Path:
    value = {
        "counts": {"papers": 40, "evidence": 2701, "figures": 302, "groups": 25, "blogs": 3},
        "dashboard_url": "http://127.0.0.1:8899",
        "routes": {"group": "cluster_demo", "paper": "paper_demo", "blog": "demo-blog"},
        "codex_prompt": "Find a falsifiable idea about adaptive agent memory.",
        "duration_seconds": 150,
        **overrides,
    }
    path.write_text(json.dumps(value))
    return path


def test_manifest_loads_approved_counts_and_routes(tmp_path: Path) -> None:
    value = DemoManifest.load(_write(tmp_path / "demo.json"))
    assert value.counts.papers == 40
    assert value.routes["group"] == "cluster_demo"
    assert value.duration_seconds == 150


def test_manifest_rejects_missing_route(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="group, paper, blog"):
        DemoManifest.load(_write(tmp_path / "demo.json", routes={"paper": "paper_demo"}))
