from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from scripts.demo.model import DemoManifest

if TYPE_CHECKING:
    from playwright.sync_api import Page


@dataclass(frozen=True)
class Scene:
    name: str
    url: str
    ready: str
    focus: str
    hold_ms: int


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


def _ready(page: Page, selector: str) -> None:
    page.wait_for_selector(selector, state="visible", timeout=15_000)
    page.wait_for_timeout(900)


def record(manifest: DemoManifest, output: Path) -> None:
    from playwright.sync_api import sync_playwright

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
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("scripts/demo/demo.json"))
    parser.add_argument("--output", type=Path, default=Path(".demo-work/scenes"))
    args = parser.parse_args()
    record(DemoManifest.load(args.manifest), args.output)


if __name__ == "__main__":
    main()
