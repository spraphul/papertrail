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
    def load(cls, path: Path) -> DemoManifest:
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


def run_checked(
    command: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)
