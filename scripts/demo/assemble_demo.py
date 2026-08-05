from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.demo.model import run_checked


def validate_timeline(scenes: list[dict], expected_duration: int) -> None:
    if not scenes or scenes[0]["start"] != 0 or scenes[-1]["end"] != expected_duration:
        raise ValueError("timeline does not span the approved duration")
    for left, right in zip(scenes, scenes[1:]):
        if left["end"] != right["start"]:
            raise ValueError(f"timeline gap between {left['id']} and {right['id']}")
    if any(scene["end"] <= scene["start"] for scene in scenes):
        raise ValueError("scene durations must be positive")


def build_transcript(narration: Path, answer: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# PaperTrail demo transcript\n\n"
        "## Commands shown\n\n"
        "```console\n"
        "pip install papertrail-local\n"
        "papertrail setup --interests-file interests.md --client codex\n"
        "```\n\n"
        + narration.read_text().replace("# PaperTrail demo narration\n\n", "")
        + "\n\n## Genuine Codex research answer\n\n"
        + answer.read_text()
    )


def assemble(scenes_path: Path, raw: Path, narration: Path, captions: Path, output: Path) -> None:
    scenes = json.loads(scenes_path.read_text())
    validate_timeline(scenes, 150)
    normalized = raw / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    normalized_files: list[Path] = []
    for index, scene in enumerate(scenes):
        duration = scene["end"] - scene["start"]
        source = raw / scene["source"]
        if not source.is_file():
            raise FileNotFoundError(source)
        target = normalized / f"{index:02d}-{scene['id']}.mp4"
        run_checked(
            [
                "ffmpeg", "-y", "-i", str(source), "-an", "-vf",
                "scale=1920:1080,fps=30,tpad=stop_mode=clone:stop_duration=60,"
                f"trim=duration={duration},setpts=PTS-STARTPTS,format=yuv420p",
                "-c:v", "libx264", "-crf", "18", str(target),
            ]
        )
        normalized_files.append(target)
    concat = raw / "concat.txt"
    concat.write_text("".join(f"file '{path.resolve()}'\n" for path in normalized_files))
    output.parent.mkdir(parents=True, exist_ok=True)
    transitions = (
        "between(t,0,1)+between(t,17,19)+between(t,41,43)+between(t,57,59)+"
        "between(t,101,103)+between(t,134,136)+between(t,149,150)"
    )
    run_checked(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-i", str(narration), "-f", "lavfi", "-i",
            "anoisesrc=color=pink:duration=150:amplitude=0.02", "-filter_complex",
            f"[0:v]scale=1920:1080,fps=30,subtitles={captions}:"
            "force_style='FontName=Arial,FontSize=10,MarginV=26,Outline=0.7',"
            "format=yuv420p[v];[1:a]highpass=f=70,loudnorm=I=-16:LRA=7:TP=-1.5[voice];"
            f"[2:a]highpass=f=120,lowpass=f=900,volume='{transitions}*0.035'[bed];"
            "[voice][bed]amix=inputs=2:duration=first:normalize=0[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow",
            "-crf", "25", "-c:a", "aac", "-b:a", "128k", "-shortest",
            "-movflags", "+faststart", str(output),
        ]
    )
    run_checked(
        [
            "ffmpeg", "-y", "-ss", "58", "-t", "12", "-i", str(output),
            "-vf", "fps=8,scale=960:-1:flags=lanczos", "-loop", "0",
            str(output.with_name("papertrail-demo-preview.webp")),
        ]
    )
    run_checked(
        [
            "ffmpeg", "-y", "-ss", "68", "-i", str(output), "-frames:v", "1",
            str(output.with_name("papertrail-demo-poster.png")),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenes", type=Path, default=Path("scripts/demo/scenes.json"))
    parser.add_argument("--raw", type=Path, default=Path(".demo-work/scenes"))
    parser.add_argument("--narration", type=Path)
    parser.add_argument(
        "--narration-script", type=Path, default=Path("docs/demo/narration.md")
    )
    parser.add_argument("--answer", type=Path, default=Path(".demo-work/codex-answer.md"))
    parser.add_argument("--transcript", type=Path, default=Path("docs/demo/transcript.md"))
    parser.add_argument("--transcript-only", action="store_true")
    parser.add_argument("--captions", type=Path, default=Path("docs/demo/papertrail-demo.vtt"))
    parser.add_argument("--output", type=Path, default=Path("docs/demo/papertrail-demo.mp4"))
    args = parser.parse_args()
    build_transcript(args.narration_script, args.answer, args.transcript)
    if args.transcript_only:
        return
    if args.narration is None:
        parser.error("--narration is required unless --transcript-only is used")
    assemble(args.scenes, args.raw, args.narration, args.captions, args.output)


if __name__ == "__main__":
    main()
