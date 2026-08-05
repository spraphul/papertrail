from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from scripts.demo.model import run_checked


HEADING = re.compile(r"^## (\d+):(\d+)–(\d+):(\d+) · .+$")


@dataclass(frozen=True)
class NarrationSection:
    start: int
    end: int
    text: str


def parse_narration(path: Path) -> list[NarrationSection]:
    sections: list[NarrationSection] = []
    start = end = None
    lines: list[str] = []
    for line in path.read_text().splitlines():
        match = HEADING.match(line)
        if match:
            if start is not None and end is not None:
                sections.append(NarrationSection(start, end, " ".join(lines).strip()))
            start = int(match[1]) * 60 + int(match[2])
            end = int(match[3]) * 60 + int(match[4])
            lines = []
        elif start is not None and line.strip():
            lines.append(line.strip())
    if start is not None and end is not None:
        sections.append(NarrationSection(start, end, " ".join(lines).strip()))
    if not sections or sections[0].start != 0 or sections[-1].end != 150:
        raise ValueError("narration must cover 0:00 through 2:30")
    if any(left.end != right.start for left, right in zip(sections, sections[1:])):
        raise ValueError("narration sections must be contiguous")
    return sections


def _duration(path: Path) -> float:
    result = run_checked(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def record(script: Path, output: Path, work: Path, voice: str, rate: int) -> None:
    work.mkdir(parents=True, exist_ok=True)
    normalized: list[Path] = []
    for index, section in enumerate(parse_narration(script)):
        raw = work / f"narration-{index:02d}.aiff"
        target = work / f"narration-{index:02d}.wav"
        run_checked(["say", "-v", voice, "-r", str(rate), "-o", str(raw), section.text])
        available = section.end - section.start - 0.5
        ratio = max(1.0, _duration(raw) / available)
        run_checked(
            [
                "ffmpeg", "-y", "-i", str(raw), "-af",
                f"atempo={ratio:.6f},apad,atrim=duration={section.end - section.start}",
                "-ar", "48000", "-ac", "1", str(target),
            ]
        )
        normalized.append(target)
    concat = work / "narration-concat.txt"
    concat.write_text("".join(f"file '{path.resolve()}'\n" for path in normalized))
    run_checked(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-c:a", "pcm_s16le", str(output),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, default=Path("docs/demo/narration.md"))
    parser.add_argument("--output", type=Path, default=Path(".demo-work/narration.wav"))
    parser.add_argument("--work", type=Path, default=Path(".demo-work/narration"))
    parser.add_argument("--voice", default="Samantha")
    parser.add_argument("--rate", type=int, default=165)
    args = parser.parse_args()
    record(args.script, args.output, args.work, args.voice, args.rate)


if __name__ == "__main__":
    main()
