from __future__ import annotations

import copy
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

import certifi


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


class AIFactoryProvider:
    """Oracle AI Factory adapter for local development.

    AI Factory currently accepts one string per embedding request, so ``embed``
    deliberately serializes a caller batch into scalar requests. Authentication is
    environment-only and the transport bypasses workstation proxy settings.
    """

    DEFAULT_BASE_URL = (
        "http://aifactory-healthai.digitalassistant.oci.oraclecloud.com:3000"
    )
    DEFAULT_API_VERSION = "2024-10-21"

    def __init__(
        self,
        *,
        bearer_token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        embedding_model: str = "oracle-text-embedding-3-small",
        reasoning_model: str = "gpt-5.4-2026-03-05",
        timeout: int = 180,
    ):
        self.provider_name = "aifactory"
        self._bearer_token = bearer_token or os.environ.get("AIFACTORY_BEARER_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.embedding_model = embedding_model
        self.reasoning_model = reasoning_model
        self.timeout = timeout
        context = ssl.create_default_context(cafile=certifi.where())
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
        )

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._bearer_token:
            raise RuntimeError(
                "AI Factory development provider requires AIFACTORY_BEARER_TOKEN"
            )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._bearer_token}",
                "Content-Type": "application/json",
                "User-Agent": "PaperTrailLocal/0.11",
            },
            method="POST",
        )
        value: Any = None
        for attempt in range(3):
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    value = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                if error.code == 429 or error.code >= 500:
                    if attempt < 2:
                        time.sleep(2**attempt)
                        continue
                try:
                    raw_detail = error.read().decode("utf-8", errors="replace")
                    detail = json.loads(raw_detail).get("error", {}).get("message")
                except (json.JSONDecodeError, AttributeError):
                    detail = None
                message = f"AI Factory returned HTTP {error.code}"
                if detail:
                    message += f": {self._redact(str(detail))}"
                raise RuntimeError(message) from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"AI Factory unavailable at {self.base_url}") from error
            except json.JSONDecodeError as error:
                raise RuntimeError("AI Factory returned invalid JSON") from error
        if not isinstance(value, dict):
            raise RuntimeError("AI Factory returned a non-object response")
        return value

    def _redact(self, value: str) -> str:
        if self._bearer_token:
            return value.replace(self._bearer_token, "[REDACTED]")
        return value

    def health(self) -> dict[str, Any]:
        return {
            "available": bool(self._bearer_token),
            "provider": self.provider_name,
            "url": self.base_url,
            "embedding_model": self.embedding_model,
            "reasoning_model": self.reasoning_model,
            "message": None if self._bearer_token else "AIFACTORY_BEARER_TOKEN is not configured",
        }

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        model = urllib.parse.quote(self.embedding_model, safe="")
        version = urllib.parse.quote(self.api_version, safe="")
        path = f"/openai/deployments/{model}/embeddings?api-version={version}"
        for text in texts:
            value = self._request(path, {"input": text})
            items = value.get("data") or value.get("embeddings")
            if not isinstance(items, list) or len(items) != 1:
                raise RuntimeError("AI Factory embedding response did not contain one vector")
            item = items[0]
            vector = item.get("embedding") if isinstance(item, dict) else item
            if not isinstance(vector, list) or not all(
                isinstance(number, (int, float)) for number in vector
            ):
                raise RuntimeError("AI Factory embedding response contained an invalid vector")
            vectors.append(vector)
        return vectors

    def structured(self, *, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        model = urllib.parse.quote(self.reasoning_model, safe="")
        output_budget = 16384 if "groups" in schema.get("properties", {}) else 8192
        # The documented GPT-5.x deployment route omits api-version.
        path = f"/openai/deployments/{model}/chat/completions"
        value = self._request(
            path,
            {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "papertrail_result",
                        "strict": True,
                        "schema": _strict_schema(schema),
                    },
                },
                "temperature": 1,
                "stream": False,
                "max_completion_tokens": output_budget,
            },
        )
        try:
            content = value["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("AI Factory reasoning response contained no message") from error
        if not isinstance(content, str):
            raise RuntimeError("AI Factory reasoning response contained no structured content")
        try:
            parsed = _parse_json_object(content)
        except json.JSONDecodeError as error:
            finish_reason = value.get("choices", [{}])[0].get("finish_reason")
            suffix = f" (finish_reason={finish_reason})" if finish_reason else ""
            raise RuntimeError(
                f"AI Factory reasoning response contained invalid JSON{suffix}"
            ) from error
        if not isinstance(parsed, dict):
            raise RuntimeError("AI Factory reasoning response contained a non-object result")
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
    if name == "aifactory":
        return AIFactoryProvider(
            bearer_token=settings.aifactory_bearer_token,
            base_url=settings.aifactory_base_url,
            api_version=settings.aifactory_api_version,
            embedding_model=settings.embedding_model,
            reasoning_model=settings.reasoning_model,
        )
    raise ValueError(
        f"Unknown PaperTrail provider {name!r}; use 'ollama', 'openai', or 'aifactory'"
    )


def provider_from_settings(settings: Any) -> IntelligenceProvider:
    embedding_name = getattr(settings, "embedding_provider", settings.intelligence_provider)
    reasoning_name = getattr(settings, "reasoning_provider", settings.intelligence_provider)
    embedding = _provider(settings, embedding_name)
    if reasoning_name == embedding_name:
        return embedding
    return CompositeProvider(embedding, _provider(settings, reasoning_name))
