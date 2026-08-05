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
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    aifactory_base_url: str = (
        "http://aifactory-healthai.digitalassistant.oci.oraclecloud.com:3000"
    )
    aifactory_api_version: str = "2024-10-21"
    aifactory_bearer_token: str | None = None
    embedding_model: str = "embeddinggemma"
    reasoning_model: str = "qwen2.5:7b"
    semantic_scholar_api_key: str | None = None

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
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_api_key=_openai_api_key(),
        aifactory_base_url=os.environ.get(
            "AIFACTORY_BASE_URL",
            "http://aifactory-healthai.digitalassistant.oci.oraclecloud.com:3000",
        ),
        aifactory_api_version=os.environ.get("AIFACTORY_API_VERSION", "2024-10-21"),
        aifactory_bearer_token=os.environ.get("AIFACTORY_BEARER_TOKEN"),
        embedding_model=os.environ.get("PAPERTRAIL_EMBEDDING_MODEL", "embeddinggemma"),
        reasoning_model=os.environ.get("PAPERTRAIL_REASONING_MODEL", "qwen2.5:7b"),
        semantic_scholar_api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
    )


def _openai_api_key() -> str | None:
    value = os.environ.get("OPENAI_API_KEY")
    if value:
        return value
    key_file = os.environ.get("PAPERTRAIL_OPENAI_API_KEY_FILE")
    if not key_file:
        return None
    path = Path(key_file).expanduser()
    try:
        value = path.read_text().strip()
    except OSError as error:
        raise RuntimeError(f"Could not read OpenAI API key file: {path}") from error
    if not value:
        raise RuntimeError(f"OpenAI API key file is empty: {path}")
    return value
