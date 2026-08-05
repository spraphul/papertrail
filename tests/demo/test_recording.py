from pathlib import Path

from scripts.demo.model import DemoManifest
from scripts.demo.record_dashboard import dashboard_scenes, scene_urls


def test_dashboard_story_visits_every_approved_product_route() -> None:
    manifest = DemoManifest.load(Path("scripts/demo/demo.json"))
    urls = scene_urls(manifest)
    assert urls[0].endswith("/#/")
    assert any("#/groups/" in url for url in urls)
    assert any("#/blog/" in url for url in urls)
    assert any("#/paper/" in url for url in urls)
    assert urls[-1].endswith("#/favorites")


def test_dashboard_scene_holds_total_approved_walkthrough() -> None:
    manifest = DemoManifest.load(Path("scripts/demo/demo.json"))
    scenes = dashboard_scenes(manifest)
    assert sum(scene.hold_ms for scene in scenes) == 44_000
    assert [scene.name for scene in scenes] == [
        "interests",
        "groups",
        "group-detail",
        "deep-dive",
        "paper-reader",
        "favorites",
    ]
