from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    home: Path
    intelligence_provider: str = "ollama"
    embedding_provider: str = "ollama"
    reasoning_provider: str = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "embeddinggemma"
    reasoning_model: str = "qwen2.5:7b"

    @property
    def database_path(self) -> Path:
        return self.home / "papertrail.db"

    @property
    def artifacts_path(self) -> Path:
        return self.home / "artifacts"

    def ensure(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.artifacts_path.mkdir(parents=True, exist_ok=True)


def settings(home: str | Path | None = None) -> Settings:
    resolved = Path(home or os.environ.get("PAPERTRAIL_HOME", ".papertrail")).expanduser()
    provider = os.environ.get("PAPERTRAIL_PROVIDER", "ollama").casefold()
    embedding_provider = os.environ.get("PAPERTRAIL_EMBEDDING_PROVIDER", provider).casefold()
    reasoning_provider = os.environ.get("PAPERTRAIL_REASONING_PROVIDER", provider).casefold()
    return Settings(
        resolved.resolve(),
        intelligence_provider=provider,
        embedding_provider=embedding_provider,
        reasoning_provider=reasoning_provider,
        ollama_url=os.environ.get("PAPERTRAIL_OLLAMA_URL", "http://127.0.0.1:11434"),
        embedding_model=os.environ.get("PAPERTRAIL_EMBEDDING_MODEL", "embeddinggemma"),
        reasoning_model=os.environ.get("PAPERTRAIL_REASONING_MODEL", "qwen2.5:7b"),
    )
