from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any


PROFILE_FILE = "profile.json"


def load_profile(home: Path) -> dict[str, Any]:
    return _load_json(home / PROFILE_FILE)


def save_profile(home: Path, value: dict[str, Any]) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / PROFILE_FILE
    _write_private_json(path, value)
    return path


def configure_runtime(home: Path) -> dict[str, Any]:
    """Load a saved provider profile into the current process."""
    profile = load_profile(home)
    providers = profile.get("providers", {})
    mapping = {
        "embedding_provider": "PAPERTRAIL_EMBEDDING_PROVIDER",
        "reasoning_provider": "PAPERTRAIL_REASONING_PROVIDER",
        "embedding_model": "PAPERTRAIL_EMBEDDING_MODEL",
        "reasoning_model": "PAPERTRAIL_REASONING_MODEL",
        "openai_base_url": "OPENAI_BASE_URL",
        "openai_api_key_file": "PAPERTRAIL_OPENAI_API_KEY_FILE",
        "aifactory_base_url": "AIFACTORY_BASE_URL",
        "aifactory_api_version": "AIFACTORY_API_VERSION",
    }
    for key, environment_name in mapping.items():
        value = providers.get(key)
        if value is not None:
            os.environ.setdefault(environment_name, str(value))

    return {"profile": profile.get("profile")}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(path)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
