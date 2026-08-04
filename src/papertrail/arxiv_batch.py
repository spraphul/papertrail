from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from .db import connect, transaction
from .service import PaperTrail, stable_id, utc_now


ATOM = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV = {"arxiv": "http://arxiv.org/schemas/atom"}
OPEN_SEARCH = {"open": "http://a9.com/-/spec/opensearch/1.1/"}


Progress = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ArxivBatchConfig:
    category: str
    from_date: date
    to_date: date
    page_size: int = 250
    request_delay: float = 3.1
    limit: int | None = None

    def validate(self) -> None:
        if not self.category or "." not in self.category:
            raise ValueError("Use a complete arXiv category such as cs.AI")
        if self.from_date > self.to_date:
            raise ValueError("from_date must be on or before to_date")
        if not 1 <= self.page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if self.request_delay < 3.0:
            raise ValueError("arXiv requires at least three seconds between requests")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be positive")

    def query(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "page_size": self.page_size,
            "request_delay": self.request_delay,
            "limit": self.limit,
        }


class ArxivBatchIngestor:
    def __init__(self, service: PaperTrail, progress: Progress | None = None):
        self.service = service
        self.progress = progress or (lambda event: None)
        self.service.initialize()
        self._last_request_at = 0.0

    def discover(self, config: ArxivBatchConfig) -> dict[str, Any]:
        config.validate()
        query = config.query()
        run_id = stable_id("run", "arxiv", json.dumps(query, sort_keys=True))
        created = utc_now()
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                """
                INSERT INTO ingestion_runs (
                    id, source, query_json, status, created_at, updated_at
                ) VALUES (?, 'arxiv', ?, 'discovering', ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (run_id, json.dumps(query, sort_keys=True), created, created),
            )
            run = db.execute("SELECT * FROM ingestion_runs WHERE id = ?", (run_id,)).fetchone()
        if run["status"] in {"discovered", "complete"}:
            return self.status(run_id)
        if run["total_expected"] and run["total_expected"] > 30_000:
            with transaction(self.service.settings.database_path) as db:
                db.execute(
                    "UPDATE ingestion_runs SET status = 'partition_required', updated_at = ? WHERE id = ?",
                    (utc_now(), run_id),
                )
            return self.status(run_id)

        cursor = run["next_cursor"]
        total_expected = run["total_expected"]
        while total_expected is None or cursor < total_expected:
            remaining = config.page_size
            if config.limit is not None:
                remaining = min(remaining, config.limit - cursor)
                if remaining <= 0:
                    break
            root = self._query_page(config, cursor, remaining)
            total_node = root.find("open:totalResults", OPEN_SEARCH)
            total_found = int(total_node.text or "0") if total_node is not None else 0
            total_expected = min(total_found, config.limit) if config.limit else total_found
            entries = root.findall("atom:entry", ATOM)
            if not entries:
                break
            with transaction(self.service.settings.database_path) as db:
                for entry in entries:
                    record = _parse_entry(entry)
                    discovery_id = stable_id("discovery", run_id, record["source_id"])
                    db.execute(
                        """
                        INSERT INTO discovery_records (
                            id, run_id, source, source_id, title, abstract, authors_json,
                            categories_json, primary_category, published_date, updated_at,
                            abstract_url, pdf_url, status, discovered_at
                        ) VALUES (?, ?, 'arxiv', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)
                        ON CONFLICT(run_id, source, source_id) DO UPDATE SET
                            title = excluded.title,
                            abstract = excluded.abstract,
                            authors_json = excluded.authors_json,
                            categories_json = excluded.categories_json,
                            primary_category = excluded.primary_category,
                            updated_at = excluded.updated_at,
                            abstract_url = excluded.abstract_url,
                            pdf_url = excluded.pdf_url
                        """,
                        (
                            discovery_id,
                            run_id,
                            record["source_id"],
                            record["title"],
                            record["abstract"],
                            json.dumps(record["authors"]),
                            json.dumps(record["categories"]),
                            record["primary_category"],
                            record["published_date"],
                            record["updated_at"],
                            record["abstract_url"],
                            record["pdf_url"],
                            utc_now(),
                        ),
                    )
                    db.execute(
                        """
                        UPDATE discovery_records
                        SET status = 'acquired', paper_id = (
                            SELECT prior.paper_id FROM discovery_records prior
                            WHERE prior.source = 'arxiv' AND prior.source_id = ?
                              AND prior.status = 'acquired' AND prior.paper_id IS NOT NULL
                              AND prior.id <> ?
                            ORDER BY prior.discovered_at DESC LIMIT 1
                        )
                        WHERE id = ? AND EXISTS (
                            SELECT 1 FROM discovery_records prior
                            WHERE prior.source = 'arxiv' AND prior.source_id = ?
                              AND prior.status = 'acquired' AND prior.paper_id IS NOT NULL
                              AND prior.id <> ?
                        )
                        """,
                        (
                            record["source_id"],
                            discovery_id,
                            discovery_id,
                            record["source_id"],
                            discovery_id,
                        ),
                    )
                    db.execute("DELETE FROM discovery_fts WHERE discovery_id = ?", (discovery_id,))
                    db.execute(
                        """
                        INSERT INTO discovery_fts (
                            discovery_id, title, abstract, authors, categories
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            discovery_id,
                            record["title"],
                            record["abstract"],
                            json.dumps(record["authors"]),
                            json.dumps(record["categories"]),
                        ),
                    )
                cursor += len(entries)
                discovered = db.execute(
                    "SELECT count(*) FROM discovery_records WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
                db.execute(
                    """
                    UPDATE ingestion_runs
                    SET total_expected = ?, discovered_count = ?, next_cursor = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (total_expected, discovered, cursor, utc_now(), run_id),
                )
            self.progress(
                {
                    "event": "arxiv.discovery.page",
                    "run_id": run_id,
                    "discovered": discovered,
                    "total_expected": total_expected,
                    "next_cursor": cursor,
                }
            )
            if total_found > 30_000 and config.limit is None:
                with transaction(self.service.settings.database_path) as db:
                    db.execute(
                        """
                        UPDATE ingestion_runs
                        SET status = 'partition_required', total_expected = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (total_found, utc_now(), run_id),
                    )
                return self.status(run_id)
            if len(entries) < remaining:
                break

        with transaction(self.service.settings.database_path) as db:
            discovered = db.execute(
                "SELECT count(*) FROM discovery_records WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            db.execute(
                """
                UPDATE ingestion_runs
                SET status = 'discovered', discovered_count = ?, total_expected = ?, updated_at = ?
                WHERE id = ?
                """,
                (discovered, total_expected or discovered, utc_now(), run_id),
            )
        return self.status(run_id)

    def discover_monthly(self, config: ArxivBatchConfig) -> dict[str, Any]:
        config.validate()
        group_query = {**config.query(), "partition": "calendar_month"}
        group_id = stable_id("group", "arxiv", json.dumps(group_query, sort_keys=True))
        created = utc_now()
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                """
                INSERT INTO ingestion_groups VALUES (?, 'arxiv', ?, 'discovering', ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (group_id, json.dumps(group_query, sort_keys=True), created, created),
            )
        for ordinal, (segment_from, segment_to) in enumerate(
            _month_segments(config.from_date, config.to_date)
        ):
            result = self.discover(
                ArxivBatchConfig(
                    category=config.category,
                    from_date=segment_from,
                    to_date=segment_to,
                    page_size=config.page_size,
                    request_delay=config.request_delay,
                    limit=None,
                )
            )
            if result["status"] == "partition_required":
                raise RuntimeError(
                    f"Monthly partition {segment_from} through {segment_to} still exceeds 30,000 results"
                )
            with transaction(self.service.settings.database_path) as db:
                db.execute(
                    "INSERT OR IGNORE INTO ingestion_group_runs VALUES (?, ?, ?)",
                    (group_id, result["id"], ordinal),
                )
            self.progress(
                {
                    "event": "arxiv.discovery.partition",
                    "group_id": group_id,
                    "from_date": segment_from.isoformat(),
                    "to_date": segment_to.isoformat(),
                    "run_id": result["id"],
                    "discovered": result["discovered_count"],
                }
            )
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "UPDATE ingestion_groups SET status = 'discovered', updated_at = ? WHERE id = ?",
                (utc_now(), group_id),
            )
        return self.group_status(group_id)

    def acquire_group(
        self,
        group_id: str,
        *,
        limit: int | None = None,
        discovery_ids: list[str] | None = None,
        retry_failed: bool = False,
        primary_only: bool = False,
        min_free_gb: float = 5.0,
        workers: int = 3,
    ) -> dict[str, Any]:
        with closing(connect(self.service.settings.database_path)) as db:
            rows = db.execute(
                """
                SELECT run_id FROM ingestion_group_runs
                WHERE group_id = ? ORDER BY ordinal
                """,
                (group_id,),
            ).fetchall()
        if not rows:
            raise KeyError(f"Unknown or empty ingestion group: {group_id}")
        remaining = limit
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "UPDATE ingestion_groups SET status = 'acquiring', updated_at = ? WHERE id = ?",
                (utc_now(), group_id),
            )
        acquired_paper_ids: list[str] = []
        newly_acquired_paper_ids: list[str] = []
        for row in rows:
            if remaining is not None and remaining <= 0:
                break
            before = self.status(row["run_id"])["acquired_count"]
            result = self.acquire(
                row["run_id"],
                limit=remaining,
                discovery_ids=discovery_ids,
                retry_failed=retry_failed,
                primary_only=primary_only,
                min_free_gb=min_free_gb,
                workers=workers,
            )
            acquired_paper_ids.extend(result.get("acquired_paper_ids", []))
            newly_acquired_paper_ids.extend(result.get("newly_acquired_paper_ids", []))
            if remaining is not None:
                remaining -= max(0, result["acquired_count"] - before)
        status = self.group_status(group_id)
        pending_key = "primary_pending_count" if primary_only else "pending_count"
        final = (
            "complete"
            if status[pending_key] == 0
            else "budgeted"
            if discovery_ids is not None
            else "discovered"
        )
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "UPDATE ingestion_groups SET status = ?, updated_at = ? WHERE id = ?",
                (final, utc_now(), group_id),
            )
        result = self.group_status(group_id)
        result["acquired_paper_ids"] = acquired_paper_ids
        result["newly_acquired_paper_ids"] = newly_acquired_paper_ids
        result["selected_discovery_ids"] = discovery_ids
        return result

    def group_status(self, group_id: str) -> dict[str, Any]:
        with closing(connect(self.service.settings.database_path)) as db:
            group = db.execute("SELECT * FROM ingestion_groups WHERE id = ?", (group_id,)).fetchone()
            if not group:
                raise KeyError(f"Unknown ingestion group: {group_id}")
            run_ids = [
                row["run_id"]
                for row in db.execute(
                    "SELECT run_id FROM ingestion_group_runs WHERE group_id = ? ORDER BY ordinal",
                    (group_id,),
                )
            ]
        runs = [self.status(run_id) for run_id in run_ids]
        result = dict(group)
        result["query"] = json.loads(result.pop("query_json"))
        result["partition_count"] = len(runs)
        result["total_expected"] = sum(item.get("total_expected") or 0 for item in runs)
        result["discovered_count"] = sum(item["discovered_count"] for item in runs)
        result["acquired_count"] = sum(item["acquired_count"] for item in runs)
        result["failed_count"] = sum(item["failed_count"] for item in runs)
        result["pending_count"] = sum(
            item["records_by_status"].get("discovered", 0) for item in runs
        )
        category = result["query"].get("category")
        with closing(connect(self.service.settings.database_path)) as db:
            primary = db.execute(
                """
                SELECT count(*) AS total,
                       sum(CASE WHEN d.status = 'acquired' THEN 1 ELSE 0 END) AS acquired,
                       sum(CASE WHEN d.status = 'failed' THEN 1 ELSE 0 END) AS failed
                FROM ingestion_group_runs gr
                JOIN discovery_records d ON d.run_id = gr.run_id
                WHERE gr.group_id = ? AND d.primary_category = ?
                """,
                (group_id, category),
            ).fetchone()
        result["primary_total_count"] = primary["total"]
        result["primary_acquired_count"] = primary["acquired"] or 0
        result["primary_failed_count"] = primary["failed"] or 0
        result["primary_pending_count"] = (
            result["primary_total_count"]
            - result["primary_acquired_count"]
            - result["primary_failed_count"]
        )
        result["runs"] = runs
        return result

    def search_metadata(
        self,
        query: str,
        *,
        group_id: str | None = None,
        limit: int = 20,
        primary_only: bool = False,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        joins = ""
        filters = ["discovery_fts MATCH ?"]
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
        if not terms:
            raise ValueError("query must contain at least one searchable term")
        fts_query = " ".join(f'"{term}"' for term in terms)
        params: list[Any] = [fts_query]
        if group_id:
            joins = "JOIN ingestion_group_runs igr ON igr.run_id = d.run_id"
            filters.append("igr.group_id = ?")
            params.append(group_id)
        if primary_only:
            if not group_id:
                raise ValueError("primary_only metadata search requires a group_id")
            filters.append(
                "d.primary_category = json_extract((SELECT query_json FROM ingestion_groups "
                "WHERE id = ?), '$.category')"
            )
            params.append(group_id)
        params.append(limit)
        with closing(connect(self.service.settings.database_path)) as db:
            rows = db.execute(
                f"""
                SELECT d.*, bm25(discovery_fts, 0.0, 4.0, 1.0, 0.3, 0.2) AS score
                FROM discovery_fts
                JOIN discovery_records d ON d.id = discovery_fts.discovery_id
                {joins}
                WHERE {' AND '.join(filters)}
                ORDER BY score, d.published_date DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {
                "source_id": row["source_id"],
                "title": row["title"],
                "abstract": row["abstract"],
                "authors": json.loads(row["authors_json"]),
                "primary_category": row["primary_category"],
                "categories": json.loads(row["categories_json"]),
                "published_date": row["published_date"],
                "status": row["status"],
                "abstract_url": row["abstract_url"],
                "score": row["score"],
            }
            for row in rows
        ]

    def acquire(
        self,
        run_id: str,
        *,
        limit: int | None = None,
        discovery_ids: list[str] | None = None,
        request_delay: float = 3.1,
        retry_failed: bool = False,
        primary_only: bool = False,
        min_free_gb: float = 5.0,
        workers: int = 3,
    ) -> dict[str, Any]:
        if request_delay < 3.0:
            raise ValueError("arXiv requires at least three seconds between requests")
        if min_free_gb < 0:
            raise ValueError("min_free_gb cannot be negative")
        if not 1 <= workers <= 8:
            raise ValueError("workers must be between 1 and 8")
        with closing(connect(self.service.settings.database_path)) as db:
            run = db.execute("SELECT * FROM ingestion_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                raise KeyError(f"Unknown ingestion run: {run_id}")
            statuses = ["discovered"] + (["failed"] if retry_failed else [])
            marks = ",".join("?" for _ in statuses)
            sql = f"""
                SELECT * FROM discovery_records
                WHERE run_id = ? AND status IN ({marks})
            """
            params: list[Any] = [run_id, *statuses]
            if primary_only:
                category = json.loads(run["query_json"])["category"]
                sql += " AND primary_category = ?"
                params.append(category)
            if discovery_ids is not None:
                if not discovery_ids:
                    records = []
                else:
                    selected_marks = ",".join("?" for _ in discovery_ids)
                    sql += f" AND id IN ({selected_marks})"
                    params.extend(discovery_ids)
            sql += " ORDER BY published_date, source_id"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            if discovery_ids is not None and not discovery_ids:
                records = []
            else:
                records = db.execute(sql, params).fetchall()
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "UPDATE ingestion_runs SET status = 'acquiring', updated_at = ? WHERE id = ?",
                (utc_now(), run_id),
            )
        acquired_paper_ids: list[str] = []
        newly_acquired_paper_ids: list[str] = []
        pending: dict[Future[dict[str, Any]], tuple[int, dict[str, Any]]] = {}

        def finish(
            index: int,
            row: dict[str, Any],
            future: Future[dict[str, Any]] | None = None,
            download_error: Exception | None = None,
        ) -> None:
            try:
                if download_error is not None:
                    raise download_error
                if future is None:
                    raise RuntimeError("Missing acquisition worker result")
                result = future.result()
                with transaction(self.service.settings.database_path) as db:
                    db.execute(
                        """
                        UPDATE discovery_records
                        SET status = 'acquired', paper_id = ?, attempts = attempts + 1,
                            error_code = NULL, error_message = NULL
                        WHERE id = ?
                        """,
                        (result["paper_id"], row["id"]),
                    )
                acquired_paper_ids.append(result["paper_id"])
                if result.get("status") != "already_ingested":
                    newly_acquired_paper_ids.append(result["paper_id"])
            except Exception as error:
                with transaction(self.service.settings.database_path) as db:
                    db.execute(
                        """
                        UPDATE discovery_records
                        SET status = 'failed', attempts = attempts + 1,
                            error_code = ?, error_message = ?
                        WHERE id = ?
                        """,
                        (error.__class__.__name__, str(error)[:1000], row["id"]),
                    )
            counts = self._update_counts(run_id)
            self.progress(
                {
                    "event": "arxiv.acquisition.paper",
                    "run_id": run_id,
                    "position": index,
                    "selected": len(records),
                    "source_id": row["source_id"],
                    **counts,
                }
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            for index, source_row in enumerate(records, 1):
                row = dict(source_row)
                free_bytes = shutil.disk_usage(self.service.settings.home).free
                if free_bytes < int(min_free_gb * 1024**3):
                    self.progress(
                        {
                            "event": "arxiv.acquisition.storage_stop",
                            "run_id": run_id,
                            "free_bytes": free_bytes,
                            "min_free_gb": min_free_gb,
                        }
                    )
                    break
                try:
                    content = self._download_pdf(row["pdf_url"], request_delay)
                except Exception as error:
                    finish(index, row, download_error=error)
                    continue
                future = executor.submit(self._ingest_downloaded_pdf, row, content)
                pending[future] = (index, row)
                if len(pending) >= workers:
                    completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for completed_future in completed:
                        completed_index, completed_row = pending.pop(completed_future)
                        finish(completed_index, completed_row, completed_future)
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for completed_future in completed:
                    completed_index, completed_row = pending.pop(completed_future)
                    finish(completed_index, completed_row, completed_future)
        counts = self._update_counts(run_id)
        final_status = "complete" if counts["pending"] == 0 else "discovered"
        with transaction(self.service.settings.database_path) as db:
            db.execute(
                "UPDATE ingestion_runs SET status = ?, updated_at = ? WHERE id = ?",
                (final_status, utc_now(), run_id),
            )
        result = self.status(run_id)
        result["acquired_paper_ids"] = acquired_paper_ids
        result["newly_acquired_paper_ids"] = newly_acquired_paper_ids
        return result

    def _ingest_downloaded_pdf(
        self, row: dict[str, Any], content: bytes
    ) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as temporary:
            temporary.write(content)
            temporary.flush()
            return self.service.ingest_pdf(
                Path(temporary.name),
                title=row["title"],
                authors=json.loads(row["authors_json"]),
                abstract=row["abstract"],
                published_date=row["published_date"],
                source_url=row["abstract_url"],
                source_class="preprint",
            )

    def status(self, run_id: str) -> dict[str, Any]:
        with closing(connect(self.service.settings.database_path)) as db:
            run = db.execute("SELECT * FROM ingestion_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                raise KeyError(f"Unknown ingestion run: {run_id}")
            counts = {
                row["status"]: row["count"]
                for row in db.execute(
                    """
                    SELECT status, count(*) AS count FROM discovery_records
                    WHERE run_id = ? GROUP BY status
                    """,
                    (run_id,),
                )
            }
            result = dict(run)
            result["query"] = json.loads(result.pop("query_json"))
            result["records_by_status"] = counts
            return result

    def list_runs(self) -> list[dict[str, Any]]:
        with closing(connect(self.service.settings.database_path)) as db:
            rows = db.execute(
                "SELECT id FROM ingestion_runs ORDER BY created_at DESC"
            ).fetchall()
        return [self.status(row["id"]) for row in rows]

    def _query_page(self, config: ArxivBatchConfig, start: int, max_results: int) -> ET.Element:
        from_stamp = config.from_date.strftime("%Y%m%d0000")
        to_stamp = config.to_date.strftime("%Y%m%d2359")
        search = f"cat:{config.category} AND submittedDate:[{from_stamp} TO {to_stamp}]"
        query = urllib.parse.urlencode(
            {
                "search_query": search,
                "start": start,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "ascending",
            }
        )
        content = self._request(f"https://export.arxiv.org/api/query?{query}", config.request_delay)
        return ET.fromstring(content)

    def _download_pdf(self, url: str, request_delay: float) -> bytes:
        try:
            content = self._request(url, request_delay, timeout=120)
        except (urllib.error.HTTPError, RuntimeError) as error:
            unversioned = re.sub(r"v\d+(?=(?:\.pdf)?$)", "", url)
            is_missing = (
                isinstance(error, urllib.error.HTTPError) and error.code == 404
            ) or "404" in str(error)
            if not is_missing or unversioned == url:
                raise
            content = self._request(unversioned, request_delay, timeout=120)
        if not content.startswith(b"%PDF"):
            raise RuntimeError("arXiv returned a non-PDF artifact")
        return content

    def _request(self, url: str, delay: float, timeout: int = 60) -> bytes:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "PaperTrailLocal/0.10 (personal research index; contact: local-user)"
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    content = response.read()
                self._last_request_at = time.monotonic()
                return content
            except urllib.error.HTTPError as error:
                self._last_request_at = time.monotonic()
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
            except urllib.error.URLError as error:
                self._last_request_at = time.monotonic()
                if "CERTIFICATE_VERIFY_FAILED" in str(error):
                    return self._request_with_curl(url, timeout)
                if attempt == 2:
                    return self._request_with_curl(url, timeout)
            time.sleep(delay * (attempt + 1))
        raise RuntimeError("Unreachable request retry state")

    def _request_with_curl(self, url: str, timeout: int) -> bytes:
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("arXiv TLS failed and the system curl fallback is unavailable")
        result = subprocess.run(
            [
                curl,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--max-time",
                str(timeout),
                "--user-agent",
                "PaperTrailLocal/0.10 (personal research index; contact: local-user)",
                url,
            ],
            capture_output=True,
        )
        self._last_request_at = time.monotonic()
        if result.returncode:
            raise RuntimeError(
                f"arXiv request failed through system curl: {result.stderr.decode(errors='replace')[:500]}"
            )
        return result.stdout

    def _update_counts(self, run_id: str) -> dict[str, int]:
        with transaction(self.service.settings.database_path) as db:
            counts = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status, count(*) AS count FROM discovery_records WHERE run_id = ? GROUP BY status",
                    (run_id,),
                )
            }
            acquired = counts.get("acquired", 0)
            failed = counts.get("failed", 0)
            pending = counts.get("discovered", 0)
            db.execute(
                """
                UPDATE ingestion_runs SET acquired_count = ?, failed_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (acquired, failed, utc_now(), run_id),
            )
        return {"acquired": acquired, "failed": failed, "pending": pending}


def _parse_entry(entry: ET.Element) -> dict[str, Any]:
    abstract_url = entry.findtext("atom:id", "", ATOM).replace("http://", "https://")
    source_id = abstract_url.rsplit("/", 1)[-1]
    pdf_url = f"https://arxiv.org/pdf/{source_id}"
    for link in entry.findall("atom:link", ATOM):
        if link.attrib.get("type") == "application/pdf":
            pdf_url = link.attrib.get("href", pdf_url).replace("http://", "https://")
    primary = entry.find("arxiv:primary_category", ARXIV)
    return {
        "source_id": source_id,
        "title": " ".join(entry.findtext("atom:title", "", ATOM).split()),
        "abstract": " ".join(entry.findtext("atom:summary", "", ATOM).split()),
        "authors": [
            " ".join(author.findtext("atom:name", "", ATOM).split())
            for author in entry.findall("atom:author", ATOM)
        ],
        "categories": [item.attrib.get("term", "") for item in entry.findall("atom:category", ATOM)],
        "primary_category": primary.attrib.get("term") if primary is not None else None,
        "published_date": entry.findtext("atom:published", "", ATOM)[:10] or None,
        "updated_at": entry.findtext("atom:updated", "", ATOM) or None,
        "abstract_url": abstract_url,
        "pdf_url": pdf_url,
    }


def _month_segments(from_date: date, to_date: date) -> list[tuple[date, date]]:
    segments: list[tuple[date, date]] = []
    cursor = from_date
    while cursor <= to_date:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        segment_end = min(to_date, next_month - timedelta(days=1))
        segments.append((cursor, segment_end))
        cursor = segment_end + timedelta(days=1)
    return segments
