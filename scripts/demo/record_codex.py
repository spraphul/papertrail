from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.demo.model import DemoManifest
from scripts.demo.record_terminal import _terminal_html, record_html_scene


EVIDENCE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")
SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9._-]{12,})\b")
INTERNAL_PAPER = re.compile(r",?\s*`paper_[A-Za-z0-9_-]+`")


def extract_evidence_ids(text: str) -> set[str]:
    return set(EVIDENCE.findall(text))


def redact_transcript(text: str, replacements: dict[str, str]) -> str:
    for private, public in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(private, public)
    return INTERNAL_PAPER.sub("", SECRET.sub("[REDACTED]", text))


def validate_audit(events: str) -> None:
    items = [json.loads(line) for line in events.splitlines() if line.strip()]

    def contains_corpus_count(value: object) -> bool:
        if isinstance(value, dict):
            if value.get("full_text_papers") == 40:
                return True
            return any(contains_corpus_count(child) for child in value.values())
        if isinstance(value, list):
            return any(contains_corpus_count(child) for child in value)
        if isinstance(value, str) and "full_text_papers" in value:
            try:
                return contains_corpus_count(json.loads(value))
            except json.JSONDecodeError:
                return '"full_text_papers":40' in value.replace(" ", "")
        return False

    papertrail_calls = [
        item
        for item in items
        if item.get("item", {}).get("type") == "mcp_tool_call"
        and item.get("item", {}).get("server") == "papertrail"
    ]
    if len(papertrail_calls) < 5:
        raise RuntimeError("Codex audit contains fewer than five PaperTrail MCP calls")
    commands = [
        str(item.get("item", {}).get("command", "")).casefold()
        for item in items
        if item.get("item", {}).get("type") == "command_execution"
    ]
    if any("sqlite" in command or "papertrail.db" in command for command in commands):
        raise RuntimeError("Codex bypassed MCP with direct database inspection")
    if not any(contains_corpus_count(item) for item in items):
        raise RuntimeError("Codex audit does not show the sanitized 40-paper corpus")


def capture(
    manifest: DemoManifest,
    home: Path,
    work: Path,
    output: Path,
    scene_output: Path,
) -> None:
    output = output.resolve()
    scene_output = scene_output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    repository = Path(__file__).parents[2]
    onboarding_bin = repository / ".demo-work" / "onboarding-venv" / "bin"
    environment = {
        **os.environ,
        "PAPERTRAIL_HOME": str(home),
        "PATH": str(onboarding_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    subprocess.run(
        [
            "papertrail",
            "--home",
            str(home),
            "connect",
            "codex",
            "--scope",
            "project",
            "--force",
        ],
        cwd=work,
        env=environment,
        check=True,
    )
    result = subprocess.run(
        [
            "codex",
            "exec",
            "-c",
            f"mcp_servers.papertrail.command={json.dumps(str(onboarding_bin / 'papertrail'))}",
            "-c",
            "mcp_servers.papertrail.args=" + json.dumps(["--home", str(home), "mcp"]),
            "--json",
            "--output-last-message",
            str(output),
            manifest.codex_prompt,
        ],
        cwd=work,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    replacements = {str(home): "<papertrail-home>", str(work): "<demo-project>"}
    clean_events = redact_transcript(result.stdout, replacements)
    output.with_suffix(".events.jsonl").write_text(clean_events)
    validate_audit(clean_events)
    if not output.is_file():
        raise RuntimeError("Codex completed without writing its final answer")
    clean = redact_transcript(output.read_text(), replacements)
    if not extract_evidence_ids(clean):
        raise RuntimeError("Codex answer contains no PaperTrail evidence IDs")
    output.write_text(clean)
    render_answer(manifest, clean, output.with_suffix(".html"), scene_output)


def render_answer(
    manifest: DemoManifest,
    answer: str,
    page: Path,
    scene_output: Path,
) -> None:
    markup = _terminal_html(["$ codex", manifest.codex_prompt, "", answer]).replace(
        "</style>",
        "pre{animation:research-scroll 29s linear 2s forwards}"
        "@keyframes research-scroll{to{transform:translateY(-58%)}}</style>",
    )
    page.write_text(markup)
    record_html_scene(page, scene_output, "codex", 33_000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("scripts/demo/demo.json"))
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=Path(".demo-work/codex-project"))
    parser.add_argument("--output", type=Path, default=Path(".demo-work/codex-answer.md"))
    parser.add_argument("--scene-output", type=Path, default=Path(".demo-work/scenes"))
    parser.add_argument("--render-existing", action="store_true")
    args = parser.parse_args()
    manifest = DemoManifest.load(args.manifest)
    if args.render_existing:
        clean = redact_transcript(args.output.read_text(), {})
        args.output.write_text(clean)
        render_answer(manifest, clean, args.output.with_suffix(".html"), args.scene_output)
        return
    capture(
        manifest,
        args.home.resolve(),
        args.work.resolve(),
        args.output,
        args.scene_output,
    )


if __name__ == "__main__":
    main()
