from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def install_daily_schedule(
    home: Path,
    *,
    hour: int,
    minute: int,
    launch_agents_dir: Path | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Daily schedule must be a valid hour and minute")
    if sys.platform != "darwin" and launch_agents_dir is None:
        raise RuntimeError("Native daily scheduling currently supports macOS launchd")
    executable = shutil.which("papertrail") or "papertrail"
    suffix = hashlib.sha256(str(home).encode()).hexdigest()[:10]
    label = f"local.papertrail.daily.{suffix}"
    directory = launch_agents_dir or Path.home() / "Library" / "LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}.plist"
    payload = {
        "Label": label,
        "ProgramArguments": [executable, "--home", str(home), "daily"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "StandardOutPath": str(home / "daily.log"),
        "StandardErrorPath": str(home / "daily-error.log"),
    }
    with path.open("wb") as stream:
        plistlib.dump(payload, stream, sort_keys=True)
    if activate:
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        loaded = subprocess.run(
            ["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True
        )
        if loaded.returncode != 0:
            raise RuntimeError(f"launchd could not install the daily job: {loaded.stderr.strip()}")
    return {
        "status": "installed",
        "scheduler": "launchd",
        "label": label,
        "path": str(path),
        "daily_at": f"{hour:02d}:{minute:02d}",
    }


def install_dashboard_service(
    home: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    launch_agents_dir: Path | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    if not 1 <= port <= 65535:
        raise ValueError("Dashboard port must be between 1 and 65535")
    if sys.platform != "darwin" and launch_agents_dir is None:
        raise RuntimeError("Native dashboard hosting currently supports macOS launchd")
    executable = shutil.which("papertrail") or "papertrail"
    suffix = hashlib.sha256(str(home).encode()).hexdigest()[:10]
    label = f"local.papertrail.dashboard.{suffix}"
    directory = launch_agents_dir or Path.home() / "Library" / "LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}.plist"
    payload = {
        "Label": label,
        "ProgramArguments": [
            executable,
            "--home",
            str(home),
            "serve",
            "--host",
            host,
            "--port",
            str(port),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(home / "dashboard.log"),
        "StandardErrorPath": str(home / "dashboard-error.log"),
    }
    with path.open("wb") as stream:
        plistlib.dump(payload, stream, sort_keys=True)
    if activate:
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        loaded = subprocess.run(
            ["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True
        )
        if loaded.returncode != 0:
            raise RuntimeError(f"launchd could not host the dashboard: {loaded.stderr.strip()}")
    return {
        "status": "installed",
        "scheduler": "launchd",
        "label": label,
        "path": str(path),
        "url": f"http://{host}:{port}",
    }
