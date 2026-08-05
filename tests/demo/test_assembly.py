import json
from pathlib import Path

from scripts.demo.assemble_demo import build_transcript, validate_timeline
from scripts.demo.record_narration import parse_narration


def test_timeline_is_contiguous_and_matches_approved_duration() -> None:
    scenes = json.loads(Path("scripts/demo/scenes.json").read_text())
    validate_timeline(scenes, expected_duration=150)
    assert scenes[0]["start"] == 0
    assert scenes[-1]["end"] == 150
    assert all(left["end"] == right["start"] for left, right in zip(scenes, scenes[1:]))


def test_transcript_combines_commands_narration_and_real_answer(tmp_path: Path) -> None:
    narration = tmp_path / "narration.md"
    narration.write_text("# PaperTrail demo narration\n\nSpoken words.\n")
    answer = tmp_path / "answer.md"
    answer.write_text("Evidence `ev_demo` and https://arxiv.org/abs/2608.00001v1")
    output = tmp_path / "transcript.md"
    build_transcript(narration, answer, output)
    text = output.read_text()
    assert "pip install papertrail-local" in text
    assert "Spoken words." in text
    assert "ev_demo" in text


def test_narration_sections_cover_the_timeline() -> None:
    sections = parse_narration(Path("docs/demo/narration.md"))
    assert [(section.start, section.end) for section in sections] == [
        (0, 18),
        (18, 42),
        (42, 58),
        (58, 102),
        (102, 135),
        (135, 150),
    ]
