from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParsedSection:
    heading: str
    text: str
    page_start: int | None = None
    page_end: int | None = None


def extract_pdf(path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]

        reader = PdfReader(path)
        return [
            (number, _valid_unicode(page.extract_text() or ""))
            for number, page in enumerate(reader.pages, 1)
        ]
    except ImportError:
        pass

    ghostscript = shutil.which("gs")
    if not ghostscript:
        raise RuntimeError(
            "PDF extraction needs either `pip install papertrail-local[pdf]` or Ghostscript."
        )
    with tempfile.TemporaryDirectory(prefix="papertrail-text-") as directory:
        output_pattern = str(Path(directory) / "page-%06d.txt")
        result = subprocess.run(
            [
                ghostscript,
                "-q",
                "-dNOPAUSE",
                "-dBATCH",
                "-sDEVICE=txtwrite",
                f"-sOutputFile={output_pattern}",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(f"PDF extraction failed: {result.stderr.strip()}")
        outputs = sorted(Path(directory).glob("page-*.txt"))
        if not outputs:
            raise RuntimeError("PDF extraction produced no pages")
        return [
            (number, _valid_unicode(output.read_text(errors="replace")))
            for number, output in enumerate(outputs, 1)
        ]


def _valid_unicode(value: str) -> str:
    return value.encode("utf-8", errors="replace").decode("utf-8")


_FIGURE_CAPTION = re.compile(r"^(?:figure|fig\.)\s*([a-z]?\d+[a-z]?)\s*[:.]?\s*(.*)$", re.I)


def figure_captions(pages: list[tuple[int, str]]) -> list[dict[str, str | int]]:
    """Find conservative figure-caption candidates in page text."""
    found: list[dict[str, str | int]] = []
    seen: set[tuple[int, str]] = set()
    for page, content in pages:
        lines = [" ".join(line.split()) for line in content.splitlines()]
        for index, line in enumerate(lines):
            match = _FIGURE_CAPTION.match(line)
            if not match:
                continue
            label = f"Figure {match.group(1)}"
            tail = [match.group(2)] if match.group(2) else []
            for following in lines[index + 1 : index + 3]:
                if not following or _FIGURE_CAPTION.match(following):
                    break
                tail.append(following)
            caption = " ".join(tail).strip()
            key = (page, label.casefold())
            if key in seen or len(caption) < 10:
                continue
            seen.add(key)
            found.append({"page": page, "label": label, "caption": caption})
    return found


def render_pdf_page(path: Path, page: int, *, dpi: int = 144) -> bytes:
    ghostscript = shutil.which("gs")
    if not ghostscript:
        raise RuntimeError("Figure acquisition requires Ghostscript (`gs`).")
    with tempfile.NamedTemporaryFile(suffix=".png") as output:
        result = subprocess.run(
            [
                ghostscript,
                "-q",
                "-dSAFER",
                "-dNOPAUSE",
                "-dBATCH",
                "-sDEVICE=png16m",
                f"-r{dpi}",
                f"-dFirstPage={page}",
                f"-dLastPage={page}",
                f"-sOutputFile={output.name}",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(f"PDF page rendering failed: {result.stderr.strip()}")
        return Path(output.name).read_bytes()


_HEADING = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]?\s+)?(?:abstract|introduction|background|related work|"
    r"method(?:ology)?|approach|experiments?|results?|discussion|limitations?|"
    r"conclusion|future work|references|appendix)(?:\s.*)?$",
    re.IGNORECASE,
)


def split_sections(pages: list[tuple[int, str]]) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    heading = "Document"
    lines: list[str] = []
    start_page: int | None = pages[0][0] if pages else None
    current_page = start_page

    def flush() -> None:
        text = "\n".join(lines).strip()
        if text:
            sections.append(ParsedSection(heading, text, start_page, current_page))

    for page, content in pages:
        current_page = page
        for raw_line in content.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if len(line) <= 100 and _HEADING.fullmatch(line):
                flush()
                heading = line
                lines = []
                start_page = page
            else:
                lines.append(line)
    flush()
    return sections or [ParsedSection("Document", "", start_page, current_page)]


def passages(text: str, target_chars: int = 1400) -> list[str]:
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", text) if part.strip()]
    output: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= target_chars:
            output.append(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", paragraph)
        chunk: list[str] = []
        size = 0
        for sentence in sentences:
            if chunk and size + len(sentence) > target_chars:
                output.append(" ".join(chunk))
                chunk = chunk[-1:]
                size = sum(map(len, chunk))
            chunk.append(sentence)
            size += len(sentence)
        if chunk:
            output.append(" ".join(chunk))
    return [item for item in output if len(item) >= 40]
