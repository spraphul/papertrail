from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .config import Settings


SKILL_NAME = "papertrail-deep-research"


def connect_agent(
    client: str,
    scope: str,
    config: Settings,
    *,
    cwd: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if client not in {"codex", "claude"}:
        raise ValueError("client must be 'codex' or 'claude'")
    if scope not in {"user", "project"}:
        raise ValueError("scope must be 'user' or 'project'")
    root = (cwd or Path.cwd()).resolve()
    skill_source = Path(__file__).with_name("skill") / SKILL_NAME
    if not skill_source.is_dir():
        raise RuntimeError("Bundled PaperTrail skill is missing")
    skill_target = _skill_target(client, scope, root)
    skill_copied = True
    if skill_target.exists():
        if force:
            shutil.rmtree(skill_target)
        else:
            skill_copied = False
    if skill_copied:
        skill_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_source, skill_target)

    executable = shutil.which("papertrail") or "papertrail"
    command = [executable, "--home", str(config.home), "mcp"]
    if client == "codex":
        config_path = (
            Path.home() / ".codex" / "config.toml"
            if scope == "user"
            else root / ".codex" / "config.toml"
        )
        configured = _configure_codex(config_path, command)
    else:
        config_path = (
            Path.home() / ".claude.json" if scope == "user" else root / ".mcp.json"
        )
        configured = _configure_claude(config_path, command)
    return {
        "status": "connected",
        "client": client,
        "scope": scope,
        "skill": str(skill_target),
        "skill_status": "installed" if skill_copied else "already_present",
        "mcp_config": str(config_path),
        "mcp_config_status": "added" if configured else "already_present",
        "papertrail_home": str(config.home),
        "restart_required": True,
    }


def _skill_target(client: str, scope: str, root: Path) -> Path:
    if client == "codex":
        base = Path.home() / ".codex" / "skills" if scope == "user" else root / ".codex" / "skills"
    else:
        base = Path.home() / ".claude" / "skills" if scope == "user" else root / ".claude" / "skills"
    return base / SKILL_NAME


def _configure_codex(path: Path, command: list[str]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if "[mcp_servers.papertrail]" in existing:
        return False
    block = (
        "[mcp_servers.papertrail]\n"
        f"command = {json.dumps(command[0])}\n"
        f"args = {json.dumps(command[1:])}\n"
    )
    separator = "" if not existing or existing.endswith("\n\n") else "\n"
    path.write_text(existing + separator + block)
    return True


def _configure_claude(path: Path, command: list[str]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    value: dict[str, Any] = {}
    if path.exists():
        parsed = json.loads(path.read_text())
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected a JSON object in {path}")
        value = parsed
    servers = value.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"Expected mcpServers to be an object in {path}")
    if "papertrail" in servers:
        return False
    servers["papertrail"] = {
        "type": "stdio",
        "command": command[0],
        "args": command[1:],
    }
    path.write_text(json.dumps(value, indent=2) + "\n")
    return True
