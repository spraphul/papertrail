from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect, transaction
from .service import PaperTrail, utc_now

try:
    import certifi
except ImportError:  # pragma: no cover - package dependency supplies it in normal installs
    certifi = None


PROVIDER = "semantic_scholar"
DEFAULT_BASE_URL = "https://api.semanticscholar.org/graph/v1"
ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", re.IGNORECASE)
DOI_ID = re.compile(r"(?:doi\.org/|doi:)(10\.\d{4,9}/[^?#\s]+)", re.IGNORECASE)


def scholarly_id(source_url: str) -> tuple[str, str] | None:
    match = ARXIV_ID.search(source_url or "")
    if match:
        value = re.sub(r"\.pdf$", "", match.group(1), flags=re.IGNORECASE)
        value = re.sub(r"v\d+$", "", value, flags=re.IGNORECASE)
        return f"ARXIV:{value}", "arxiv"
    match = DOI_ID.search(source_url or "")
    if match:
        return f"DOI:{urllib.parse.unquote(match.group(1))}", "doi"
    return None


class SemanticScholarClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def papers(self, identifiers: list[str]) -> list[dict[str, Any] | None]:
        if not identifiers:
            return []
        request = urllib.request.Request(
            f"{self.base_url}/paper/batch?fields="
            "paperId,externalIds,citationCount,influentialCitationCount,referenceCount",
            data=json.dumps({"ids": identifiers}).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "PaperTrailLocal/0.11",
                **({"x-api-key": self.api_key} if self.api_key else {}),
            },
            method="POST",
        )
        context = ssl.create_default_context(cafile=certifi.where() if certifi else None)
        value: Any = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=context
                ) as response:
                    value = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 3:
                    retry_after = error.headers.get("Retry-After")
                    try:
                        delay = max(1.0, float(retry_after)) if retry_after else 2**attempt
                    except ValueError:
                        delay = 2**attempt
                    time.sleep(min(delay, 8.0))
                    continue
                raise RuntimeError(f"Semantic Scholar returned HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                raise RuntimeError("Semantic Scholar citation metadata is unavailable") from error
            except json.JSONDecodeError as error:
                raise RuntimeError("Semantic Scholar returned invalid JSON") from error
        if not isinstance(value, list) or len(value) != len(identifiers):
            raise RuntimeError("Semantic Scholar returned an invalid citation batch")
        return [item if isinstance(item, dict) else None for item in value]


def refresh_citations(
    service: PaperTrail,
    *,
    paper_ids: list[str] | None = None,
    stale_days: int = 7,
    client: SemanticScholarClient | None = None,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Refresh cached citation metadata without making ingestion depend on it."""
    if stale_days < 0:
        raise ValueError("stale_days cannot be negative")
    if not 1 <= batch_size <= 500:
        raise ValueError("batch_size must be between 1 and 500")
    service.initialize()
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    with closing(connect(service.settings.database_path)) as db:
        clauses: list[str] = []
        params: list[Any] = []
        if paper_ids is not None:
            unique = list(dict.fromkeys(paper_ids))
            if not unique:
                return _empty_result()
            clauses.append(f"p.id IN ({','.join('?' for _ in unique)})")
            params.extend(unique)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            f"""
            SELECT p.id AS paper_id, p.source_url, m.fetched_at
            FROM papers p LEFT JOIN paper_citation_metrics m
              ON m.paper_id = p.id AND m.provider = ?
            {where} ORDER BY p.id
            """,
            (PROVIDER, *params),
        ).fetchall()
    pending = []
    skipped_fresh = 0
    skipped_unmatched = 0
    for row in rows:
        if row["fetched_at"]:
            try:
                fetched = datetime.fromisoformat(str(row["fetched_at"]).replace("Z", "+00:00"))
            except ValueError:
                fetched = datetime.min.replace(tzinfo=timezone.utc)
            if fetched >= cutoff:
                skipped_fresh += 1
                continue
        identifier = scholarly_id(row["source_url"])
        if identifier is None:
            skipped_unmatched += 1
            continue
        pending.append((row["paper_id"], *identifier))
    active_client = client or SemanticScholarClient(service.settings.semantic_scholar_api_key)
    refreshed = 0
    unavailable = 0
    warnings: list[str] = []
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            response = active_client.papers([item[1] for item in batch])
        except RuntimeError as error:
            unavailable += len(batch)
            warnings.append(str(error))
            continue
        fetched_at = utc_now()
        with transaction(service.settings.database_path) as db:
            for (paper_id, _identifier, match_method), item in zip(batch, response, strict=True):
                if item is None or not item.get("paperId"):
                    unavailable += 1
                    continue
                try:
                    counts = [
                        _nonnegative_int(item.get(field))
                        for field in (
                            "citationCount",
                            "influentialCitationCount",
                            "referenceCount",
                        )
                    ]
                except RuntimeError as error:
                    unavailable += 1
                    warnings.append(str(error))
                    continue
                db.execute(
                    """
                    INSERT INTO paper_citation_metrics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(paper_id, provider) DO UPDATE SET
                        provider_work_id = excluded.provider_work_id,
                        citation_count = excluded.citation_count,
                        influential_citation_count = excluded.influential_citation_count,
                        reference_count = excluded.reference_count,
                        match_method = excluded.match_method,
                        match_confidence = excluded.match_confidence,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        paper_id,
                        PROVIDER,
                        str(item["paperId"]),
                        *counts,
                        match_method,
                        1.0,
                        fetched_at,
                    ),
                )
                refreshed += 1
    return {
        "provider": PROVIDER,
        "eligible": len(pending),
        "refreshed": refreshed,
        "unavailable": unavailable,
        "skipped_fresh": skipped_fresh,
        "skipped_unmatched": skipped_unmatched,
        "warnings": list(dict.fromkeys(warnings)),
    }


def refresh_discovery_citations(
    service: PaperTrail,
    *,
    group_id: str,
    client: SemanticScholarClient | None = None,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Fetch lightweight citation signals before capped PDF acquisition."""
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        rows = db.execute(
            """
            SELECT d.id AS discovery_id, d.source_id
            FROM discovery_records d
            JOIN ingestion_group_runs gr ON gr.run_id = d.run_id
            LEFT JOIN discovery_citation_metrics c ON c.discovery_id = d.id
              AND c.provider = ?
            WHERE gr.group_id = ? AND d.status IN ('discovered', 'failed')
              AND c.discovery_id IS NULL
            ORDER BY d.id
            """,
            (PROVIDER, group_id),
        ).fetchall()
    pending = [
        (row["discovery_id"], "ARXIV:" + re.sub("v[0-9]+$", "", row["source_id"]))
        for row in rows
    ]
    active_client = client or SemanticScholarClient(service.settings.semantic_scholar_api_key)
    refreshed = 0
    unavailable = 0
    warnings: list[str] = []
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            response = active_client.papers([item[1] for item in batch])
        except RuntimeError as error:
            unavailable += len(batch)
            warnings.append(str(error))
            continue
        fetched_at = utc_now()
        with transaction(service.settings.database_path) as db:
            for (discovery_id, _identifier), item in zip(batch, response, strict=True):
                if item is None or not item.get("paperId"):
                    unavailable += 1
                    continue
                try:
                    counts = [
                        _nonnegative_int(item.get(field))
                        for field in (
                            "citationCount",
                            "influentialCitationCount",
                            "referenceCount",
                        )
                    ]
                except RuntimeError as error:
                    unavailable += 1
                    warnings.append(str(error))
                    continue
                db.execute(
                    """
                    INSERT OR REPLACE INTO discovery_citation_metrics
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (discovery_id, PROVIDER, str(item["paperId"]), *counts, fetched_at),
                )
                refreshed += 1
    return {
        "provider": PROVIDER,
        "eligible": len(pending),
        "refreshed": refreshed,
        "unavailable": unavailable,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RuntimeError("Semantic Scholar returned an invalid citation count")
    return int(value)


def _empty_result() -> dict[str, Any]:
    return {
        "provider": PROVIDER,
        "eligible": 0,
        "refreshed": 0,
        "unavailable": 0,
        "skipped_fresh": 0,
        "skipped_unmatched": 0,
        "warnings": [],
    }
