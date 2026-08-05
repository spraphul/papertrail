from __future__ import annotations

import copy
import json
import urllib.error
import urllib.parse
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
            headers={"Content-Type": "application/json", "User-Agent": "PaperTrailLocal/0.11"},
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


class OpenAIProvider:
    """Dependency-free client for the OpenAI Responses and Embeddings APIs."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = "https://api.openai.com/v1",
        embedding_model: str = "text-embedding-3-small",
        reasoning_model: str = "gpt-5.6",
        timeout: int = 180,
    ):
        self.provider_name = "openai"
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.embedding_model = embedding_model
        self.reasoning_model = reasoning_model
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "PaperTrailLocal/0.11",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self, path: str, payload: dict[str, Any] | None = None, *, timeout: int | None = None
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            headers=self._headers(),
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read()).get("error", {}).get("message")
            except (json.JSONDecodeError, AttributeError):
                detail = None
            message = f"OpenAI-compatible API returned HTTP {error.code}"
            if detail:
                message += f": {detail}"
            raise RuntimeError(message) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(f"OpenAI-compatible API unavailable at {self.base_url}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError("OpenAI-compatible API returned invalid JSON") from error
        if not isinstance(value, dict):
            raise RuntimeError("OpenAI-compatible API returned a non-object response")
        return value

    def health(self) -> dict[str, Any]:
        if self.base_url == "https://api.openai.com/v1" and not self.api_key:
            return {
                "available": False,
                "provider": self.provider_name,
                "url": self.base_url,
                "message": "OPENAI_API_KEY is not configured",
            }
        try:
            self._request("/models", timeout=5)
        except RuntimeError as error:
            return {
                "available": False,
                "provider": self.provider_name,
                "url": self.base_url,
                "message": str(error),
            }
        return {
            "available": True,
            "provider": self.provider_name,
            "url": self.base_url,
            "embedding_model": self.embedding_model,
            "reasoning_model": self.reasoning_model,
        }

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        value = self._request(
            "/embeddings",
            {
                "model": self.embedding_model,
                "input": texts,
                "encoding_format": "float",
            },
        )
        items = value.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise RuntimeError("Embedding provider returned an invalid batch")
        try:
            ordered = sorted(items, key=lambda item: item["index"])
            if [item["index"] for item in ordered] != list(range(len(texts))):
                raise ValueError
            embeddings = [item["embedding"] for item in ordered]
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Embedding provider returned invalid indexed vectors") from error
        if not all(
            isinstance(vector, list) and all(isinstance(number, (int, float)) for number in vector)
            for vector in embeddings
        ):
            raise RuntimeError("Embedding provider returned invalid vectors")
        return embeddings

    def structured(self, *, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        value = self._request(
            "/responses",
            {
                "model": self.reasoning_model,
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "papertrail_result",
                        "schema": _strict_schema(schema),
                        "strict": True,
                    }
                },
                "store": False,
            },
        )
        if value.get("status") not in {None, "completed"}:
            raise RuntimeError(f"Reasoning provider returned status {value.get('status')!r}")
        content = _response_output_text(value)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("Reasoning provider returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise RuntimeError("Reasoning provider returned a non-object result")
        return parsed


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return an OpenAI strict-schema copy without mutating shared schemas."""
    normalized = copy.deepcopy(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                value["additionalProperties"] = False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(normalized)
    return normalized


def _parse_json_object(content: str) -> dict[str, Any]:
    """Decode a JSON object, tolerating Markdown fences from compatible gateways."""
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].strip().casefold() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise RuntimeError("Reasoning provider returned a non-object result")
    return value


def _response_output_text(value: dict[str, Any]) -> str:
    direct = value.get("output_text")
    if isinstance(direct, str):
        return direct
    for item in value.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise RuntimeError(f"Reasoning provider refused the request: {part.get('refusal', '')}")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                return part["text"]
    raise RuntimeError("Reasoning provider returned no structured content")


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


def provider_from_settings(settings: Any) -> IntelligenceProvider:
    embedding_name = getattr(settings, "embedding_provider", settings.intelligence_provider)
    reasoning_name = getattr(settings, "reasoning_provider", settings.intelligence_provider)
    embedding = _provider(settings, embedding_name)
    if reasoning_name == embedding_name:
        return embedding
    return CompositeProvider(embedding, _provider(settings, reasoning_name))
