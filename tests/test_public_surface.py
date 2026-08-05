from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
TEXT_SUFFIXES = {".md", ".py", ".toml", ".json", ".js", ".css", ".html"}
PUBLIC_ROOTS = (
    ROOT / "README.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "src",
    ROOT / "tests",
)
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
