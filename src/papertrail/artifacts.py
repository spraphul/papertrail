from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactRef:
    content_hash: str
    uri: str
    path: Path


class LocalArtifactStore:
    def __init__(self, root: Path):
        self.root = root

    def put_bytes(self, content: bytes, *, namespace: str, suffix: str = "") -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / namespace / digest[:2] / f"{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Artifact hash mismatch at {path}")
        return ArtifactRef(digest, f"file://{path}", path)
