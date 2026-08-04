from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from .db import connect, transaction
from .organization import latest_organization
from .service import PaperTrail, stable_id, utc_now


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "synthesis": {"type": "string"},
        "trends": {"type": "array", "items": {"type": "string"}},
        "blogs": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "title": {"type": "string"},
                    "dek": {"type": "string"},
                    "surprise": {"type": "string"},
                    "markdown": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "figure_ids": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {"type": "string"},
                    },
                    "themes": {"type": "array", "items": {"type": "string"}},
                    "related_paper_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "paper_id",
                    "title",
                    "dek",
                    "surprise",
                    "markdown",
                    "evidence_ids",
                    "figure_ids",
                    "themes",
                    "related_paper_ids",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "synthesis", "trends", "blogs"],
    "additionalProperties": False,
}

PAPERTRAIL_MCP_TOOLS = (
    "search_catalog",
    "search_papers",
    "search_figures",
    "hybrid_search",
    "check_idea_novelty",
    "discover_opportunities",
    "get_paper",
    "find_related_papers",
    "get_scientific_records",
    "get_figure",
    "get_evidence",
    "get_snapshot_info",
    "get_corpus_status",
    "get_research_groups",
)


def generate_daily_digest(
    service: PaperTrail,
    *,
    snapshot_id: str,
    paper_ids: list[str],
    agent_client: str,
    agent_model: str | None = None,
    agent_executable: str | None = None,
    max_blogs: int = 3,
    organization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if agent_client not in {"codex", "claude"}:
        raise ValueError("Daily analyst must be codex or claude")
    if not 1 <= max_blogs <= 3:
        raise ValueError("Daily blog count must be between 1 and 3")
    service.initialize()
    run_date = date.today().isoformat()
    run_id = stable_id("digest", run_date, snapshot_id, agent_client)
    with closing(connect(service.settings.database_path)) as db:
        existing = db.execute(
            "SELECT status, candidate_paper_ids_json FROM daily_digest_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if existing and existing["status"] == "complete" and not paper_ids:
            blogs = [
                dict(row)
                for row in db.execute(
                    "SELECT id AS blog_id, slug, paper_id FROM daily_blogs WHERE digest_run_id = ?",
                    (run_id,),
                )
            ]
            return {"status": "already_complete", "run_id": run_id, "blogs": blogs}
        if existing and existing["status"] == "failed" and not paper_ids:
            paper_ids = json.loads(existing["candidate_paper_ids_json"])
    candidates = _candidates(service, paper_ids, limit=40)
    if not candidates:
        _upsert_run(
            service,
            run_id,
            run_date=run_date,
            snapshot_id=snapshot_id,
            agent_client=agent_client,
            agent_model=agent_model,
            status="no_new_papers",
            candidate_ids=[],
        )
        return {"status": "no_new_papers", "run_id": run_id, "blogs": []}
    _upsert_run(
        service,
        run_id,
        run_date=run_date,
        snapshot_id=snapshot_id,
        agent_client=agent_client,
        agent_model=agent_model,
        status="running",
        candidate_ids=[item["paper_id"] for item in candidates],
    )
    try:
        prompt = _prompt(
            snapshot_id,
            candidates,
            min(max_blogs, len(candidates)),
            organization=organization,
        )
        output = _invoke_agent(
            service.settings.home, agent_client, prompt, agent_model, agent_executable
        )
        saved = _validate_and_store(
            service,
            run_id,
            output,
            candidate_ids={item["paper_id"] for item in candidates},
            max_blogs=max_blogs,
        )
        return {"status": "complete", "run_id": run_id, **saved}
    except Exception as error:
        with transaction(service.settings.database_path) as db:
            db.execute(
                "UPDATE daily_digest_runs SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                (str(error)[:2000], utc_now(), run_id),
            )
        raise


def dashboard_data(service: PaperTrail) -> dict[str, Any]:
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        totals = dict(
            db.execute(
                """
                SELECT
                    (SELECT count(*) FROM papers) AS papers,
                    (SELECT count(*) FROM evidence_passages) AS evidence,
                    (SELECT count(*) FROM visual_evidence) AS figures,
                    (SELECT count(*) FROM scientific_records) AS scientific_records,
                    (SELECT count(*) FROM daily_blogs) AS blogs
                """
            ).fetchone()
        )
        recent = [
            _blog_row(row)
            for row in db.execute(
                """
                SELECT b.*, p.canonical_title AS paper_title, p.published_date,
                       p.authors_json, r.run_date, r.headline AS digest_headline
                FROM daily_blogs b
                JOIN papers p ON p.id = b.paper_id
                JOIN daily_digest_runs r ON r.id = b.digest_run_id
                ORDER BY b.created_at DESC LIMIT 30
                """
            )
        ]
        digests = [
            {
                **dict(row),
                "trends": json.loads(row["trends_json"]),
                "candidate_paper_ids": json.loads(row["candidate_paper_ids_json"]),
            }
            for row in db.execute(
                "SELECT * FROM daily_digest_runs ORDER BY run_date DESC, created_at DESC LIMIT 30"
            )
        ]
        papers_by_day = [
            dict(row)
            for row in db.execute(
                """
                SELECT substr(published_date, 1, 10) AS day, count(*) AS count
                FROM papers WHERE published_date IS NOT NULL
                GROUP BY day ORDER BY day DESC LIMIT 30
                """
            )
        ][::-1]
    themes: dict[str, int] = {}
    for blog in recent:
        for theme in blog["themes"]:
            themes[theme] = themes.get(theme, 0) + 1
    return {
        "totals": totals,
        "blogs": recent,
        "digests": digests,
        "papers_by_day": papers_by_day,
        "themes": [
            {"theme": key, "count": value}
            for key, value in sorted(themes.items(), key=lambda item: (-item[1], item[0]))[:12]
        ],
        "organization": latest_organization(service),
    }


def get_blog(service: PaperTrail, slug: str) -> dict[str, Any]:
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        row = db.execute(
            """
            SELECT b.*, p.canonical_title AS paper_title, p.published_date,
                   p.authors_json, r.run_date, r.headline AS digest_headline
            FROM daily_blogs b JOIN papers p ON p.id = b.paper_id
            JOIN daily_digest_runs r ON r.id = b.digest_run_id WHERE b.slug = ?
            """,
            (slug,),
        ).fetchone()
    if not row:
        raise KeyError(f"Unknown daily blog: {slug}")
    return _blog_row(row)


def figure_path(service: PaperTrail, figure_id: str) -> tuple[Path, str]:
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        row = db.execute(
            "SELECT artifact_uri FROM visual_evidence WHERE id = ?", (figure_id,)
        ).fetchone()
    if not row:
        raise KeyError(f"Unknown figure: {figure_id}")
    path = Path(str(row["artifact_uri"]).removeprefix("file://")).resolve()
    artifacts = service.settings.artifacts_path.resolve()
    if artifacts not in path.parents or not path.is_file():
        raise KeyError(f"Figure artifact is unavailable: {figure_id}")
    content_type = "image/png" if path.suffix.casefold() == ".png" else "application/octet-stream"
    return path, content_type


def paper_artifact_path(service: PaperTrail, paper_id: str) -> tuple[Path, str]:
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        row = db.execute(
            """
            SELECT v.artifact_uri FROM paper_versions v
            WHERE v.paper_id = ? AND v.is_current = 1
            """,
            (paper_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Unknown paper: {paper_id}")
    path = Path(str(row["artifact_uri"]).removeprefix("file://")).resolve()
    artifacts = service.settings.artifacts_path.resolve()
    if artifacts not in path.parents or not path.is_file():
        raise KeyError(f"Paper artifact is unavailable: {paper_id}")
    content_type = "application/pdf" if path.suffix.casefold() == ".pdf" else "text/plain; charset=utf-8"
    return path, content_type


def _candidates(service: PaperTrail, paper_ids: list[str], limit: int) -> list[dict[str, Any]]:
    unique = list(dict.fromkeys(paper_ids))
    if not unique:
        return []
    marks = ",".join("?" for _ in unique)
    with closing(connect(service.settings.database_path)) as db:
        rows = db.execute(
            f"""
            SELECT p.id AS paper_id, p.canonical_title AS title, p.abstract, p.source_url,
                   p.published_date, p.authors_json,
                   count(DISTINCT sr.id) AS scientific_record_count,
                   count(DISTINCT v.id) AS figure_count
            FROM papers p
            LEFT JOIN scientific_records sr ON sr.paper_id = p.id
            LEFT JOIN visual_evidence v ON v.paper_id = p.id
            WHERE p.id IN ({marks})
            GROUP BY p.id
            ORDER BY scientific_record_count DESC, figure_count DESC, p.published_date DESC
            LIMIT ?
            """,
            (*unique, limit),
        ).fetchall()
    return [
        {**dict(row), "authors": json.loads(row["authors_json"])} for row in rows
    ]


def _prompt(
    snapshot_id: str,
    candidates: list[dict[str, Any]],
    blog_count: int,
    *,
    organization: dict[str, Any] | None = None,
) -> str:
    compact = [
        {
            "paper_id": item["paper_id"],
            "title": item["title"],
            "abstract": item["abstract"],
            "published_date": item["published_date"],
            "source_url": item["source_url"],
            "scientific_record_count": item["scientific_record_count"],
            "figure_count": item["figure_count"],
        }
        for item in candidates
    ]
    cluster_context = [
        {
            "cluster_id": group["cluster_id"],
            "label": group["label"],
            "paper_count": group["paper_count"],
            "new_paper_count": group["new_paper_count"],
            "top_terms": group["top_terms"],
            "new_titles": [
                paper["title"] for paper in group["papers"] if paper["is_new"]
            ][:8],
        }
        for group in (organization or {}).get("groups", [])[:20]
    ]
    return f"""
Use $papertrail-deep-research and only the read-only PaperTrail MCP tools.
Analyze today's newly indexed candidate papers against snapshot {snapshot_id}. Identify patterns
across the batch, then select exactly {blog_count} papers whose result, method, limitation, or
connection to prior work is genuinely surprising. Popularity alone is not a reason.

For each selection, inspect the exact paper, scientific records, supporting evidence passages,
related prior work, and available figures. Write a standalone 900-1500 word Markdown deep dive.
Distinguish source claims from your synthesis. Cite exact evidence IDs inline as [ev_...]. Do not
claim global novelty. Include only figure IDs returned by PaperTrail and discuss what each chosen
figure actually shows. Do not write files or use shell tools.

Return only the requested JSON. Candidate papers:
{json.dumps(compact, separators=(',', ':'))}

The deterministic hybrid organization pass produced these problem neighborhoods.
Use them as navigation aids, verify their interpretation against evidence, and mention
cross-cluster convergence or fragmentation when it is meaningful:
{json.dumps(cluster_context, separators=(',', ':'))}
""".strip()


def _invoke_agent(
    home: Path,
    client: str,
    prompt: str,
    model: str | None,
    configured_executable: str | None = None,
) -> dict[str, Any]:
    runtime = home / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    schema_path = runtime / "daily-blog-schema.json"
    output_path = runtime / "daily-blog-output.json"
    schema_path.write_text(json.dumps(OUTPUT_SCHEMA))
    if output_path.exists():
        output_path.unlink()
    if client == "codex":
        executable = _resolve_executable(configured_executable, "codex")
        if not executable:
            raise RuntimeError("Codex CLI is not installed or not on PATH")
        command = [
            executable,
            "exec",
            "--ignore-user-config",
            "-c",
            'approval_policy="never"',
            "-c",
            f"mcp_servers.papertrail.command={json.dumps(sys.executable)}",
            "-c",
            "mcp_servers.papertrail.args="
            + json.dumps(
                ["-m", "papertrail.cli", "--home", str(home), "mcp"],
                separators=(",", ":"),
            ),
            "-c",
            "mcp_servers.papertrail.cwd="
            + json.dumps(str(Path(__file__).resolve().parents[2])),
            "-c",
            "mcp_servers.papertrail.env={PYTHONPATH="
            + json.dumps(str(Path(__file__).resolve().parents[2] / "src"))
            + "}",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--ephemeral",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=home,
            timeout=1800,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Codex daily analysis failed: {completed.stderr[-1200:].strip()}")
        return json.loads(output_path.read_text())
    executable = _resolve_executable(configured_executable, "claude")
    if not executable:
        raise RuntimeError("Claude CLI is not installed or not on PATH")
    allowed = ",".join(f"mcp__papertrail__{name}" for name in PAPERTRAIL_MCP_TOOLS)
    command = [
        executable,
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        "30",
        "--allowedTools",
        allowed,
        "--disallowedTools",
        "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch,Read,Glob,Grep",
    ]
    if model:
        command.extend(["--model", model])
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        cwd=home,
        timeout=1800,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Claude daily analysis failed: {completed.stderr[-1200:].strip()}")
    outer = json.loads(completed.stdout)
    content = outer.get("result", outer) if isinstance(outer, dict) else outer
    if isinstance(content, str):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        return json.loads(content)
    if not isinstance(content, dict):
        raise RuntimeError("Claude daily analysis returned no JSON object")
    return content


def _resolve_executable(configured: str | None, fallback: str) -> str | None:
    if configured and "/" in configured:
        return configured if Path(configured).is_file() else None
    return shutil.which(configured or fallback)


def _validate_and_store(
    service: PaperTrail,
    run_id: str,
    output: dict[str, Any],
    *,
    candidate_ids: set[str],
    max_blogs: int,
) -> dict[str, Any]:
    blogs = output.get("blogs")
    if not isinstance(blogs, list) or not 1 <= len(blogs) <= max_blogs:
        raise ValueError(f"Daily agent must return between 1 and {max_blogs} blogs")
    seen: set[str] = set()
    saved: list[dict[str, Any]] = []
    with transaction(service.settings.database_path) as db:
        db.execute("DELETE FROM daily_blogs WHERE digest_run_id = ?", (run_id,))
        for item in blogs:
            paper_id = str(item.get("paper_id", ""))
            if paper_id not in candidate_ids or paper_id in seen:
                raise ValueError("Daily blog selected an unknown or duplicate candidate paper")
            seen.add(paper_id)
            evidence_ids = list(dict.fromkeys(map(str, item.get("evidence_ids", []))))
            figure_ids = list(dict.fromkeys(map(str, item.get("figure_ids", []))))
            if not evidence_ids:
                raise ValueError(f"Daily blog for {paper_id} has no evidence IDs")
            evidence_marks = ",".join("?" for _ in evidence_ids)
            evidence_count = db.execute(
                f"SELECT count(*) FROM evidence_passages WHERE paper_id = ? AND id IN ({evidence_marks})",
                (paper_id, *evidence_ids),
            ).fetchone()[0]
            if evidence_count != len(evidence_ids):
                raise ValueError(f"Daily blog for {paper_id} cites foreign or unknown evidence")
            if figure_ids:
                figure_marks = ",".join("?" for _ in figure_ids)
                figure_count = db.execute(
                    f"SELECT count(*) FROM visual_evidence WHERE paper_id = ? AND id IN ({figure_marks})",
                    (paper_id, *figure_ids),
                ).fetchone()[0]
                if figure_count != len(figure_ids):
                    raise ValueError(f"Daily blog for {paper_id} cites foreign or unknown figures")
            available_figures = db.execute(
                "SELECT count(*) FROM visual_evidence WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            if available_figures and not figure_ids:
                raise ValueError(f"Daily blog for {paper_id} omitted its available figures")
            markdown = str(item.get("markdown", "")).strip()
            if len(markdown.split()) < 700:
                raise ValueError(f"Daily blog for {paper_id} is too short to be a deep dive")
            if any(evidence_id not in markdown for evidence_id in evidence_ids):
                raise ValueError(f"Daily blog for {paper_id} does not cite every evidence ID inline")
            if any(figure_id not in markdown for figure_id in figure_ids):
                raise ValueError(f"Daily blog for {paper_id} does not discuss every figure inline")
            related_ids = list(dict.fromkeys(map(str, item.get("related_paper_ids", []))))
            if related_ids:
                related_marks = ",".join("?" for _ in related_ids)
                related_count = db.execute(
                    f"SELECT count(*) FROM papers WHERE id IN ({related_marks})", related_ids
                ).fetchone()[0]
                if related_count != len(related_ids):
                    raise ValueError(f"Daily blog for {paper_id} names unknown related papers")
            title = str(item.get("title", "")).strip()
            if not all(
                (
                    title,
                    str(item.get("dek", "")).strip(),
                    str(item.get("surprise", "")).strip(),
                    item.get("themes"),
                )
            ):
                raise ValueError(f"Daily blog for {paper_id} is missing editorial metadata")
            paper = db.execute(
                "SELECT source_url FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if not paper or not paper["source_url"]:
                raise ValueError(f"Daily blog for {paper_id} has no canonical source URL")
            slug_base = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:60]
            slug = f"{date.today().isoformat()}-{slug_base or paper_id}"
            blog_id = stable_id("blog", run_id, paper_id)
            values = (
                blog_id,
                run_id,
                paper_id,
                paper["source_url"],
                slug,
                title,
                str(item.get("dek", "")).strip(),
                str(item.get("surprise", "")).strip(),
                markdown,
                json.dumps(evidence_ids),
                json.dumps(figure_ids),
                json.dumps(item.get("themes", [])),
                json.dumps(related_ids),
                utc_now(),
            )
            db.execute(
                "INSERT INTO daily_blogs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            saved.append({"blog_id": blog_id, "slug": slug, "paper_id": paper_id})
        db.execute(
            """
            UPDATE daily_digest_runs SET status = 'complete', headline = ?, synthesis = ?,
                trends_json = ?, error_message = NULL, completed_at = ? WHERE id = ?
            """,
            (
                str(output.get("headline", "")).strip(),
                str(output.get("synthesis", "")).strip(),
                json.dumps(output.get("trends", [])),
                utc_now(),
                run_id,
            ),
        )
    return {
        "headline": output.get("headline", ""),
        "synthesis": output.get("synthesis", ""),
        "trends": output.get("trends", []),
        "blogs": saved,
    }


def _upsert_run(
    service: PaperTrail,
    run_id: str,
    *,
    run_date: str,
    snapshot_id: str,
    agent_client: str,
    agent_model: str | None,
    status: str,
    candidate_ids: list[str],
) -> None:
    with transaction(service.settings.database_path) as db:
        db.execute(
            """
            INSERT INTO daily_digest_runs (
                id, run_date, snapshot_id, agent_client, agent_model, status,
                candidate_paper_ids_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status = excluded.status,
                candidate_paper_ids_json = excluded.candidate_paper_ids_json,
                error_message = NULL, completed_at = NULL
            """,
            (
                run_id,
                run_date,
                snapshot_id,
                agent_client,
                agent_model,
                status,
                json.dumps(candidate_ids),
                utc_now(),
            ),
        )


def _blog_row(row: Any) -> dict[str, Any]:
    value = dict(row)
    for source, target in (
        ("authors_json", "authors"),
        ("evidence_ids_json", "evidence_ids"),
        ("figure_ids_json", "figure_ids"),
        ("themes_json", "themes"),
        ("related_paper_ids_json", "related_paper_ids"),
    ):
        value[target] = json.loads(value.pop(source))
    return value
