from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol


class IntelligenceProvider(Protocol):
    provider_name: str
    embedding_model: str
    reasoning_model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def structured(self, *, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...

    def health(self) -> dict[str, Any]: ...


class CompositeProvider:
    def __init__(self, embedding: IntelligenceProvider, reasoning: IntelligenceProvider):
        self.embedding = embedding
        self.reasoning = reasoning
        self.provider_name = f"{embedding.provider_name}+{reasoning.provider_name}"
        self.embedding_provider_name = embedding.provider_name
        self.reasoning_provider_name = reasoning.provider_name
        self.embedding_model = embedding.embedding_model
        self.reasoning_model = reasoning.reasoning_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.embedding.embed(texts)

    def structured(
        self, *, system: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        return self.reasoning.structured(system=system, prompt=prompt, schema=schema)

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "embedding": self.embedding.health(),
            "reasoning": self.reasoning.health(),
        }


class OllamaProvider:
    """Dependency-free client for a local Ollama server."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        embedding_model: str = "embeddinggemma",
        reasoning_model: str = "qwen2.5:7b",
        timeout: int = 180,
    ):
        self.provider_name = "ollama"
        self.base_url = base_url.rstrip("/")
        self.embedding_model = embedding_model
        self.reasoning_model = reasoning_model
        self.timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "PaperTrailLocal/0.5"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Local model service unavailable at {self.base_url}. Start Ollama and pull "
                f"'{self.embedding_model}' and '{self.reasoning_model}'."
            ) from error

    def health(self) -> dict[str, Any]:
        request = urllib.request.Request(f"{self.base_url}/api/tags")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                value = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError):
            return {"available": False, "url": self.base_url, "models": []}
        models = [item.get("name", "") for item in value.get("models", [])]
        return {"available": True, "url": self.base_url, "models": models}

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        value = self._post(
            "/api/embed",
            {"model": self.embedding_model, "input": texts, "truncate": True},
        )
        embeddings = value.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("Embedding provider returned an invalid batch")
        return embeddings

    def structured(self, *, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        value = self._post(
            "/api/chat",
            {
                "model": self.reasoning_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
            },
        )
        content = value.get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError("Reasoning provider returned no structured content")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("Reasoning provider returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise RuntimeError("Reasoning provider returned a non-object result")
        return parsed


def _provider(settings: Any, name: str) -> IntelligenceProvider:
    if name == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_url,
            embedding_model=settings.embedding_model,
            reasoning_model=settings.reasoning_model,
        )
    raise ValueError(f"Unknown PaperTrail provider {name!r}; use 'ollama'")


def provider_from_settings(settings: Any) -> IntelligenceProvider:
    embedding_name = getattr(settings, "embedding_provider", settings.intelligence_provider)
    reasoning_name = getattr(settings, "reasoning_provider", settings.intelligence_provider)
    embedding = _provider(settings, embedding_name)
    if reasoning_name == embedding_name:
        return embedding
    return CompositeProvider(embedding, _provider(settings, reasoning_name))
