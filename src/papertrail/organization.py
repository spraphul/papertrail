from __future__ import annotations

import json
import math
import re
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .db import connect, transaction
from .providers import IntelligenceProvider
from .service import PaperTrail, stable_id, utc_now


ALGORITHM_VERSION = "hybrid-llm-problem-taxonomy:v2"
PROJECTION_DIMENSIONS = 96
LLM_CLUSTER_BATCH_SIZE = 20
LLM_ABSTRACT_CHARACTERS = 1400
LLM_RECORD_CHARACTERS = 300
STOPWORDS = {
    "about", "after", "also", "among", "based", "been", "before", "being", "between",
    "both", "can", "could", "from", "have", "into", "more", "most", "other", "over",
    "approach", "approaches", "data", "method", "methods", "model", "models", "paper",
    "performance", "propose", "proposed", "result", "results", "show", "shows", "study",
    "such", "task", "tasks", "than", "that",
    "their", "these", "they", "this", "through", "toward", "using", "which", "while",
    "with", "without", "work", "we", "our", "the", "and", "for", "are", "was", "were",
}


@dataclass
class PaperFeature:
    paper_id: str
    title: str
    abstract: str
    source_url: str
    published_date: str | None
    records: dict[str, list[str]]
    tokens: set[str]
    vector: list[float] | None


@dataclass
class Cluster:
    members: list[tuple[PaperFeature, float]] = field(default_factory=list)
    centroid: list[float] | None = None
    tokens: Counter[str] = field(default_factory=Counter)
    llm_title: str | None = None
    llm_description: str | None = None
    llm_shared_problem: str | None = None
    llm_refined: bool = False

    def add(self, paper: PaperFeature, similarity: float) -> None:
        count = len(self.members)
        self.members.append((paper, similarity))
        self.tokens.update(paper.tokens)
        if paper.vector is None:
            return
        if self.centroid is None:
            self.centroid = list(paper.vector)
        else:
            self.centroid = [
                (current * count + value) / (count + 1)
                for current, value in zip(self.centroid, paper.vector)
            ]
            self.centroid = _normalize(self.centroid)


def organize_snapshot(
    service: PaperTrail,
    snapshot_id: str,
    *,
    new_paper_ids: list[str] | None = None,
    max_clusters: int = 48,
    similarity_threshold: float = 0.58,
    label_provider: IntelligenceProvider | None = None,
) -> dict[str, Any]:
    if not 2 <= max_clusters <= 100:
        raise ValueError("max_clusters must be between 2 and 100")
    if not 0.0 < similarity_threshold < 1.0:
        raise ValueError("similarity_threshold must be between 0 and 1")
    service.initialize()
    features, embedding_key = _paper_features(service, snapshot_id)
    run_date = date.today().isoformat()
    run_id = stable_id(
        "organization",
        run_date,
        snapshot_id,
        embedding_key,
        ALGORITHM_VERSION,
        str(max_clusters),
        f"{similarity_threshold:.4f}",
        _organization_model(label_provider),
    )
    if not features:
        return _persist(
            service,
            run_id,
            run_date,
            snapshot_id,
            embedding_key,
            [],
            set(),
            max_clusters,
            similarity_threshold,
            label_provider,
            {"enabled": label_provider is not None, "calls": 0, "fallback_clusters": 0},
        )
    clusters: list[Cluster] = []
    for paper in features:
        best_cluster = None
        best_score = -1.0
        for cluster in clusters:
            score = _cluster_similarity(paper, cluster)
            if score > best_score:
                best_cluster, best_score = cluster, score
        if best_cluster is not None and (
            best_score >= similarity_threshold or len(clusters) >= max_clusters
        ):
            best_cluster.add(paper, best_score)
        else:
            created = Cluster()
            created.add(paper, 1.0)
            clusters.append(created)
    clusters = _consolidate_singletons(clusters, floor=max(0.38, similarity_threshold - 0.16))
    clusters, refinement = _refine_clusters(clusters, label_provider)
    clusters.sort(key=lambda item: (-len(item.members), sorted(x[0].paper_id for x in item.members)))
    return _persist(
        service,
        run_id,
        run_date,
        snapshot_id,
        embedding_key,
        clusters,
        set(new_paper_ids or []),
        max_clusters,
        similarity_threshold,
        label_provider,
        refinement,
    )


def latest_organization(service: PaperTrail) -> dict[str, Any] | None:
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        run = db.execute(
            "SELECT * FROM organization_runs ORDER BY run_date DESC, created_at DESC LIMIT 1"
        ).fetchone()
        if not run:
            return None
        groups = []
        for cluster in db.execute(
            """
            SELECT * FROM paper_clusters WHERE organization_run_id = ?
            ORDER BY paper_count DESC, label
            """,
            (run["id"],),
        ):
            members = [
                {**dict(row), "authors": json.loads(row["authors_json"])}
                for row in db.execute(
                    """
                    SELECT m.paper_id, m.similarity, m.is_new, m.position,
                           p.canonical_title AS title, p.abstract, p.authors_json,
                           p.published_date, p.source_url
                    FROM paper_cluster_members m JOIN papers p ON p.id = m.paper_id
                    WHERE m.cluster_id = ? ORDER BY m.position
                    """,
                    (cluster["id"],),
                )
            ]
            groups.append(
                {
                    **dict(cluster),
                    "top_terms": json.loads(cluster["top_terms_json"]),
                    "papers": members,
                }
            )
    value = dict(run)
    value["configuration"] = json.loads(value.pop("configuration_json"))
    return {**value, "groups": groups}


def _paper_features(
    service: PaperTrail, snapshot_id: str
) -> tuple[list[PaperFeature], str]:
    embedding_key = f"{service.settings.embedding_provider}:{service.settings.embedding_model}"
    with closing(connect(service.settings.database_path)) as db:
        snapshot = db.execute("SELECT id FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if not snapshot:
            raise KeyError(f"Unknown snapshot: {snapshot_id}")
        papers = db.execute(
            """
            SELECT sp.paper_id, sp.paper_version_id, p.canonical_title, p.abstract,
                   p.source_url, p.published_date
            FROM snapshot_papers sp JOIN papers p ON p.id = sp.paper_id
            WHERE sp.snapshot_id = ? ORDER BY p.canonical_title, p.id
            """,
            (snapshot_id,),
        ).fetchall()
        version_to_paper = {row["paper_version_id"]: row["paper_id"] for row in papers}
        records: dict[str, dict[str, list[str]]] = {row["paper_id"]: {} for row in papers}
        for row in db.execute(
            """
            SELECT sr.paper_id, sr.record_type, sr.statement FROM snapshot_records ss
            JOIN scientific_records sr ON sr.id = ss.record_id
            WHERE ss.snapshot_id = ? AND sr.record_type IN
                  ('contribution', 'method', 'assumption', 'empirical_result',
                   'limitation', 'future_work')
            """,
            (snapshot_id,),
        ):
            records.setdefault(row["paper_id"], {}).setdefault(row["record_type"], []).append(
                row["statement"]
            )
        sums: dict[str, list[float]] = {}
        counts: Counter[str] = Counter()
        for row in db.execute(
            """
            SELECT e.paper_version_id, e.vector_json FROM snapshot_embeddings se
            JOIN embeddings e ON e.record_id = se.record_id AND e.model = se.model
            WHERE se.snapshot_id = ? AND se.model = ?
            """,
            (snapshot_id, embedding_key),
        ):
            paper_id = version_to_paper.get(row["paper_version_id"])
            if not paper_id:
                continue
            projected = _project(json.loads(row["vector_json"]))
            if paper_id not in sums:
                sums[paper_id] = [0.0] * PROJECTION_DIMENSIONS
            sums[paper_id] = [a + b for a, b in zip(sums[paper_id], projected)]
            counts[paper_id] += 1
    features = []
    for row in papers:
        paper_id = row["paper_id"]
        text = " ".join(
            [
                row["canonical_title"],
                row["abstract"],
                *[
                    statement
                    for values in records.get(paper_id, {}).values()
                    for statement in values
                ],
            ]
        )
        vector = None
        if counts[paper_id]:
            vector = _normalize([value / counts[paper_id] for value in sums[paper_id]])
        features.append(
            PaperFeature(
                paper_id=paper_id,
                title=row["canonical_title"],
                abstract=row["abstract"],
                source_url=row["source_url"],
                published_date=row["published_date"],
                records=records.get(paper_id, {}),
                tokens=_tokens(text),
                vector=vector,
            )
        )
    return features, embedding_key


def _persist(
    service: PaperTrail,
    run_id: str,
    run_date: str,
    snapshot_id: str,
    embedding_key: str,
    clusters: list[Cluster],
    new_ids: set[str],
    max_clusters: int,
    similarity_threshold: float,
    label_provider: IntelligenceProvider | None,
    refinement: dict[str, Any],
) -> dict[str, Any]:
    all_members = [paper for cluster in clusters for paper, _ in cluster.members]
    document_frequency: Counter[str] = Counter()
    for paper in all_members:
        document_frequency.update(paper.tokens)
    prepared = []
    for cluster in clusters:
        ordered = sorted(
            cluster.members,
            key=lambda item: (item[0].paper_id not in new_ids, -item[1], item[0].title),
        )
        prepared.append(
            (ordered, _top_terms(cluster, document_frequency, len(all_members)))
        )
    generated_labels = _generate_labels(
        prepared,
        label_provider,
        skip_indexes={index for index, cluster in enumerate(clusters) if cluster.llm_title},
    )
    now = utc_now()
    saved_groups = []
    used_labels: set[str] = set()
    with transaction(service.settings.database_path) as db:
        existing_cluster_ids = [
            row[0]
            for row in db.execute(
                "SELECT id FROM paper_clusters WHERE organization_run_id = ?", (run_id,)
            )
        ]
        if existing_cluster_ids:
            marks = ",".join("?" for _ in existing_cluster_ids)
            db.execute(f"DELETE FROM paper_cluster_members WHERE cluster_id IN ({marks})", existing_cluster_ids)
            db.execute("DELETE FROM paper_clusters WHERE organization_run_id = ?", (run_id,))
        db.execute("DELETE FROM organization_runs WHERE id = ?", (run_id,))
        db.execute(
            "INSERT INTO organization_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                run_date,
                snapshot_id,
                embedding_key,
                ALGORITHM_VERSION,
                json.dumps(
                    {
                        "max_clusters": max_clusters,
                        "similarity_threshold": similarity_threshold,
                        "semantic_weight": 0.78,
                        "lexical_weight": 0.22,
                        "projection_dimensions": PROJECTION_DIMENSIONS,
                        "llm_refinement": refinement,
                    },
                    sort_keys=True,
                ),
                len(all_members),
                sum(paper.vector is not None for paper in all_members),
                len(clusters),
                now,
            ),
        )
        for ordinal, (cluster, prepared_group) in enumerate(zip(clusters, prepared, strict=True)):
            ordered, terms = prepared_group
            member_ids = sorted(item[0].paper_id for item in ordered)
            cluster_id = stable_id("cluster", run_id, *member_ids)
            fallback_label = _fallback_label(ordered, ordinal)
            generated = generated_labels.get(ordinal, {})
            label = cluster.llm_title or generated.get("title", fallback_label)
            if label.casefold() in used_labels:
                label = fallback_label
            if label.casefold() in used_labels:
                label = f"{label} ({ordinal + 1})"
            used_labels.add(label.casefold())
            new_count = sum(paper.paper_id in new_ids for paper, _ in ordered)
            average = sum(score for _, score in ordered) / len(ordered)
            description = cluster.llm_description or generated.get(
                "description",
                f"Research related to {label.casefold()}, represented by {len(ordered)} papers.",
            )
            db.execute(
                "INSERT INTO paper_clusters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cluster_id,
                    run_id,
                    label,
                    description,
                    json.dumps(terms),
                    len(ordered),
                    new_count,
                    average,
                    now,
                ),
            )
            for position, (paper, similarity) in enumerate(ordered, 1):
                db.execute(
                    "INSERT INTO paper_cluster_members VALUES (?, ?, ?, ?, ?)",
                    (cluster_id, paper.paper_id, similarity, int(paper.paper_id in new_ids), position),
                )
            saved_groups.append(
                {
                    "cluster_id": cluster_id,
                    "label": label,
                    "paper_count": len(ordered),
                    "new_paper_count": new_count,
                    "average_similarity": round(average, 4),
                    "top_terms": terms,
                    "papers": [
                        {
                            "paper_id": paper.paper_id,
                            "title": paper.title,
                            "source_url": paper.source_url,
                            "is_new": paper.paper_id in new_ids,
                            "similarity": round(similarity, 4),
                        }
                        for paper, similarity in ordered
                    ],
                }
            )
    return {
        "status": "complete",
        "organization_run_id": run_id,
        "snapshot_id": snapshot_id,
        "algorithm": ALGORITHM_VERSION,
        "embedding_model": embedding_key,
        "configuration": {
            "max_clusters": max_clusters,
            "similarity_threshold": similarity_threshold,
            "semantic_weight": 0.78,
            "lexical_weight": 0.22,
            "projection_dimensions": PROJECTION_DIMENSIONS,
            "llm_refinement": refinement,
        },
        "paper_count": len(all_members),
        "semantic_paper_count": sum(paper.vector is not None for paper in all_members),
        "lexical_only_paper_count": sum(paper.vector is None for paper in all_members),
        "cluster_count": len(clusters),
        "groups": saved_groups,
    }


def _cluster_similarity(paper: PaperFeature, cluster: Cluster) -> float:
    lexical = _jaccard(paper.tokens, set(cluster.tokens))
    if paper.vector is None or cluster.centroid is None:
        return lexical
    semantic = max(-1.0, min(1.0, sum(a * b for a, b in zip(paper.vector, cluster.centroid))))
    return 0.78 * max(0.0, semantic) + 0.22 * lexical


def _consolidate_singletons(clusters: list[Cluster], floor: float) -> list[Cluster]:
    stable = [cluster for cluster in clusters if len(cluster.members) > 1]
    singletons = [cluster for cluster in clusters if len(cluster.members) == 1]
    if not stable:
        return clusters
    unresolved = []
    for singleton in singletons:
        paper = singleton.members[0][0]
        target, score = max(
            ((_cluster, _cluster_similarity(paper, _cluster)) for _cluster in stable),
            key=lambda item: item[1],
        )
        if score >= floor:
            target.add(paper, score)
        else:
            unresolved.append(singleton)
    return stable + unresolved


def _organization_model(provider: IntelligenceProvider | None) -> str:
    if provider is None:
        return "none"
    return f"{provider.provider_name}:{provider.reasoning_model}"


def _refine_clusters(
    clusters: list[Cluster], provider: IntelligenceProvider | None
) -> tuple[list[Cluster], dict[str, Any]]:
    stats: dict[str, Any] = {
        "enabled": provider is not None,
        "provider": None if provider is None else provider.provider_name,
        "model": None if provider is None else provider.reasoning_model,
        "calls": 0,
        "candidate_clusters": sum(len(cluster.members) > 1 for cluster in clusters),
        "refined_clusters": 0,
        "fallback_clusters": 0,
        "batch_size": LLM_CLUSTER_BATCH_SIZE,
        "context": ["title", "abstract", "scientific_records"],
    }
    if provider is None:
        return clusters, stats
    refined: list[Cluster] = []
    for cluster in clusters:
        if len(cluster.members) < 2:
            refined.append(cluster)
            continue
        members = sorted(cluster.members, key=lambda item: (item[0].title, item[0].paper_id))
        batches = [
            members[index : index + LLM_CLUSTER_BATCH_SIZE]
            for index in range(0, len(members), LLM_CLUSTER_BATCH_SIZE)
        ]
        for batch in batches:
            stats["calls"] += 1
            groups = _adjudicate_cluster(batch, provider)
            if groups is None:
                stats["fallback_clusters"] += 1
                fallback = Cluster()
                for paper, score in batch:
                    fallback.add(paper, score)
                refined.append(fallback)
                continue
            stats["refined_clusters"] += 1
            refined.extend(groups)
    return refined, stats


def _adjudicate_cluster(
    members: list[tuple[PaperFeature, float]], provider: IntelligenceProvider
) -> list[Cluster] | None:
    dossiers = [_paper_dossier(paper) for paper, _ in members]
    schema = {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "shared_problem": {"type": "string"},
                        "paper_ids": {"type": "array", "items": {"type": "string"}},
                        "membership_confidence": {"type": "number"},
                    },
                    "required": [
                        "title",
                        "description",
                        "shared_problem",
                        "paper_ids",
                        "membership_confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["groups"],
        "additionalProperties": False,
    }
    try:
        output = provider.structured(
            system=(
                "You are building a precise scientific problem taxonomy. Group papers only when "
                "they address the same concrete research problem and their objectives, evaluated "
                "setting, or technical mechanism are meaningfully compatible. Shared broad topics, "
                "model families, benchmark words, or generic methods are not enough. Use titles and "
                "abstracts for scope and typed scientific records for contributions, assumptions, "
                "results, and limitations. Prefer a singleton over a misleading group."
            ),
            prompt=(
                "Partition every paper below exactly once. Do not invent, omit, or duplicate IDs. "
                "A group needs a defensible shared problem; put a paper alone when the relationship "
                "is merely topical or uncertain. Titles must be specific 2-9 word research-problem "
                "labels. Descriptions must state both the inclusion rule and why the members belong. "
                "membership_confidence is 0 to 1 for the weakest member in that group.\n\n"
                + json.dumps({"papers": dossiers}, indent=2)
            ),
            schema=schema,
        )
    except (RuntimeError, ValueError, OSError):
        return None
    raw_groups = output.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return None
    by_id = {paper.paper_id: paper for paper, _ in members}
    expected = set(by_id)
    seen: set[str] = set()
    prepared: list[tuple[dict[str, Any], list[str]]] = []
    for item in raw_groups:
        if not isinstance(item, dict):
            return None
        paper_ids = item.get("paper_ids")
        if (
            not isinstance(paper_ids, list)
            or not paper_ids
            or any(not isinstance(paper_id, str) for paper_id in paper_ids)
        ):
            return None
        if any(paper_id not in expected or paper_id in seen for paper_id in paper_ids):
            return None
        seen.update(paper_ids)
        prepared.append((item, paper_ids))
    if seen != expected:
        return None
    groups = []
    for item, paper_ids in prepared:
        title = " ".join(str(item.get("title", "")).split()).strip(" .")
        description = " ".join(str(item.get("description", "")).split())
        shared_problem = " ".join(str(item.get("shared_problem", "")).split())
        confidence = item.get("membership_confidence")
        if (
            not 2 <= len(title.split()) <= 10
            or len(title) > 100
            or not description
            or not shared_problem
            or not isinstance(confidence, (int, float))
        ):
            return None
        score = max(0.0, min(1.0, float(confidence)))
        group = Cluster(
            llm_title=title,
            llm_description=description,
            llm_shared_problem=shared_problem,
            llm_refined=True,
        )
        for paper_id in paper_ids:
            group.add(by_id[paper_id], 1.0 if len(paper_ids) == 1 else score)
        groups.append(group)
    return groups


def _paper_dossier(paper: PaperFeature) -> dict[str, Any]:
    records = {
        record_type: [_clip(statement, LLM_RECORD_CHARACTERS) for statement in statements[:2]]
        for record_type, statements in sorted(paper.records.items())
        if statements
    }
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "abstract": _clip(paper.abstract, LLM_ABSTRACT_CHARACTERS),
        "scientific_records": records,
    }


def _clip(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _top_terms(cluster: Cluster, document_frequency: Counter[str], total: int) -> list[str]:
    scored = []
    for token, frequency in cluster.tokens.items():
        inverse = math.log((total + 1) / (document_frequency[token] + 1)) + 1.0
        scored.append((frequency * inverse, token))
    return [token for _, token in sorted(scored, key=lambda item: (-item[0], item[1]))[:6]]


def _generate_labels(
    groups: list[tuple[list[tuple[PaperFeature, float]], list[str]]],
    provider: IntelligenceProvider | None,
    *,
    skip_indexes: set[int] | None = None,
) -> dict[int, dict[str, str]]:
    if provider is None or not groups:
        return {}
    summaries = []
    skipped = skip_indexes or set()
    for index, (members, terms) in enumerate(groups):
        if index in skipped:
            continue
        summaries.append(
            {
                "cluster_index": index,
                "paper_count": len(members),
                "distinctive_terms": terms,
                "representative_titles": [paper.title for paper, _ in members[:8]],
            }
        )
    if not summaries:
        return {}
    schema = {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cluster_index": {"type": "integer"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["cluster_index", "title", "description"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["labels"],
        "additionalProperties": False,
    }
    try:
        output = provider.structured(
            system=(
                "You name scientific research neighborhoods. Produce specific, concise, "
                "problem-oriented labels that help a researcher understand why papers are grouped."
            ),
            prompt=(
                "Create one unique title and one plain-language sentence for every cluster below. "
                "Titles must be 2-9 words, use normal title case, and name both the concrete domain "
                "and the shared objective, constraint, or method. Never emit keyword bundles, "
                "middle-dot lists, paper titles, cluster numbers, or generic constructions such as "
                "'AI Research', 'Agent Performance', or 'Autonomy in Agents'.\n\n"
                + json.dumps(summaries, indent=2)
            ),
            schema=schema,
        )
    except (RuntimeError, ValueError, OSError):
        return {}
    labels: dict[int, dict[str, str]] = {}
    used: set[str] = set()
    for item in output.get("labels", []):
        if not isinstance(item, dict) or not isinstance(item.get("cluster_index"), int):
            continue
        index = item["cluster_index"]
        title = " ".join(str(item.get("title", "")).split()).strip(" .")
        description = " ".join(str(item.get("description", "")).split())
        normalized = title.casefold()
        if (
            index < 0
            or index >= len(groups)
            or not 2 <= len(title.split()) <= 10
            or len(title) > 100
            or "·" in title
            or normalized in used
            or not description
        ):
            continue
        used.add(normalized)
        labels[index] = {"title": title, "description": description}
    return labels


def _fallback_label(
    members: list[tuple[PaperFeature, float]], ordinal: int
) -> str:
    if not members:
        return f"Research Group {ordinal + 1}"
    title = re.sub(r"^(?:a|an|the)\s+", "", members[0][0].title, flags=re.IGNORECASE)
    words = title.split()
    if len(words) > 9:
        title = " ".join(words[:9]).rstrip(" ,:;-") + "…"
    return title or f"Research Group {ordinal + 1}"


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", text.casefold())
        if token not in STOPWORDS and not token.isdigit()
    }


def _project(vector: list[float]) -> list[float]:
    projected = [0.0] * PROJECTION_DIMENSIONS
    for index, raw in enumerate(vector):
        bucket = index % PROJECTION_DIMENSIONS
        sign = 1.0 if ((index * 2654435761) >> 8) & 1 else -1.0
        projected[bucket] += float(raw) * sign
    return _normalize(projected)


def _normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude else vector


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0
