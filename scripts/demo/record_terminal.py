from __future__ import annotations

import argparse
import html
import json
import subprocess
from pathlib import Path


def _run(command: list[str], root: Path, log: Path) -> str:
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=True)
    output = result.stdout + result.stderr
    with log.open("a") as stream:
        stream.write("$ " + " ".join(command) + "\n" + output + "\n")
    return output


def _terminal_html(lines: list[str]) -> str:
    content = "\n".join(html.escape(line) for line in lines)
    return f"""<!doctype html><meta charset="utf-8"><style>
    body{{margin:0;background:#20231f;color:#e8eee4;font:27px/1.7 ui-monospace,monospace}}
    main{{box-sizing:border-box;width:1920px;height:1080px;padding:105px 125px;overflow:hidden}}
    pre{{white-space:pre-wrap}} .prompt{{color:#8fba8b}} .ok{{color:#d99a7c}}
    </style><main><pre>{content}</pre></main>"""


def _editorial_html(eyebrow: str, headline: str, detail: str) -> str:
    return f"""<!doctype html><meta charset="utf-8"><style>
    body{{margin:0;background:#f5f0e6;color:#332f2a}}
    main{{box-sizing:border-box;width:1920px;height:1080px;padding:150px;display:flex;
      flex-direction:column;justify-content:center}}
    small{{font:22px Arial;color:#b76549;letter-spacing:.12em;text-transform:uppercase}}
    h1{{font:78px/1.04 Georgia;margin:28px 0;max-width:1500px}}
    p{{font:30px/1.5 Arial;color:#655c52;max-width:1400px}}
    </style><main><small>{html.escape(eyebrow)}</small><h1>{html.escape(headline)}</h1>
    <p>{html.escape(detail)}</p></main>"""


def record_html_scene(page_path: Path, output: Path, name: str, duration_ms: int) -> None:
    from playwright.sync_api import sync_playwright

    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(output),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto(page_path.resolve().as_uri())
        page.wait_for_timeout(duration_ms)
        video = page.video
        context.close()
        if video is None:
            raise RuntimeError(f"{name} video was not created")
        video.save_as(output / f"{name}.webm")
        browser.close()


def record(root: Path, output: Path) -> None:
    work = root / ".demo-work"
    work.mkdir(exist_ok=True)
    interests = work / "onboarding-interests.md"
    interests.write_text("Reliable agents that adapt to changing tools and environments.\n")
    environment = work / "onboarding-venv"
    home = work / "onboarding-home"
    raw_log = work / "onboarding.log"
    commands = [
        ["python3", "-m", "venv", "--system-site-packages", str(environment)],
        [
            str(environment / "bin" / "pip"),
            "install",
            "--no-build-isolation",
            "-e",
            ".[pdf]",
        ],
        [
            str(environment / "bin" / "papertrail"),
            "--home",
            str(home),
            "setup",
            "--interests-file",
            str(interests),
            "--client",
            "codex",
            "--no-schedule",
            "--no-dashboard",
        ],
    ]
    outputs = [_run(command, root, raw_log) for command in commands]
    setup = json.loads(outputs[-1])
    if setup.get("status") != "ready":
        raise RuntimeError(f"setup did not become ready: {setup}")
    pip_success = next(
        (line for line in reversed(outputs[1].splitlines()) if "Successfully installed" in line),
        "",
    )
    if not pip_success:
        raise RuntimeError("wheel installation did not report success")
    lines = [
        "$ pip install papertrail-local",
        pip_success,
        "",
        "$ papertrail setup --interests-file interests.md --client codex",
        "✓ local index · ✓ MCP · ✓ daily workflow",
    ]
    page_path = work / "onboarding.html"
    page_path.write_text(_terminal_html(lines))
    cards = {
        "pain": _editorial_html(
            "The daily surplus",
            "205 papers today. Which three matter?",
            "PaperTrail builds reusable evidence and connections—not 205 disposable summaries.",
        ),
        "daily-run": _editorial_html(
            "Daily intelligence",
            "205 → 40 → 2,701 → 302 → 25 → 3",
            "Discovered → enriched → evidence → figures → neighborhoods → deep dives",
        ),
        "handoff": _editorial_html(
            "Open source · local first",
            "Turn the paper surplus into a research trail.",
            "Install PaperTrail. Bring Ollama or OpenAI. Connect Codex or Claude.",
        ),
    }
    for name, markup in cards.items():
        card = work / f"{name}.html"
        card.write_text(markup)
        duration = {"pain": 18_000, "daily-run": 16_000, "handoff": 15_000}[name]
        record_html_scene(card, output, name, duration)
    record_html_scene(page_path, output, "terminal", 24_000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path(".demo-work/scenes"))
    args = parser.parse_args()
    record(args.root.resolve(), args.output)


if __name__ == "__main__":
    main()
