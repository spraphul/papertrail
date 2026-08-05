from __future__ import annotations

import math
import re
from datetime import date
from typing import Any


FORMULA_VERSION = "group-rank-v1"
TOKEN_RE = re.compile(r"[a-z][a-z0-9-]{2,}", re.IGNORECASE)
STOPWORDS = {
    "about", "after", "also", "among", "based", "from", "into", "paper", "papers",
    "research", "that", "their", "these", "this", "using", "which", "with", "work",
}


def rank_group_papers(
    papers: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    if not papers:
        return []
    current = today or date.today()
    positive = _tokens(" ".join(map(str, profile.get("positive_labels", []))))
    negative = _tokens(" ".join(map(str, profile.get("negative_labels", []))))
    personalized = bool(profile.get("active") and positive)
    citation_scores = _citation_percentiles(papers, current)
    ranked = []
    for paper in papers:
        similarity = _bounded(paper.get("similarity"), default=0.0)
        affinity = _affinity(paper, positive, negative) if personalized else None
        recency = _recency(paper.get("published_date"), current)
        citation = citation_scores.get(str(paper.get("paper_id")))
        components = {
            "neighborhood_relevance": similarity,
            "personal_affinity": affinity,
            "recency": recency,
            "citation_impact": citation,
        }
        base_weights = {
            "neighborhood_relevance": 0.30 if personalized else 0.50,
            "personal_affinity": 0.30 if personalized else 0.0,
            "recency": 0.25 if personalized else 0.35,
            "citation_impact": 0.15,
        }
        available_weight = sum(
            base_weights[key]
            for key, value in components.items()
            if value is not None and base_weights[key] > 0
        )
        score = (
            sum(
                base_weights[key] * float(value)
                for key, value in components.items()
                if value is not None
            )
            / available_weight
            if available_weight
            else similarity
        )
        result = dict(paper)
        result["ranking"] = {
            "formula_version": FORMULA_VERSION,
            "score": round(score, 6),
            "components": {
                key: round(value, 6) if value is not None else None
                for key, value in components.items()
            },
            "reasons": _reasons(affinity, recency, citation, paper),
        }
        ranked.append(result)
    ranked.sort(key=_sort_key)
    for position, paper in enumerate(ranked, 1):
        paper["ranking"]["position"] = position
    return ranked


def _affinity(
    paper: dict[str, Any], positive: set[str], negative: set[str]
) -> float | None:
    if not positive:
        return None
    tokens = _tokens(
        f"{paper.get('title', '')} {paper.get('abstract', '')} "
        f"{paper.get('ranking_context', '')}"
    )
    if not tokens:
        return 0.0
    positive_score = len(tokens & positive) / max(1.0, math.sqrt(len(tokens) * len(positive)))
    negative_score = len(tokens & negative) / max(1.0, math.sqrt(len(tokens) * len(negative)))
    return max(0.0, min(1.0, positive_score * 3.0 - negative_score * 2.0))


def _recency(value: Any, today: date) -> float | None:
    if not value:
        return None
    try:
        published = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    age = max(0, (today - published).days)
    return max(0.0, min(1.0, 2 ** (-age / 180.0)))


def _citation_percentiles(
    papers: list[dict[str, Any]], today: date
) -> dict[str, float | None]:
    cohorts: dict[str, list[tuple[str, float]]] = {}
    result: dict[str, float | None] = {}
    for paper in papers:
        paper_id = str(paper.get("paper_id"))
        count = paper.get("citation_count")
        try:
            published = date.fromisoformat(str(paper.get("published_date"))[:10])
        except ValueError:
            result[paper_id] = None
            continue
        if count is None:
            result[paper_id] = None
            continue
        age = max(0, (today - published).days)
        if age < 14:
            result[paper_id] = 0.5
            continue
        cohort = "30" if age < 30 else "90" if age < 90 else "365" if age < 365 else "old"
        cohorts.setdefault(cohort, []).append((paper_id, math.log1p(max(0, int(count)))))
    for values in cohorts.values():
        ordered = sorted(score for _, score in values)
        if len(ordered) == 1:
            result[values[0][0]] = 0.5
            continue
        for paper_id, score in values:
            below = sum(value < score for value in ordered)
            equal = sum(value == score for value in ordered)
            result[paper_id] = (below + (equal - 1) / 2) / (len(ordered) - 1)
    return result


def _reasons(
    affinity: float | None,
    recency: float | None,
    citation: float | None,
    paper: dict[str, Any],
) -> list[str]:
    candidates: list[tuple[float, str]] = []
    if affinity is not None and affinity >= 0.25:
        candidates.append((affinity, "Strong match to your interests"))
    if paper.get("is_new") or (recency is not None and recency >= 0.9):
        candidates.append((recency or 0.9, "New this month"))
    if citation is not None and citation >= 0.75:
        candidates.append((citation, "Highly cited for its age"))
    if _bounded(paper.get("similarity"), default=0.0) >= 0.7:
        candidates.append((float(paper["similarity"]), "Central to this neighborhood"))
    return [label for _, label in sorted(candidates, key=lambda item: (-item[0], item[1]))[:2]]


def _sort_key(paper: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -paper["ranking"]["score"],
        -_date_ordinal(paper.get("published_date")),
        -_bounded(paper.get("similarity"), default=0.0),
        str(paper.get("title", "")).casefold(),
        str(paper.get("paper_id", "")),
    )


def _date_ordinal(value: Any) -> int:
    try:
        return date.fromisoformat(str(value)[:10]).toordinal()
    except ValueError:
        return 0


def _tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_RE.findall(text or "")
        if token.casefold() not in STOPWORDS
    }


def _bounded(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default
