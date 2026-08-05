# PaperTrail Demo Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a reproducible 2.5-minute PaperTrail demo that moves from local onboarding through the personalized dashboard to genuine evidence-grounded Codex research, with no internal-provider or private-user material in the public repository.

**Architecture:** A small Python demo toolchain prepares a sanitized copy of a real PaperTrail home, records deterministic browser and terminal/Codex scenes with Playwright, assembles them with FFmpeg, and validates both the media and public tree before README publication. The corpus itself stays local; only scripts, a count/route manifest, final compressed media, captions, narration, and transcript are committed.

**Tech Stack:** Python 3.10+, SQLite, Playwright/Chromium, Codex CLI, PaperTrail MCP, FFmpeg/ffprobe, pytest, Ruff, HTML/CSS, Markdown/WebVTT.

---

## File structure

```text
pyproject.toml                              # adds the optional demo capture dependency
.gitignore                                 # ignores companion state and raw demo workspaces
README.md                                  # public-only provider docs and clickable demo hero
docs/ARCHITECTURE.md                       # portable Ollama/OpenAI provider boundary only
docs/demo/
  narration.md                             # final voice script with timecodes
  transcript.md                            # accessible full transcript and cited links
  papertrail-demo.vtt                      # captions
  papertrail-demo.mp4                      # final compressed video
  papertrail-demo-preview.webp             # 10–15 second clickable README preview
  papertrail-demo-poster.png               # static fallback/poster
scripts/demo/
  __init__.py
  model.py                                 # typed manifest and shared subprocess helpers
  demo.json                                # approved counts, routes, timings, and prompt
  prepare_demo_workspace.py                # copies and sanitizes a real PaperTrail home
  record_dashboard.py                      # deterministic Playwright product scenes
  record_terminal.py                       # real command capture rendered as terminal scenes
  record_codex.py                          # genuine Codex-to-PaperTrail MCP transcript capture
  assemble_demo.py                         # FFmpeg concat, audio, captions, poster, and preview
  validate_demo.py                         # media, evidence, URL, privacy, and repository gates
tests/demo/
  __init__.py
  test_model.py
  test_prepare_demo_workspace.py
  test_recording.py
  test_validate_demo.py
tests/test_public_surface.py                # prevents internal-only material from returning
```

Raw browser videos, copied indexes, command logs, Codex event streams, narration audio, and temporary frames live under `.demo-work/` and are never committed.

### Task 1: Restore a strictly public provider surface

**Files:**
- Create: `tests/test_public_surface.py`
- Modify: `src/papertrail/config.py`
- Modify: `src/papertrail/profile.py`
- Modify: `src/papertrail/cli.py`
- Modify: `src/papertrail/providers.py`
- Modify: `tests/test_public.py`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Write the failing public-surface regression test**

```python
# tests/test_public_surface.py
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
TEXT_SUFFIXES = {".md", ".py", ".toml", ".json", ".js", ".css", ".html"}
PUBLIC_ROOTS = (ROOT / "README.md", ROOT / "docs" / "ARCHITECTURE.md", ROOT / "src", ROOT / "tests")
FORBIDDEN = (
    "ai" + "factory",
    "oracle" + "cloud.com",
    "scm" + "service",
    "ai" + "factory_bearer_token",
)


def test_public_tree_contains_no_internal_provider_surface() -> None:
    offenders: list[str] = []
    paths = [root for root in PUBLIC_ROOTS if root.is_file()]
    paths.extend(path for root in PUBLIC_ROOTS if root.is_dir() for path in root.rglob("*"))
    for path in paths:
        if not path.is_file() or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        if any(part in {".git", ".venv", ".demo-work", ".superpowers"} for part in path.parts):
            continue
        if path == Path(__file__):
            continue
        text = path.read_text(errors="ignore").casefold()
        matches = [term for term in FORBIDDEN if term in text]
        if matches:
            offenders.append(f"{path.relative_to(ROOT)}: {', '.join(matches)}")
    assert not offenders, "internal-only content found:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run the test and verify that it exposes the current leak**

Run: `pytest tests/test_public_surface.py -v`

Expected: FAIL listing `README.md`, `docs/ARCHITECTURE.md`, `config.py`, `profile.py`, `cli.py`, `providers.py`, and the provider tests.

- [ ] **Step 3: Remove the internal adapter and configuration branches**

Make the public code expose exactly the portable providers:

```python
# src/papertrail/config.py — Settings provider fields after the edit
ollama_url: str = "http://127.0.0.1:11434"
openai_base_url: str = "https://api.openai.com/v1"
openai_api_key: str | None = None
embedding_model: str = "embeddinggemma"
reasoning_model: str = "qwen2.5:7b"
semantic_scholar_api_key: str | None = None
```

```python
# src/papertrail/profile.py — configure_runtime mapping after the edit
mapping = {
    "embedding_provider": "PAPERTRAIL_EMBEDDING_PROVIDER",
    "reasoning_provider": "PAPERTRAIL_REASONING_PROVIDER",
    "embedding_model": "PAPERTRAIL_EMBEDDING_MODEL",
    "reasoning_model": "PAPERTRAIL_REASONING_MODEL",
    "openai_base_url": "OPENAI_BASE_URL",
    "openai_api_key_file": "PAPERTRAIL_OPENAI_API_KEY_FILE",
}
```

```python
# src/papertrail/cli.py — both provider arguments
command.add_argument(
    "--embedding-provider", choices=("ollama", "openai"), default="ollama"
)
command.add_argument(
    "--reasoning-provider", choices=("ollama", "openai"), default="ollama"
)
```

Delete the two internal endpoint arguments, the third-provider validation block, its saved profile fields, and its setup response field. Reduce model defaults to:

```python
embedding_model = arguments.embedding_model or (
    "text-embedding-3-small"
    if arguments.embedding_provider == "openai"
    else "embeddinggemma"
)
reasoning_model = arguments.reasoning_model or (
    "gpt-5.6" if arguments.reasoning_provider == "openai" else "qwen2.5:7b"
)
```

Delete the internal-only provider class located between `OpenAIProvider` and `_strict_schema` in `src/papertrail/providers.py`; `_provider` must end as:

```python
def _provider(settings: Any, name: str) -> IntelligenceProvider:
    if name == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_url,
            embedding_model=settings.embedding_model,
            reasoning_model=settings.reasoning_model,
        )
    if name == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            embedding_model=settings.embedding_model,
            reasoning_model=settings.reasoning_model,
        )
    raise ValueError(f"Unknown PaperTrail provider {name!r}; use 'ollama' or 'openai'")
```

Remove the four internal-provider tests and import from `tests/test_public.py`. Rewrite README development text and the architecture introduction to describe Ollama and OpenAI only.

- [ ] **Step 4: Verify the public-only surface**

Run: `pytest tests/test_public_surface.py tests/test_public.py -v`

Expected: PASS.

Run: `ruff check src tests scripts`

Expected: `All checks passed!`

- [ ] **Step 5: Commit the public cleanup**

```bash
git add README.md docs/ARCHITECTURE.md src/papertrail/config.py src/papertrail/profile.py \
  src/papertrail/cli.py src/papertrail/providers.py tests/test_public.py tests/test_public_surface.py
git commit -m "fix: keep internal providers out of public package"
```

### Task 2: Add the typed demo manifest and local workspace boundary

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `scripts/demo/__init__.py`
- Create: `scripts/demo/model.py`
- Create: `scripts/demo/demo.json`
- Create: `tests/demo/__init__.py`
- Create: `tests/demo/test_model.py`

- [ ] **Step 1: Write failing manifest tests**

```python
# tests/demo/test_model.py
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
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest tests/demo/test_model.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.demo.model'`.

- [ ] **Step 3: Implement the manifest types**

```python
# scripts/demo/model.py
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class DemoCounts:
    papers: int
    evidence: int
    figures: int
    groups: int
    blogs: int


@dataclass(frozen=True)
class DemoManifest:
    counts: DemoCounts
    dashboard_url: str
    routes: dict[str, str]
    codex_prompt: str
    duration_seconds: int

    @classmethod
    def load(cls, path: Path) -> "DemoManifest":
        value = json.loads(path.read_text())
        routes = {str(key): str(item) for key, item in value["routes"].items()}
        if not {"group", "paper", "blog"}.issubset(routes):
            raise ValueError("routes must include group, paper, blog")
        counts = DemoCounts(**{key: int(item) for key, item in value["counts"].items()})
        if min(vars(counts).values()) < 1:
            raise ValueError("all demo counts must be positive")
        return cls(
            counts=counts,
            dashboard_url=str(value["dashboard_url"]).rstrip("/"),
            routes=routes,
            codex_prompt=str(value["codex_prompt"]).strip(),
            duration_seconds=int(value["duration_seconds"]),
        )


def run_checked(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)
```

Create empty `scripts/demo/__init__.py` and `tests/demo/__init__.py`. Seed `scripts/demo/demo.json` with the approved 40/2701/302/25/3 counts, port `8899`, the real route IDs selected during workspace preparation, duration `150`, and this exact prompt:

```text
Use $papertrail-deep-research to investigate whether agent memory can detect and adapt to changing tool interfaces. Search broadly across the local corpus, inspect exact evidence and at least one relevant paper figure, challenge the emerging pattern against nearby prior work, and propose one falsifiable paper idea. Cite PaperTrail evidence IDs and public source URLs. State clearly that novelty is bounded by this corpus.
```

- [ ] **Step 4: Add demo dependencies and ignore raw work**

```toml
# pyproject.toml
[project.optional-dependencies]
pdf = ["pypdf>=5.0"]
scale = ["sqlite-vec>=0.1.9,<0.2"]
dev = ["pytest>=8.0", "ruff>=0.9"]
demo = ["playwright>=1.54,<2"]
```

```gitignore
# .gitignore
.superpowers/
.demo-work/
docs/demo/raw/
```

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m pip install -e '.[dev,demo]' && playwright install chromium`

Expected: editable install succeeds and Chromium is installed.

Run: `pytest tests/demo/test_model.py -v && ruff check scripts tests/demo`

Expected: PASS and `All checks passed!`

```bash
git add pyproject.toml .gitignore scripts/demo tests/demo
git commit -m "build: add reproducible demo manifest"
```

### Task 3: Prepare and sanitize the real demo workspace

**Files:**
- Create: `scripts/demo/prepare_demo_workspace.py`
- Create: `tests/demo/test_prepare_demo_workspace.py`
- Modify: `scripts/demo/demo.json`

- [ ] **Step 1: Write the failing sanitizer test**

```python
# tests/demo/test_prepare_demo_workspace.py
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
    artifact = source / "artifacts" / "papers" / "paper.pdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"%PDF-1.4 demo")
    (source / "profile.json").write_text(
        json.dumps({"providers": {"reasoning_provider": "private-provider"}, "secret": "token"})
    )
    with sqlite3.connect(source / "papertrail.db") as db:
        db.execute(
            "INSERT INTO explicit_interest_profile VALUES (1, ?, 'ready', NULL, ?, ?)",
            ("private personal interest", "2026-08-04T00:00:00Z", "2026-08-04T00:00:00Z"),
        )
        db.commit()

    target = tmp_path / "public-home"
    prepare_workspace(source, target, "Reliable agents under changing tools")

    profile = json.loads((target / "profile.json").read_text())
    assert profile["providers"]["reasoning_provider"] == "ollama"
    assert "token" not in json.dumps(profile).casefold()
    with sqlite3.connect(target / "papertrail.db") as db:
        explicit = db.execute("SELECT text FROM explicit_interest_profile").fetchone()[0]
    assert explicit == "Reliable agents under changing tools"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/demo/test_prepare_demo_workspace.py -v`

Expected: FAIL because `prepare_workspace` does not exist.

- [ ] **Step 3: Implement safe copy, database scrubbing, and path rewriting**

```python
# scripts/demo/prepare_demo_workspace.py
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


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
            path = Path(uri)
            if path.is_absolute():
                try:
                    relative = path.relative_to(source)
                except ValueError as error:
                    raise ValueError(f"artifact escapes source home: {path}") from error
                db.execute(
                    f"UPDATE {table} SET artifact_uri = ? WHERE rowid = ?",
                    (str(target / relative), rowid),
                )


def prepare_workspace(source: Path, target: Path, interests: str) -> None:
    source, target = source.resolve(), target.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite {target}")
    if not (source / "papertrail.db").is_file():
        raise FileNotFoundError(source / "papertrail.db")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("*.log", "*.pid", "*-error.log"))
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
        db.execute(
            "UPDATE preference_profile_versions SET summary_json = ?",
            (json.dumps({"concepts": [{"label": interests, "polarity": "positive"}]}),),
        )
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-home", type=Path, required=True)
    parser.add_argument("--output-home", type=Path, required=True)
    parser.add_argument("--interests-file", type=Path, required=True)
    args = parser.parse_args()
    prepare_workspace(args.source_home, args.output_home, args.interests_file.read_text().strip())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add invariant checks and emit the selected real routes**

Add this helper and call it at the end of `main`; it queries totals, the latest organization’s largest useful group, a paper in that group with an available PDF and figures, and the newest complete blog. It fails if a source is not public arXiv, an artifact is missing, or counts differ.

```python
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
                "SELECT count(*) FROM paper_clusters WHERE organization_run_id=?", (run["id"],)
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
              AND EXISTS (SELECT 1 FROM paper_versions v WHERE v.paper_id=p.id AND v.is_current=1)
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
        artifacts = [Path(row[0]) for row in db.execute("SELECT artifact_uri FROM paper_versions")]
        artifacts += [Path(row[0]) for row in db.execute("SELECT artifact_uri FROM visual_evidence")]
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
```

Import `DemoCounts` and `DemoManifest`, add `--manifest` and `--prepared-manifest` path arguments, then finish `main` with:

```python
manifest = DemoManifest.load(args.manifest)
prepared = inspect_workspace(args.output_home.resolve(), manifest.counts)
args.prepared_manifest.parent.mkdir(parents=True, exist_ok=True)
args.prepared_manifest.write_text(json.dumps(prepared, indent=2) + "\n")
```

The emitted structure must be:

```json
{
  "counts": {"papers": 40, "evidence": 2701, "figures": 302, "groups": 25, "blogs": 3},
  "routes": {"group": "cluster_...", "paper": "paper_...", "blog": "..."},
  "source_urls": ["https://arxiv.org/abs/..."]
}
```

Copy the selected public IDs into `scripts/demo/demo.json`; never commit the copied database.

- [ ] **Step 5: Test and commit**

Run: `pytest tests/demo/test_prepare_demo_workspace.py tests/demo/test_model.py -v`

Expected: PASS.

```bash
git add scripts/demo/prepare_demo_workspace.py scripts/demo/demo.json tests/demo/test_prepare_demo_workspace.py
git commit -m "feat: prepare sanitized PaperTrail demo data"
```

### Task 4: Record deterministic dashboard scenes

**Files:**
- Create: `scripts/demo/record_dashboard.py`
- Create: `tests/demo/test_recording.py`
- Modify: `scripts/demo/demo.json`

- [ ] **Step 1: Write a failing scene-contract test**

```python
# tests/demo/test_recording.py
from pathlib import Path

from scripts.demo.model import DemoManifest
from scripts.demo.record_terminal import _terminal_html, record_html_scene
from scripts.demo.record_dashboard import scene_urls


def test_dashboard_story_visits_every_approved_product_route() -> None:
    manifest = DemoManifest.load(Path("scripts/demo/demo.json"))
    urls = scene_urls(manifest)
    assert urls[0].endswith("/#/")
    assert any("#/groups/" in url for url in urls)
    assert any("#/blog/" in url for url in urls)
    assert any("#/paper/" in url for url in urls)
    assert urls[-1].endswith("#/favorites")
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/demo/test_recording.py -v`

Expected: FAIL because `record_dashboard` does not exist.

- [ ] **Step 3: Implement the route contract and Playwright capture**

```python
# scripts/demo/record_dashboard.py
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import Page, sync_playwright

from scripts.demo.model import DemoManifest


def scene_urls(manifest: DemoManifest) -> list[str]:
    root = manifest.dashboard_url
    return [
        f"{root}/#/",
        f"{root}/#/groups",
        f"{root}/#/groups/{quote(manifest.routes['group'])}",
        f"{root}/#/blog/{quote(manifest.routes['blog'])}",
        f"{root}/#/paper/{quote(manifest.routes['paper'])}",
        f"{root}/#/favorites",
    ]


def _ready(page: Page, selector: str) -> None:
    page.wait_for_selector(selector, state="visible", timeout=15_000)
    page.wait_for_timeout(900)


def record(manifest: DemoManifest, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            record_video_dir=str(output),
            record_video_size={"width": 1920, "height": 1080},
            reduced_motion="reduce",
        )
        page = context.new_page()
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto(scene_urls(manifest)[0], wait_until="networkidle")
        _ready(page, ".interest-card")
        page.locator(".stats").scroll_into_view_if_needed()
        page.wait_for_timeout(1800)
        for url, selector in zip(scene_urls(manifest)[1:], ("h1", ".group-detail-list", ".article", ".paper-frame", ".favorite-list")):
            page.goto(url, wait_until="networkidle")
            _ready(page, selector)
            page.mouse.wheel(0, 520)
            page.wait_for_timeout(1800)
        video = page.video
        context.close()
        browser.close()
        if console_errors:
            raise RuntimeError("browser console errors: " + " | ".join(console_errors))
        if video is None:
            raise RuntimeError("Playwright did not create a video")
        video.save_as(output / "dashboard.webm")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("scripts/demo/demo.json"))
    parser.add_argument("--output", type=Path, default=Path(".demo-work/scenes"))
    args = parser.parse_args()
    record(DemoManifest.load(args.manifest), args.output)


if __name__ == "__main__":
    main()
```

Replace the single-take `record` body with this scene loop so each approved beat is independently retimeable. The `focus` selector receives the deliberate reading hold.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Scene:
    name: str
    url: str
    ready: str
    focus: str
    hold_ms: int


def dashboard_scenes(manifest: DemoManifest) -> list[Scene]:
    urls = scene_urls(manifest)
    return [
        Scene("interests", urls[0], ".interest-card", ".interest-card", 6000),
        Scene("groups", urls[1], ".group-grid", ".group-grid", 7000),
        Scene("group-detail", urls[2], ".group-detail-list", ".ranking-reasons", 8000),
        Scene("deep-dive", urls[3], ".article", ".selection-panel", 8000),
        Scene("paper-reader", urls[4], ".paper-frame", ".reader-figures", 9000),
        Scene("favorites", urls[5], ".favorite-list", ".favorite-list", 6000),
    ]


def record(manifest: DemoManifest, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for scene in dashboard_scenes(manifest):
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=str(output),
                record_video_size={"width": 1920, "height": 1080},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.goto(scene.url, wait_until="networkidle")
            _ready(page, scene.ready)
            page.locator(scene.focus).first.scroll_into_view_if_needed()
            if scene.name == "group-detail":
                star = page.locator("[data-favorite-paper]").first
                with page.expect_response(lambda response: "/v1/favorites/" in response.url):
                    star.click()
            page.wait_for_timeout(scene.hold_ms)
            video = page.video
            context.close()
            if video is None:
                raise RuntimeError(f"Playwright did not record {scene.name}")
            video.save_as(output / f"dashboard-{scene.name}.webm")
        browser.close()
    if console_errors:
        raise RuntimeError("browser console errors: " + " | ".join(console_errors))
```

- [ ] **Step 4: Verify against the sanitized live dashboard**

Run the server:

```bash
PAPERTRAIL_HOME="$PWD/.demo-work/papertrail-home" papertrail serve --host 127.0.0.1 --port 8899
```

In another terminal run:

```bash
python -m scripts.demo.record_dashboard --output .demo-work/scenes
ffprobe -v error -show_entries stream=width,height -of json .demo-work/scenes/dashboard.webm
```

Expected: no console errors and a 1920×1080 video stream.

- [ ] **Step 5: Commit**

```bash
git add scripts/demo/record_dashboard.py tests/demo/test_recording.py scripts/demo/demo.json
git commit -m "feat: record deterministic dashboard walkthrough"
```

### Task 5: Capture truthful onboarding and Codex research scenes

**Files:**
- Create: `scripts/demo/record_terminal.py`
- Create: `scripts/demo/record_codex.py`
- Create: `tests/demo/test_codex_capture.py`
- Modify: `tests/demo/test_recording.py`

- [ ] **Step 1: Write failing redaction and evidence-transcript tests**

```python
# tests/demo/test_codex_capture.py
from scripts.demo.record_codex import extract_evidence_ids, redact_transcript


def test_codex_transcript_keeps_evidence_and_removes_private_paths() -> None:
    raw = "Found ev_abc123 in /Users/private/.papertrail using token sk-secret"
    clean = redact_transcript(raw, replacements={"/Users/private": "<demo-home>"})
    assert extract_evidence_ids(clean) == {"ev_abc123"}
    assert "/Users/private" not in clean
    assert "sk-secret" not in clean


def test_codex_transcript_requires_evidence() -> None:
    assert extract_evidence_ids("unsupported answer") == set()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/demo/test_codex_capture.py -v`

Expected: FAIL because the capture module does not exist.

- [ ] **Step 3: Implement genuine Codex capture and conservative redaction**

```python
# scripts/demo/record_codex.py
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from scripts.demo.model import DemoManifest


EVIDENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9._-]{12,})\b")


def extract_evidence_ids(text: str) -> set[str]:
    return set(EVIDENCE.findall(text))


def redact_transcript(text: str, replacements: dict[str, str]) -> str:
    for private, public in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(private, public)
    return SECRET.sub("[REDACTED]", text)


def capture(
    manifest: DemoManifest, home: Path, work: Path, output: Path, scene_output: Path
) -> None:
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["papertrail", "--home", str(home), "connect", "codex", "--scope", "project", "--force"],
        cwd=work,
        check=True,
    )
    environment = {**os.environ, "PAPERTRAIL_HOME": str(home)}
    result = subprocess.run(
        ["codex", "exec", "--json", "--output-last-message", str(output), manifest.codex_prompt],
        cwd=work,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    clean = redact_transcript(output.read_text(), {str(home): "<papertrail-home>", str(work): "<demo-project>"})
    if not extract_evidence_ids(clean):
        raise RuntimeError("Codex answer contains no PaperTrail evidence IDs")
    output.write_text(clean)
    (output.with_suffix(".events.jsonl")).write_text(redact_transcript(result.stdout, {str(home): "<papertrail-home>", str(work): "<demo-project>"}))
    page = output.with_suffix(".html")
    markup = _terminal_html(["$ codex", manifest.codex_prompt, "", clean]).replace(
        "</style>",
        "pre{animation:research-scroll 29s linear 2s forwards}"
        "@keyframes research-scroll{to{transform:translateY(-58%)}}</style>",
    )
    page.write_text(markup)
    record_html_scene(page, scene_output, "codex", 33_000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("scripts/demo/demo.json"))
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=Path(".demo-work/codex-project"))
    parser.add_argument("--output", type=Path, default=Path(".demo-work/codex-answer.md"))
    parser.add_argument("--scene-output", type=Path, default=Path(".demo-work/scenes"))
    args = parser.parse_args()
    capture(
        DemoManifest.load(args.manifest), args.home.resolve(), args.work.resolve(),
        args.output, args.scene_output,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement terminal rendering from real commands**

Use this implementation. It records raw output first, selects only lines that actually occurred, escapes them, and renders a 1920×1080 terminal page with Playwright.

```python
# scripts/demo/record_terminal.py
from __future__ import annotations

import argparse
import html
import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright


COMMANDS = [
    ["python3", "-m", "venv", ".demo-work/onboarding-venv"],
    [".demo-work/onboarding-venv/bin/pip", "install", "-e", ".[pdf]"],
    [
        ".demo-work/onboarding-venv/bin/papertrail",
        "--home",
        ".demo-work/onboarding-home",
        "setup",
        "--interests-file",
        ".demo-work/interests.md",
        "--client",
        "codex",
        "--no-schedule",
        "--no-dashboard",
    ],
]


def _run(command: list[str], root: Path, log: Path) -> str:
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=True)
    output = result.stdout + result.stderr
    with log.open("a") as stream:
        stream.write("$ " + " ".join(command) + "\n" + output + "\n")
    return output


def _terminal_html(lines: list[str]) -> str:
    content = "\n".join(html.escape(line) for line in lines)
    return f"""<!doctype html><meta charset="utf-8"><style>
    body{{margin:0;background:#20231f;color:#e8eee4;font:27px/1.7 ui-monospace,monospace}}
    main{{box-sizing:border-box;width:1920px;height:1080px;padding:105px 125px}}
    .prompt{{color:#8fba8b}} .ok{{color:#d99a7c}}
    </style><main><pre>{content}</pre></main>"""


def _editorial_html(eyebrow: str, headline: str, detail: str) -> str:
    return f"""<!doctype html><meta charset="utf-8"><style>
    body{{margin:0;background:#f5f0e6;color:#332f2a}}
    main{{box-sizing:border-box;width:1920px;height:1080px;padding:150px;display:flex;
      flex-direction:column;justify-content:center}}
    small{{font:22px Arial;color:#b76549;letter-spacing:.12em;text-transform:uppercase}}
    h1{{font:78px/1.04 Georgia;margin:28px 0;max-width:1500px}}
    p{{font:30px/1.5 Arial;color:#655c52;max-width:1400px}}
    </style><main><small>{html.escape(eyebrow)}</small><h1>{html.escape(headline)}</h1>
    <p>{html.escape(detail)}</p></main>"""


def record_html_scene(page_path: Path, output: Path, name: str, duration_ms: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(output),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto(page_path.resolve().as_uri())
        page.wait_for_timeout(duration_ms)
        video = page.video
        context.close()
        browser.close()
        if video is None:
            raise RuntimeError(f"{name} video was not created")
        video.save_as(output / f"{name}.webm")


def record(root: Path, output: Path) -> None:
    work = root / ".demo-work"
    work.mkdir(exist_ok=True)
    interests = work / "interests.md"
    interests.write_text("Reliable agents that adapt to changing tools and environments.\n")
    raw_log = work / "onboarding.log"
    outputs = [_run(command, root, raw_log) for command in COMMANDS]
    setup = json.loads(outputs[-1])
    if setup.get("status") != "ready":
        raise RuntimeError(f"setup did not become ready: {setup}")
    pip_success = next(
        (line for line in reversed(outputs[1].splitlines()) if "Successfully installed" in line),
        "",
    )
    if not pip_success:
        raise RuntimeError("wheel installation did not report success")
    lines = [
        '<span class="prompt">$</span> pip install papertrail-local',
        f'<span class="ok">{pip_success}</span>',
        '<span class="prompt">$</span> papertrail setup --interests-file interests.md --client codex',
        '<span class="ok">✓ local index · ✓ MCP · ✓ daily workflow</span>',
    ]
    page_path = work / "onboarding.html"
    page_path.write_text(_terminal_html(lines))
    cards = {
        "pain": _editorial_html("The daily surplus", "205 papers today. Which three matter?", "PaperTrail builds reusable evidence and connections—not 205 disposable summaries."),
        "daily-run": _editorial_html("Daily intelligence", "205 → 40 → 2,701 → 302 → 25 → 3", "Discovered → enriched → evidence → figures → neighborhoods → deep dives"),
        "handoff": _editorial_html("Open source · local first", "Turn the paper surplus into a research trail.", "Install PaperTrail. Bring Ollama or OpenAI. Connect Codex or Claude."),
    }
    for name, markup in cards.items():
        card = work / f"{name}.html"
        card.write_text(markup)
        record_html_scene(card, output, name, {"pain": 18_000, "daily-run": 16_000, "handoff": 15_000}[name])
    record_html_scene(page_path, output, "terminal", 24_000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path(".demo-work/scenes"))
    args = parser.parse_args()
    record(args.root.resolve(), args.output)


if __name__ == "__main__":
    main()
```

The rendered scene shortens `python3 -m pip install -e '.[pdf]'` to the release-facing `pip install papertrail-local` only after verifying the built wheel installs and exposes the same command. Keep the raw audit log in `.demo-work`.

- [ ] **Step 5: Run the genuine capture and inspect evidence**

Run:

```bash
python -m scripts.demo.record_terminal
python -m scripts.demo.record_codex --home .demo-work/papertrail-home
rg -n 'ev_|https://arxiv.org/abs/' .demo-work/codex-answer.md
```

Expected: at least two resolvable evidence IDs and public arXiv URLs, with no private paths or credentials.

- [ ] **Step 6: Test and commit**

Run: `pytest tests/demo/test_recording.py tests/demo/test_codex_capture.py -v`

Expected: PASS.

```bash
git add scripts/demo/record_terminal.py scripts/demo/record_codex.py \
  tests/demo/test_recording.py tests/demo/test_codex_capture.py
git commit -m "feat: capture onboarding and genuine Codex research"
```

### Task 6: Write narration, captions, transcript, and assemble the media

**Files:**
- Create: `docs/demo/narration.md`
- Create: `docs/demo/papertrail-demo.vtt`
- Create: `docs/demo/transcript.md`
- Create: `scripts/demo/scenes.json`
- Create: `scripts/demo/assemble_demo.py`
- Create: `tests/demo/test_assembly.py`

- [ ] **Step 1: Write the failing timeline test**

```python
# tests/demo/test_assembly.py
import json
from pathlib import Path

from scripts.demo.assemble_demo import validate_timeline


def test_timeline_is_contiguous_and_matches_approved_duration() -> None:
    scenes = json.loads(Path("scripts/demo/scenes.json").read_text())
    validate_timeline(scenes, expected_duration=150)
    assert scenes[0]["start"] == 0
    assert scenes[-1]["end"] == 150
    assert all(left["end"] == right["start"] for left, right in zip(scenes, scenes[1:]))
```

- [ ] **Step 2: Create the approved timeline**

```json
[
  {"id":"pain","start":0,"end":18,"source":"pain.webm"},
  {"id":"onboarding","start":18,"end":42,"source":"terminal.webm"},
  {"id":"daily-run","start":42,"end":58,"source":"daily-run.webm"},
  {"id":"interests","start":58,"end":64,"source":"dashboard-interests.webm"},
  {"id":"groups","start":64,"end":71,"source":"dashboard-groups.webm"},
  {"id":"group-detail","start":71,"end":79,"source":"dashboard-group-detail.webm"},
  {"id":"deep-dive","start":79,"end":87,"source":"dashboard-deep-dive.webm"},
  {"id":"paper-reader","start":87,"end":96,"source":"dashboard-paper-reader.webm"},
  {"id":"favorites","start":96,"end":102,"source":"dashboard-favorites.webm"},
  {"id":"codex","start":102,"end":135,"source":"codex.webm"},
  {"id":"handoff","start":135,"end":150,"source":"handoff.webm"}
]
```

Run: `pytest tests/demo/test_assembly.py -v`

Expected: FAIL because `validate_timeline` does not exist.

- [ ] **Step 3: Implement deterministic FFmpeg assembly**

```python
# scripts/demo/assemble_demo.py
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.demo.model import run_checked


def validate_timeline(scenes: list[dict], expected_duration: int) -> None:
    if not scenes or scenes[0]["start"] != 0 or scenes[-1]["end"] != expected_duration:
        raise ValueError("timeline does not span the approved duration")
    for left, right in zip(scenes, scenes[1:]):
        if left["end"] != right["start"]:
            raise ValueError(f"timeline gap between {left['id']} and {right['id']}")
    if any(scene["end"] <= scene["start"] for scene in scenes):
        raise ValueError("scene durations must be positive")


def assemble(scenes_path: Path, raw: Path, narration: Path, captions: Path, output: Path) -> None:
    scenes = json.loads(scenes_path.read_text())
    validate_timeline(scenes, 150)
    normalized = raw / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    normalized_files: list[Path] = []
    for index, scene in enumerate(scenes):
        duration = scene["end"] - scene["start"]
        source = raw / scene["source"]
        if not source.is_file():
            raise FileNotFoundError(source)
        target = normalized / f"{index:02d}-{scene['id']}.mp4"
        run_checked([
            "ffmpeg", "-y", "-i", str(source), "-an", "-vf",
            f"scale=1920:1080,fps=30,tpad=stop_mode=clone:stop_duration=60,"
            f"trim=duration={duration},setpts=PTS-STARTPTS,format=yuv420p",
            "-c:v", "libx264", "-crf", "18", str(target),
        ])
        normalized_files.append(target)
    concat = raw / "concat.txt"
    concat.write_text("".join(f"file '{path.resolve()}'\n" for path in normalized_files))
    output.parent.mkdir(parents=True, exist_ok=True)
    transition_windows = (
        "between(t,0,1)+between(t,17,19)+between(t,41,43)+"
        "between(t,57,59)+between(t,101,103)+between(t,134,136)+between(t,149,150)"
    )
    run_checked([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(narration), "-f", "lavfi", "-i",
        "anoisesrc=color=pink:duration=150:amplitude=0.02", "-filter_complex",
        f"[0:v]scale=1920:1080,fps=30,subtitles={captions}:"
        "force_style='FontName=Arial,FontSize=18,MarginV=48,Outline=1',format=yuv420p[v];"
        "[1:a]highpass=f=70,loudnorm=I=-16:LRA=7:TP=-1.5[voice];"
        f"[2:a]highpass=f=120,lowpass=f=900,volume='{transition_windows}*0.035'[bed];"
        "[voice][bed]amix=inputs=2:duration=first:normalize=0[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow",
        "-crf", "25", "-c:a", "aac",
        "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(output),
    ])
    run_checked([
        "ffmpeg", "-y", "-ss", "58", "-t", "12", "-i", str(output),
        "-vf", "fps=10,scale=960:-1:flags=lanczos", "-loop", "0",
        str(output.with_name("papertrail-demo-preview.webp")),
    ])
    run_checked([
        "ffmpeg", "-y", "-ss", "68", "-i", str(output), "-frames:v", "1",
        str(output.with_name("papertrail-demo-poster.png")),
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenes", type=Path, default=Path("scripts/demo/scenes.json"))
    parser.add_argument("--raw", type=Path, default=Path(".demo-work/scenes"))
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--captions", type=Path, default=Path("docs/demo/papertrail-demo.vtt"))
    parser.add_argument("--output", type=Path, default=Path("docs/demo/papertrail-demo.mp4"))
    args = parser.parse_args()
    assemble(args.scenes, args.raw, args.narration, args.captions, args.output)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write the exact voice and accessibility text**

Use this approved narration as `docs/demo/narration.md`; only tighten wording if the recorded read exceeds its scene boundary:

```markdown
## 0:00–0:18 · The pain

Two hundred and five new AI papers arrived today. I do not need two hundred and five
summaries. I need to know which three deserve attention, what evidence they actually
contain, and whether they change an idea I am already exploring.

## 0:18–0:42 · Local onboarding

This is PaperTrail. Install the Python package, describe your interests in ordinary
Markdown, and connect Codex or Claude. The index, paper artifacts, preferences, and
research history stay in a local PaperTrail home. Ollama works fully locally, while the
OpenAI provider lets you bring any compatible embedding and reasoning models available
to your account.

## 0:42–0:58 · Daily run

One daily command discovered all two hundred and five papers, then ranked the surplus
before downloading expensive PDFs. Forty were enriched into two thousand seven hundred
and one evidence passages and three hundred and two figures. Sixty percent of the cap
follows your interests, twenty percent explores their frontier, and twenty percent keeps
room for surprise.

## 0:58–1:42 · Dashboard

The dashboard explains that process instead of hiding it. Edit the interest profile in
plain language. See why each paper ranked: relevance, recency, citation context, and the
reasoning model's reading of its problem and mechanism. Twenty-five problem neighborhoods
consolidate papers that target the same research question. Open the full group to inspect
every member, save useful papers to a compact library, or read one of three personalized
deep dives. Each essay links back to the public source and exact local evidence. Figures
come from the acquired paper, and View paper opens the local PDF reader rather than sending
you to the same abstract page.

## 1:42–2:15 · Codex research

The index is also an MCP research surface. Here Codex searches the agent-memory thread,
opens exact passages, checks a figure, and challenges the apparent pattern against nearby
work. The answer cites PaperTrail evidence IDs and public paper URLs, then turns the gap
into a falsifiable proposal: test whether memory policies that detect interface drift can
separate genuine adaptation from benchmark-specific recovery. This is not a claim of
global novelty. It is an auditable hypothesis bounded by the papers actually indexed.

## 2:15–2:30 · Handoff

PaperTrail is open source, local first, and deliberately small: Python, SQLite, your model
provider, and the research clients you already use. Install it, give it your interests,
and let each day's paper surplus become a trail you can search, question, and build on.
```

Use this complete `papertrail-demo.vtt` timing draft; adjust cue boundaries only to match the final spoken waveform, never the wording:

```vtt
WEBVTT

00:00:00.000 --> 00:00:06.000
Two hundred and five new AI papers arrived today.

00:00:06.000 --> 00:00:12.000
I do not need two hundred and five summaries.
I need to know which three deserve attention,

00:00:12.000 --> 00:00:18.000
what evidence they contain, and whether they change
an idea I am already exploring.

00:00:18.000 --> 00:00:24.000
This is PaperTrail. Install the Python package,
then describe your interests in ordinary Markdown.

00:00:24.000 --> 00:00:30.000
Connect Codex or Claude. The index, paper artifacts,
preferences, and research history stay local.

00:00:30.000 --> 00:00:36.000
Ollama works fully locally.
The OpenAI provider lets you bring compatible models.

00:00:36.000 --> 00:00:42.000
Use the embedding and reasoning models
available to your own account.

00:00:42.000 --> 00:00:47.300
One daily command discovered all 205 papers,
then ranked them before downloading PDFs.

00:00:47.300 --> 00:00:52.600
Forty became 2,701 evidence passages,
302 figures, and 25 research neighborhoods.

00:00:52.600 --> 00:00:58.000
The cap is 60 percent interests, 20 percent frontier,
and 20 percent deliberate surprise.

00:00:58.000 --> 00:01:04.200
The dashboard explains that process.
Edit your interest profile in plain language.

00:01:04.200 --> 00:01:10.400
See why each paper ranked: relevance, recency,
citation context, problem, and mechanism.

00:01:10.400 --> 00:01:16.600
Problem neighborhoods consolidate papers
that target the same research question.

00:01:16.600 --> 00:01:22.800
Open the full group to inspect every member,
or save useful papers to a compact library.

00:01:22.800 --> 00:01:29.200
Read one of three personalized deep dives.
Each links to the public source and exact evidence.

00:01:29.200 --> 00:01:35.600
Figures come from the acquired paper.
View paper opens the local PDF reader.

00:01:35.600 --> 00:01:42.000
The source action remains a distinct link
to the public arXiv page.

00:01:42.000 --> 00:01:48.600
The index is also an MCP research surface.
Codex searches the agent-memory thread.

00:01:48.600 --> 00:01:55.200
It opens exact passages, checks a figure,
and challenges the pattern against nearby work.

00:01:55.200 --> 00:02:01.800
The answer cites evidence IDs and paper URLs,
then turns the gap into a falsifiable proposal.

00:02:01.800 --> 00:02:08.400
Can memory policies detect interface drift and separate
real adaptation from benchmark-specific recovery?

00:02:08.400 --> 00:02:15.000
This is not a claim of global novelty.
It is an auditable, corpus-bounded hypothesis.

00:02:15.000 --> 00:02:20.000
PaperTrail is open source, local first,
and deliberately small.

00:02:20.000 --> 00:02:25.000
Python, SQLite, your model provider,
and the research clients you already use.

00:02:25.000 --> 00:02:30.000
Give it your interests, and turn each day's surplus
into a trail you can search, question, and build on.
```

Write `transcript.md` with the complete narration above, the exact visible commands, the full cleaned Codex answer, evidence IDs, and linked arXiv sources.

- [ ] **Step 5: Assemble and commit the text/tooling, not raw footage yet**

Run: `pytest tests/demo/test_assembly.py -v && ruff check scripts/demo tests/demo`

Expected: PASS and `All checks passed!`

```bash
git add docs/demo/narration.md docs/demo/papertrail-demo.vtt docs/demo/transcript.md \
  scripts/demo/scenes.json scripts/demo/assemble_demo.py tests/demo/test_assembly.py
git commit -m "feat: add narrated demo assembly pipeline"
```

### Task 7: Enforce media, evidence, link, and privacy release gates

**Files:**
- Create: `scripts/demo/validate_demo.py`
- Create: `tests/demo/test_validate_demo.py`

- [ ] **Step 1: Write failing validator tests**

```python
# tests/demo/test_validate_demo.py
from pathlib import Path

import pytest

from scripts.demo.validate_demo import scan_text_tree, validate_evidence_ids


def test_text_scan_reports_private_paths_and_secrets(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text("/Users/alice/private sk-abcdefghijk")
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/demo/test_validate_demo.py -v`

Expected: FAIL because `validate_demo` does not exist.

- [ ] **Step 3: Implement the release validator**

```python
# scripts/demo/validate_demo.py
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from scripts.demo.model import run_checked


EVIDENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
PRIVATE = (
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9._-]{12,}\b"),
    re.compile(("ai" + "factory"), re.IGNORECASE),
    re.compile("oracle" + r"cloud\.com", re.IGNORECASE),
)


def scan_text_tree(root: Path) -> None:
    offenders: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", ".demo-work", ".superpowers"} for part in path.parts):
            continue
        if "docs/superpowers" in path.as_posix():
            continue
        if path.suffix.casefold() not in {".md", ".py", ".toml", ".json", ".vtt", ".js", ".css", ".html"}:
            continue
        text = path.read_text(errors="ignore")
        if any(pattern.search(text) for pattern in PRIVATE):
            offenders.append(str(path))
    if offenders:
        raise ValueError("private material: " + ", ".join(offenders))


def validate_evidence_ids(database: Path, text: str) -> None:
    visible = EVIDENCE.findall(text)
    if not visible:
        raise ValueError("demo transcript contains no evidence IDs")
    marks = ",".join("?" for _ in visible)
    with sqlite3.connect(database) as db:
        found = {row[0] for row in db.execute(f"SELECT id FROM evidence_passages WHERE id IN ({marks})", visible)}
    missing = sorted(set(visible) - found)
    if missing:
        raise ValueError("missing evidence IDs: " + ", ".join(missing))


def probe(video: Path) -> dict:
    result = run_checked([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,width,height,r_frame_rate",
        "-of", "json", str(video),
    ])
    return json.loads(result.stdout)


def validate_video(video: Path) -> None:
    value = probe(video)
    duration = float(value["format"]["duration"])
    if not 140 <= duration <= 160:
        raise ValueError(f"video duration {duration:.2f}s is outside 140–160s")
    if int(value["format"]["size"]) > 25 * 1024 * 1024:
        raise ValueError("video exceeds 25 MiB")
    stream = next(item for item in value["streams"] if item["codec_type"] == "video")
    if (stream["width"], stream["height"], stream["r_frame_rate"]) != (1920, 1080, "30/1"):
        raise ValueError(f"unexpected video stream: {stream}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--video", type=Path, default=Path("docs/demo/papertrail-demo.mp4"))
    parser.add_argument("--transcript", type=Path, default=Path("docs/demo/transcript.md"))
    args = parser.parse_args()
    scan_text_tree(args.repo)
    validate_video(args.video)
    validate_evidence_ids(args.home / "papertrail.db", args.transcript.read_text())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add live-route and visual review gates**

Add these functions and call them from `main`. They request the live health/dashboard routes, verify every selected route in Chromium, check public source URLs with a HEAD-or-GET fallback, validate the PDF byte range, compare counts, generate a contact sheet, and require the manual visual checklist.

```python
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

from scripts.demo.model import DemoManifest


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 400:
                raise ValueError(f"{url} returned HTTP {response.status}")
            return response.read()
    except urllib.error.HTTPError as error:
        raise ValueError(f"{url} returned HTTP {error.code}") from error


def validate_live_routes(manifest: DemoManifest) -> None:
    root = manifest.dashboard_url
    health = json.loads(_request(f"{root}/health"))
    if health.get("status") != "ok":
        raise ValueError(f"dashboard health failed: {health}")
    dashboard = json.loads(_request(f"{root}/v1/dashboard"))
    totals = dashboard["totals"]
    actual = {
        "papers": totals["papers"],
        "evidence": totals["evidence"],
        "figures": totals["figures"],
        "groups": dashboard["organization"]["cluster_count"],
        "blogs": totals["blogs"],
    }
    if actual != vars(manifest.counts):
        raise ValueError(f"live counts changed: {actual}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for fragment, selector in (
            (f"groups/{manifest.routes['group']}", ".group-detail-list"),
            (f"blog/{manifest.routes['blog']}", ".article"),
            (f"paper/{manifest.routes['paper']}", ".paper-frame"),
        ):
            page.goto(f"{root}/#/{fragment}", wait_until="networkidle")
            page.wait_for_selector(selector, timeout=15_000)
        source_urls = page.locator("a.source").evaluate_all("nodes => nodes.map(node => node.href)")
        browser.close()
    for url in source_urls:
        try:
            _request(url, method="HEAD")
        except ValueError:
            _request(url, headers={"Range": "bytes=0-0"})
    _request(
        f"{root}/v1/papers/{manifest.routes['paper']}/artifact",
        headers={"Range": "bytes=0-1023"},
    )


def validate_visual_review(video: Path, review_path: Path) -> None:
    contact = review_path.with_name("contact-sheet.png")
    run_checked([
        "ffmpeg", "-y", "-i", str(video), "-vf",
        "fps=1/5,scale=480:-1,tile=5x6", "-frames:v", "1", str(contact),
    ])
    review = json.loads(review_path.read_text())
    required = {
        "no_private_text_visible",
        "captions_match_narration",
        "cursor_does_not_obscure_evidence",
        "figures_are_legible",
        "no_dead_clicks_or_loading_states",
    }
    failed = sorted(key for key in required if review.get(key) is not True)
    if failed:
        raise ValueError("visual review incomplete: " + ", ".join(failed))
```

Generate the contact sheet with the helper's equivalent command:

```bash
ffmpeg -y -i docs/demo/papertrail-demo.mp4 \
  -vf "fps=1/5,scale=480:-1,tile=5x6" -frames:v 1 .demo-work/contact-sheet.png
```

Require a checked `.demo-work/review.json` containing exactly:

```json
{
  "no_private_text_visible": true,
  "captions_match_narration": true,
  "cursor_does_not_obscure_evidence": true,
  "figures_are_legible": true,
  "no_dead_clicks_or_loading_states": true
}
```

Add `--manifest` and `--review` arguments and complete `main` with:

```python
manifest = DemoManifest.load(args.manifest)
scan_text_tree(args.repo)
validate_video(args.video)
validate_evidence_ids(args.home / "papertrail.db", args.transcript.read_text())
validate_live_routes(manifest)
validate_visual_review(args.video, args.review)
```

- [ ] **Step 5: Test and commit**

Run: `pytest tests/demo/test_validate_demo.py -v && ruff check scripts/demo tests/demo`

Expected: PASS and `All checks passed!`

```bash
git add scripts/demo/validate_demo.py tests/demo/test_validate_demo.py
git commit -m "test: gate PaperTrail demo publication"
```

### Task 8: Produce the final media and integrate it into the README

**Files:**
- Create: `docs/demo/papertrail-demo.mp4`
- Create: `docs/demo/papertrail-demo-preview.webp`
- Create: `docs/demo/papertrail-demo-poster.png`
- Modify: `docs/demo/transcript.md`
- Modify: `README.md`

- [ ] **Step 1: Prepare the sanitized real index**

Create `.demo-work/interests.md` with the public profile:

```markdown
I care about reliable AI agents that adapt when tools, interfaces, and environments change.
Prioritize mechanisms with strong evaluation, explicit failure analysis, and evidence that
distinguishes durable adaptation from benchmark-specific prompting. Keep some room for
surprising adjacent work on memory, verification, and self-improvement.
```

Run:

```bash
python -m scripts.demo.prepare_demo_workspace \
  --source-home "$PAPERTRAIL_SOURCE_HOME" \
  --output-home "$PWD/.demo-work/papertrail-home" \
  --interests-file "$PWD/.demo-work/interests.md"
```

Expected: the manifest reports exactly 40 papers, 2701 evidence passages, 302 figures, 25 groups, and 3 blogs; no source data is modified.

- [ ] **Step 2: Start the disposable dashboard and record all scenes**

Run:

```bash
PAPERTRAIL_HOME="$PWD/.demo-work/papertrail-home" \
  papertrail serve --host 127.0.0.1 --port 8899
```

In another terminal:

```bash
python -m scripts.demo.record_terminal
python -m scripts.demo.record_dashboard
python -m scripts.demo.record_codex --home "$PWD/.demo-work/papertrail-home"
```

Expected: all approved scene files and raw audit logs exist under `.demo-work`; the dashboard recorder reports no console errors; the Codex answer contains resolvable evidence IDs and public source URLs.

- [ ] **Step 3: Record narration and assemble**

Record the checked `docs/demo/narration.md` script in a warm, matter-of-fact voice to `.demo-work/narration.wav`. Normalize it without crushing dynamics:

```bash
python -m scripts.demo.assemble_demo \
  --narration .demo-work/narration.wav \
  --output docs/demo/papertrail-demo.mp4
```

Expected: MP4, animated WebP, and poster are created under `docs/demo/`.

- [ ] **Step 4: Validate the finished deliverables**

Run:

```bash
python -m scripts.demo.validate_demo \
  --home "$PWD/.demo-work/papertrail-home" \
  --video docs/demo/papertrail-demo.mp4
pytest -q
ruff check .
python -m build
```

Expected: all demo gates pass, all project tests pass, Ruff passes, and source/wheel artifacts build.

- [ ] **Step 5: Add the clickable README hero**

Place this directly after the opening problem/value paragraph:

```markdown
<p align="center">
  <a href="docs/demo/papertrail-demo.mp4">
    <img src="docs/demo/papertrail-demo-preview.webp"
         alt="Watch the 2 minute 30 second PaperTrail demo: local onboarding, personalized dashboard, and evidence-grounded Codex research"
         width="960">
  </a>
</p>

<p align="center">
  <a href="docs/demo/papertrail-demo.mp4">Watch the 2:30 demo</a>
  · <a href="docs/demo/transcript.md">Read the transcript</a>
</p>
```

Keep the existing static screenshots lower in the README as feature references rather than the primary explanation.

- [ ] **Step 6: Inspect the exact release diff and commit**

Run:

```bash
git status --short
git diff --check
git diff --stat
pytest tests/test_public_surface.py -q
```

Expected: only intended public assets and README/transcript changes; no raw workspace, credentials, personal paths, or internal-provider strings.

```bash
git add README.md docs/demo/papertrail-demo.mp4 docs/demo/papertrail-demo-preview.webp \
  docs/demo/papertrail-demo-poster.png docs/demo/papertrail-demo.vtt \
  docs/demo/transcript.md docs/demo/narration.md
git commit -m "docs: add the PaperTrail product demo"
```

### Task 9: Final fresh-clone and GitHub rendering verification

**Files:**
- Modify only if verification exposes an issue.

- [ ] **Step 1: Verify install and tests from a clean worktree**

Run:

```bash
git worktree add /tmp/papertrail-demo-verify HEAD
cd /tmp/papertrail-demo-verify
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,pdf,demo]'
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Expected: clean install, all tests pass, and Ruff reports no issues.

- [ ] **Step 2: Verify README-relative media locally**

Run:

```bash
test -s docs/demo/papertrail-demo.mp4
test -s docs/demo/papertrail-demo-preview.webp
test -s docs/demo/papertrail-demo-poster.png
test -s docs/demo/papertrail-demo.vtt
test -s docs/demo/transcript.md
ffprobe -v error docs/demo/papertrail-demo.mp4
```

Expected: every artifact exists and ffprobe exits 0.

- [ ] **Step 3: Push and verify GitHub rendering**

Push the reviewed commits, open `https://github.com/spraphul/papertrail`, and verify that the animated preview loads, the MP4 opens, the transcript link resolves, and the README remains readable with animation disabled.

- [ ] **Step 4: Record final verification**

If no fixes were necessary, do not create an empty commit. Report the commit IDs, final MP4 duration/size, test totals, and the public GitHub links. If verification required a fix, commit only that fix with:

```bash
git add <exact-fixed-files>
git commit -m "fix: repair demo release links"
```
