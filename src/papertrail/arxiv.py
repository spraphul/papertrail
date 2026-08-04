from __future__ import annotations

import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .service import PaperTrail


ATOM = {"atom": "http://www.w3.org/2005/Atom"}


def normalize_arxiv_id(value: str) -> str:
    value = value.strip().removeprefix("https://arxiv.org/abs/").removeprefix("http://arxiv.org/abs/")
    return value.removeprefix("arXiv:")


def import_paper(service: PaperTrail, arxiv_id: str) -> dict[str, Any]:
    identifier = normalize_arxiv_id(arxiv_id)
    request = urllib.request.Request(
        f"https://export.arxiv.org/api/query?id_list={identifier}",
        headers={"User-Agent": "PaperTrailLocal/0.10 (personal research index)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())
    entry = root.find("atom:entry", ATOM)
    if entry is None:
        raise ValueError(f"arXiv did not return paper {identifier}")
    title = " ".join((entry.findtext("atom:title", "", ATOM)).split())
    abstract = " ".join((entry.findtext("atom:summary", "", ATOM)).split())
    published = entry.findtext("atom:published", "", ATOM)[:10] or None
    authors = [
        " ".join(author.findtext("atom:name", "", ATOM).split())
        for author in entry.findall("atom:author", ATOM)
    ]
    pdf_url = f"https://arxiv.org/pdf/{identifier}"
    pdf_request = urllib.request.Request(
        pdf_url, headers={"User-Agent": "PaperTrailLocal/0.10 (personal research index)"}
    )
    with urllib.request.urlopen(pdf_request, timeout=90) as response:
        content = response.read()
    if not content.startswith(b"%PDF"):
        raise RuntimeError("arXiv returned a non-PDF artifact")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as temporary:
        temporary.write(content)
        temporary.flush()
        result = service.ingest_pdf(
            Path(temporary.name),
            title=title,
            authors=authors,
            abstract=abstract,
            published_date=published,
            source_url=f"https://arxiv.org/abs/{identifier}",
            source_class="preprint",
        )
    result["arxiv_id"] = identifier
    return result
