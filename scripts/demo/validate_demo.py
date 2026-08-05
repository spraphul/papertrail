from __future__ import annotations

import argparse
import json
import re
import sqlite3
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import certifi

from scripts.demo.model import DemoManifest, run_checked


EVIDENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
SOURCE_URL = re.compile(r"https://arxiv\.org/abs/[A-Za-z0-9.]+v\d+")
PRIVATE = (
    re.compile("/" + r"Users/[^/\s]+/"),
    re.compile(r"\b" + "sk" + r"-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b" + "ey" + "J" + r"[A-Za-z0-9._-]{12,}\b"),
    re.compile("ai" + "factory", re.IGNORECASE),
    re.compile("oracle" + r"cloud\.com", re.IGNORECASE),
)


def scan_text_tree(root: Path) -> None:
    offenders: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", ".demo-work", ".superpowers"} for part in path.parts):
            continue
        if "docs/superpowers" in path.as_posix():
            continue
        if path.suffix.casefold() not in {
            ".md", ".py", ".toml", ".json", ".vtt", ".js", ".css", ".html"
        }:
            continue
        text = path.read_text(errors="ignore")
        if any(pattern.search(text) for pattern in PRIVATE):
            offenders.append(str(path.relative_to(root)))
    if offenders:
        raise ValueError("private material: " + ", ".join(offenders))


def validate_evidence_ids(database: Path, text: str) -> None:
    visible = EVIDENCE.findall(text)
    if not visible:
        raise ValueError("demo transcript contains no evidence IDs")
    marks = ",".join("?" for _ in visible)
    with sqlite3.connect(database) as db:
        found = {
            row[0]
            for row in db.execute(
                f"SELECT id FROM evidence_passages WHERE id IN ({marks})", visible
            )
        }
    missing = sorted(set(visible) - found)
    if missing:
        raise ValueError("missing evidence IDs: " + ", ".join(missing))


def probe(video: Path) -> dict:
    result = run_checked(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_type,width,height,r_frame_rate",
            "-of", "json", str(video),
        ]
    )
    return json.loads(result.stdout)


def validate_video(video: Path) -> None:
    value = probe(video)
    duration = float(value["format"]["duration"])
    if not 140 <= duration <= 160:
        raise ValueError(f"video duration {duration:.2f}s is outside 140–160s")
    if int(value["format"]["size"]) > 25 * 1024 * 1024:
        raise ValueError("video exceeds 25 MiB")
    stream = next(item for item in value["streams"] if item["codec_type"] == "video")
    actual = (stream["width"], stream["height"], stream["r_frame_rate"])
    if actual != (1920, 1080, "30/1"):
        raise ValueError(f"unexpected video stream: {stream}")


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            if response.status >= 400:
                raise ValueError(f"{url} returned HTTP {response.status}")
            return response.read()
    except urllib.error.HTTPError as error:
        raise ValueError(f"{url} returned HTTP {error.code}") from error


def validate_live_routes(manifest: DemoManifest, transcript: str) -> None:
    from playwright.sync_api import sync_playwright

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
        browser.close()
    for url in sorted(set(SOURCE_URL.findall(transcript))):
        try:
            _request(url, method="HEAD")
        except ValueError:
            _request(url, headers={"Range": "bytes=0-0"})
    artifact = f"{root}/v1/papers/{manifest.routes['paper']}/artifact"
    if not _request(artifact, headers={"Range": "bytes=0-1023"}).startswith(b"%PDF"):
        raise ValueError("selected local paper artifact is not a PDF")


def validate_visual_review(video: Path, review_path: Path) -> None:
    contact = review_path.with_name("contact-sheet.png")
    run_checked(
        [
            "ffmpeg", "-y", "-i", str(video), "-vf",
            "fps=1/5,scale=480:-1,tile=5x6", "-frames:v", "1", "-update", "1",
            str(contact),
        ]
    )
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--video", type=Path, default=Path("docs/demo/papertrail-demo.mp4"))
    parser.add_argument("--transcript", type=Path, default=Path("docs/demo/transcript.md"))
    parser.add_argument("--manifest", type=Path, default=Path("scripts/demo/demo.json"))
    parser.add_argument("--review", type=Path, default=Path(".demo-work/review.json"))
    args = parser.parse_args()
    transcript = args.transcript.read_text()
    scan_text_tree(args.repo)
    validate_video(args.video)
    validate_evidence_ids(args.home / "papertrail.db", transcript)
    validate_live_routes(DemoManifest.load(args.manifest), transcript)
    validate_visual_review(args.video, args.review)


if __name__ == "__main__":
    main()
