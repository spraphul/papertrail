from __future__ import annotations

import json
import math
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
from .preferences import aggregate_profile
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
                    "selection_mode": {
                        "type": "string",
                        "enum": ["preference", "exploration", "editorial"],
                    },
                    "selection_reason": {"type": "string"},
                    "matched_favorite_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "matched_preference_labels": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
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
                    "selection_mode",
                    "selection_reason",
                    "matched_favorite_ids",
                    "matched_preference_labels",
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

PREFERENCE_STOPWORDS = {
    "about", "after", "also", "among", "based", "been", "being", "between", "from",
    "have", "into", "more", "most", "other", "paper", "papers", "propose", "result",
    "results", "show", "shows", "such", "than", "that", "their", "these", "they",
    "this", "through", "using", "which", "while", "with", "without", "work", "the",
    "and", "for", "are", "was", "were", "our", "we",
}


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
    personalize: bool = True,
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
    personalization = _personalization_profile(
        service, organization=organization, enabled=personalize
    )
    candidates = _candidates(service, paper_ids, limit=40, personalization=personalization)
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
        requested_blog_count = min(max_blogs, len(candidates))
        prompt = _prompt(
            snapshot_id,
            candidates,
            requested_blog_count,
            organization=organization,
            personalization=personalization,
        )
        output = _invoke_agent(
            service.settings.home, agent_client, prompt, agent_model, agent_executable
        )
        saved = _validate_and_store(
            service,
            run_id,
            output,
            candidate_ids={item["paper_id"] for item in candidates},
            required_blog_count=requested_blog_count,
            personalization=personalization,
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
                    (SELECT count(*) FROM daily_blogs) AS blogs,
                    (SELECT count(*) FROM paper_favorites) AS favorites
                """
            ).fetchone()
        )
        recent = [
            _blog_row(row)
            for row in db.execute(
                """
                SELECT b.*, p.canonical_title AS paper_title, p.published_date,
                       p.authors_json, r.run_date, r.headline AS digest_headline,
                       x.selection_mode, x.selection_reason,
                       x.matched_favorite_ids_json,
                       x.matched_preference_labels_json
                FROM daily_blogs b
                JOIN papers p ON p.id = b.paper_id
                JOIN daily_digest_runs r ON r.id = b.digest_run_id
                LEFT JOIN daily_blog_personalization x ON x.blog_id = b.id
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
                   p.authors_json, r.run_date, r.headline AS digest_headline,
                   x.selection_mode, x.selection_reason,
                   x.matched_favorite_ids_json,
                   x.matched_preference_labels_json
            FROM daily_blogs b JOIN papers p ON p.id = b.paper_id
            JOIN daily_digest_runs r ON r.id = b.digest_run_id
            LEFT JOIN daily_blog_personalization x ON x.blog_id = b.id
            WHERE b.slug = ?
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


def _clip(value: str, limit: int) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _preference_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", value.casefold())
        if token not in PREFERENCE_STOPWORDS and not token.isdigit()
    }


def _normalize_vector(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude else vector


def _paper_vectors(db: Any, paper_ids: list[str], model: str) -> dict[str, list[float]]:
    if not paper_ids:
        return {}
    marks = ",".join("?" for _ in paper_ids)
    sums: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for row in db.execute(
        f"""
        SELECT v.paper_id, e.vector_json FROM embeddings e
        JOIN paper_versions v ON v.id = e.paper_version_id
        WHERE e.model = ? AND v.paper_id IN ({marks})
        """,
        (model, *paper_ids),
    ):
        vector = [float(value) for value in json.loads(row["vector_json"])]
        if row["paper_id"] not in sums:
            sums[row["paper_id"]] = [0.0] * len(vector)
            counts[row["paper_id"]] = 0
        if len(sums[row["paper_id"]]) != len(vector):
            continue
        sums[row["paper_id"]] = [
            current + value for current, value in zip(sums[row["paper_id"]], vector)
        ]
        counts[row["paper_id"]] += 1
    return {
        paper_id: _normalize_vector([value / counts[paper_id] for value in total])
        for paper_id, total in sums.items()
        if counts[paper_id]
    }


def _centroid(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    dimensions = len(vectors[0])
    compatible = [vector for vector in vectors if len(vector) == dimensions]
    if not compatible:
        return None
    return _normalize_vector(
        [sum(vector[index] for vector in compatible) / len(compatible) for index in range(dimensions)]
    )


def _personalization_profile(
    service: PaperTrail,
    *,
    organization: dict[str, Any] | None,
    enabled: bool,
) -> dict[str, Any]:
    research_profile = aggregate_profile(service, persist=False) if enabled else {}
    empty = {
        "enabled": enabled,
        "active": False,
        "favorite_ids": [],
        "papers": [],
        "top_terms": [],
        "preference_labels": [],
        "negative_labels": [],
        "themes": [],
        "research_groups": [],
    }
    if not enabled:
        return empty
    with closing(connect(service.settings.database_path)) as db:
        favorites = db.execute(
            """
            SELECT p.id AS paper_id, p.canonical_title AS title, p.abstract,
                   f.created_at AS favorited_at
            FROM paper_favorites f JOIN papers p ON p.id = f.paper_id
            ORDER BY f.created_at DESC, p.canonical_title LIMIT 24
            """
        ).fetchall()
        all_favorite_ids = [
            row["paper_id"]
            for row in db.execute(
                "SELECT paper_id FROM paper_favorites ORDER BY created_at DESC"
            )
        ]
        favorite_ids = [row["paper_id"] for row in favorites]
        records: dict[str, dict[str, list[str]]] = {}
        marks = ",".join("?" for _ in favorite_ids)
        theme_counts: dict[str, int] = {}
        if favorite_ids:
            for row in db.execute(
                f"""
                SELECT paper_id, record_type, statement FROM scientific_records
                WHERE paper_id IN ({marks})
                ORDER BY confidence DESC, created_at DESC
                """,
                favorite_ids,
            ):
                bucket = records.setdefault(row["paper_id"], {}).setdefault(row["record_type"], [])
                if len(bucket) < 2:
                    bucket.append(_clip(row["statement"], 320))
            for row in db.execute(
                f"SELECT themes_json FROM daily_blogs WHERE paper_id IN ({marks})",
                favorite_ids,
            ):
                for theme in json.loads(row["themes_json"]):
                    theme_counts[str(theme)] = theme_counts.get(str(theme), 0) + 1
        embedding_model = (
            f"{service.settings.embedding_provider}:{service.settings.embedding_model}"
        )
        favorite_vectors = _paper_vectors(db, favorite_ids, embedding_model)
        preference_vector = _centroid(list(favorite_vectors.values()))
    paper_context = [
        {
            "paper_id": row["paper_id"],
            "title": row["title"],
            "abstract": _clip(row["abstract"], 1000),
            "scientific_records": records.get(row["paper_id"], {}),
        }
        for row in favorites
    ]
    term_counts: dict[str, int] = {}
    for paper in paper_context:
        text = " ".join(
            [
                paper["title"],
                paper["abstract"],
                *[
                    statement
                    for values in paper["scientific_records"].values()
                    for statement in values
                ],
            ]
        )
        for token in _preference_tokens(text):
            term_counts[token] = term_counts.get(token, 0) + 1
    preference_labels = list(research_profile.get("positive_labels", []))
    for label in preference_labels:
        for token in _preference_tokens(label):
            term_counts[token] = term_counts.get(token, 0) + 2
    groups = []
    prompt_favorite_ids = set(favorite_ids)
    for group in (organization or {}).get("groups", []):
        matches = [
            paper["paper_id"]
            for paper in group.get("papers", [])
            if paper["paper_id"] in prompt_favorite_ids
        ]
        if matches:
            groups.append(
                {
                    "label": group["label"],
                    "matched_favorite_ids": matches,
                }
            )
    return {
        "enabled": True,
        "active": bool(favorites or research_profile.get("active")),
        "favorite_ids": all_favorite_ids,
        "papers": paper_context,
        "top_terms": [
            term
            for term, _ in sorted(term_counts.items(), key=lambda item: (-item[1], item[0]))[:30]
        ],
        "preference_labels": preference_labels,
        "negative_labels": list(research_profile.get("negative_labels", [])),
        "preference_event_count": int(research_profile.get("event_count", 0)),
        "preference_session_count": int(research_profile.get("session_count", 0)),
        "themes": [
            theme
            for theme, _ in sorted(theme_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
        ],
        "research_groups": groups[:12],
        "embedding_model": embedding_model if preference_vector else None,
        "preference_vector": preference_vector,
    }


def _candidates(
    service: PaperTrail,
    paper_ids: list[str],
    limit: int,
    *,
    personalization: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
            """,
            unique,
        ).fetchall()
        embedding_model = (
            f"{service.settings.embedding_provider}:{service.settings.embedding_model}"
        )
        candidate_vectors = _paper_vectors(db, unique, embedding_model)
    candidates = [{**dict(row), "authors": json.loads(row["authors_json"])} for row in rows]
    profile_terms = set((personalization or {}).get("top_terms", []))
    for candidate in candidates:
        candidate_terms = _preference_tokens(f"{candidate['title']} {candidate['abstract']}")
        matches = sorted(profile_terms & candidate_terms)
        candidate["preference_matches"] = matches[:8]
        lexical_score = (
            round(len(matches) / max(1.0, len(profile_terms) ** 0.5 * len(candidate_terms) ** 0.5), 4)
            if profile_terms
            else 0.0
        )
        candidate_vector = candidate_vectors.get(candidate["paper_id"])
        preference_vector = (personalization or {}).get("preference_vector")
        semantic_score = None
        if (
            isinstance(candidate_vector, list)
            and isinstance(preference_vector, list)
            and len(candidate_vector) == len(preference_vector)
        ):
            semantic_score = max(
                0.0,
                min(1.0, sum(a * b for a, b in zip(candidate_vector, preference_vector))),
            )
        candidate["lexical_preference_score"] = lexical_score
        candidate["semantic_preference_score"] = (
            round(semantic_score, 4) if semantic_score is not None else None
        )
        candidate["preference_score"] = round(
            0.8 * semantic_score + 0.2 * lexical_score
            if semantic_score is not None
            else lexical_score,
            4,
        )
    editorial = sorted(
        candidates,
        key=lambda item: (
            -item["scientific_record_count"],
            -item["figure_count"],
            -int((item["published_date"] or "0000-00-00").replace("-", "")),
            item["paper_id"],
        ),
    )
    if not (personalization or {}).get("active"):
        return editorial[:limit]
    preferred = sorted(
        candidates,
        key=lambda item: (
            -item["preference_score"],
            -item["scientific_record_count"],
            -item["figure_count"],
            item["paper_id"],
        ),
    )
    preference_slots = min(len(preferred), max(1, round(limit * 0.6)))
    selected = preferred[:preference_slots]
    selected_ids = {item["paper_id"] for item in selected}
    selected.extend(item for item in editorial if item["paper_id"] not in selected_ids)
    return selected[:limit]


def _prompt(
    snapshot_id: str,
    candidates: list[dict[str, Any]],
    blog_count: int,
    *,
    organization: dict[str, Any] | None = None,
    personalization: dict[str, Any] | None = None,
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
            "preference_score": item.get("preference_score", 0.0),
            "semantic_preference_score": item.get("semantic_preference_score"),
            "preference_matches": item.get("preference_matches", []),
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
    profile = personalization or {"enabled": False, "active": False}
    if profile.get("active"):
        source_summary = (
            f"{len(profile.get('favorite_ids', []))} starred papers and "
            f"{profile.get('preference_event_count', 0)} derived chat-interest signals"
        )
        selection_policy = (
            f"Personalization is active from {source_summary}. "
            + (
                "Choose exactly one preference-aligned paper."
                if blog_count == 1
                else "Choose at least one preference-aligned paper and at least one exploration "
                "paper outside the established preference profile. Do not choose more than "
                f"{blog_count - 1} preference-aligned papers."
            )
            + " For a preference pick, selection_mode must be 'preference', name one or more "
            "actual matched favourite IDs and/or exact matched preference labels from the local "
            "profile, and explain the concrete problem, mechanism, or limitation connection. "
            "For an exploration pick, selection_mode must be 'exploration', both match arrays "
            "must be empty, and explain why the paper is a "
            "useful surprise rather than random novelty. Favourites are ranking signals, not "
            "evidence; verify every scientific claim from the selected paper and corpus."
        )
    else:
        selection_policy = (
            "No active favourite profile is available. Use selection_mode 'editorial', keep "
            "both match arrays empty, and explain the evidence-based editorial reason."
        )
    return f"""
Use $papertrail-deep-research and only the read-only PaperTrail MCP tools.
Analyze today's newly indexed candidate papers against snapshot {snapshot_id}. Identify patterns
across the batch, then select exactly {blog_count} papers whose result, method, limitation, or
connection to prior work is genuinely surprising. Popularity alone is not a reason.

{selection_policy}

For each selection, inspect the exact paper, scientific records, supporting evidence passages,
related prior work, and available figures. Write a standalone 900-1500 word Markdown deep dive.
Distinguish source claims from your synthesis. Cite exact evidence IDs inline as [ev_...]. Do not
claim global novelty. Include only figure IDs returned by PaperTrail and discuss what each chosen
figure actually shows. For each blog, both its evidence_ids array and every inline [ev_...] citation
must belong to that blog's selected paper_id; never place evidence from a related paper in the
selected paper's evidence_ids. Mention related papers by title or related_paper_ids, but keep their
evidence out of the selected-paper deep dive. Apply the same selected-paper-only rule to figure_ids.
Do not write files or use shell tools.

Return only the requested JSON. Candidate papers:
{json.dumps(compact, separators=(',', ':'))}

Local preference profile derived from starred papers and consented Codex/Claude research chats.
Use it only for selection and
personalization—not as evidence about a candidate paper:
{json.dumps({key: value for key, value in profile.items() if key not in {'enabled', 'favorite_ids', 'preference_vector'}}, separators=(',', ':'))}

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
    required_blog_count: int,
    personalization: dict[str, Any],
) -> dict[str, Any]:
    blogs = output.get("blogs")
    if not isinstance(blogs, list) or len(blogs) != required_blog_count:
        raise ValueError(f"Daily agent must return exactly {required_blog_count} blogs")
    favorite_ids = set(personalization.get("favorite_ids", []))
    preference_labels = {
        str(label).casefold() for label in personalization.get("preference_labels", [])
    }
    personalization_active = bool(personalization.get("active"))
    modes = [str(item.get("selection_mode", "")) for item in blogs]
    if personalization_active:
        if "preference" not in modes:
            raise ValueError("Personalized daily selection requires a preference-aligned pick")
        if required_blog_count > 1 and "exploration" not in modes:
            raise ValueError("Personalized daily selection requires an exploration pick")
        if modes.count("preference") > max(1, required_blog_count - 1):
            raise ValueError("Personalized daily selection reserved too few exploration slots")
    elif any(mode != "editorial" for mode in modes):
        raise ValueError("Daily selection without a favourite profile must use editorial mode")
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
            selection_mode = str(item.get("selection_mode", ""))
            selection_reason = " ".join(str(item.get("selection_reason", "")).split())
            matched_favorite_ids = list(
                dict.fromkeys(map(str, item.get("matched_favorite_ids", [])))
            )
            matched_preference_labels = list(
                dict.fromkeys(map(str, item.get("matched_preference_labels", [])))
            )
            if selection_mode not in {"preference", "exploration", "editorial"}:
                raise ValueError(f"Daily blog for {paper_id} has an invalid selection mode")
            if len(selection_reason) < 20:
                raise ValueError(f"Daily blog for {paper_id} has no meaningful selection reason")
            if any(favorite_id not in favorite_ids for favorite_id in matched_favorite_ids):
                raise ValueError(f"Daily blog for {paper_id} cites an unknown favourite")
            if any(
                label.casefold() not in preference_labels
                for label in matched_preference_labels
            ):
                raise ValueError(f"Daily blog for {paper_id} cites an unknown preference label")
            if selection_mode == "preference" and not (
                matched_favorite_ids or matched_preference_labels
            ):
                raise ValueError(f"Preference blog for {paper_id} names no matching preference")
            if selection_mode != "preference" and (
                matched_favorite_ids or matched_preference_labels
            ):
                raise ValueError(
                    f"Non-preference blog for {paper_id} must not claim preference matches"
                )
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
            db.execute(
                "INSERT INTO daily_blog_personalization VALUES (?, ?, ?, ?, ?, ?)",
                (
                    blog_id,
                    selection_mode,
                    selection_reason,
                    json.dumps(matched_favorite_ids),
                    json.dumps(matched_preference_labels),
                    utc_now(),
                ),
            )
            saved.append(
                {
                    "blog_id": blog_id,
                    "slug": slug,
                    "paper_id": paper_id,
                    "selection_mode": selection_mode,
                    "selection_reason": selection_reason,
                    "matched_favorite_ids": matched_favorite_ids,
                    "matched_preference_labels": matched_preference_labels,
                }
            )
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
    matched = value.pop("matched_favorite_ids_json", None)
    value["matched_favorite_ids"] = json.loads(matched) if matched else []
    matched_labels = value.pop("matched_preference_labels_json", None)
    value["matched_preference_labels"] = json.loads(matched_labels) if matched_labels else []
    value["selection_mode"] = value.get("selection_mode") or "editorial"
    value["selection_reason"] = value.get("selection_reason") or (
        "Selected by the evidence-first editorial rubric before personalization was available."
    )
    return value
