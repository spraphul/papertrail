from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .db import connect, transaction
from .providers import IntelligenceProvider
from .service import PaperTrail, stable_id, utc_now


PREFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["topic", "problem", "method", "artifact", "positive", "negative"],
                    },
                    "label": {"type": "string"},
                    "context": {"type": "string"},
                    "confidence": {"type": "number"},
                    "explicitness": {
                        "type": "string",
                        "enum": ["explicit", "inferred"],
                    },
                },
                "required": ["kind", "label", "context", "confidence", "explicitness"],
            },
        }
    },
    "required": ["events"],
}

ALLOWED_SOURCES = {"codex", "claude"}
ALLOWED_KINDS = {"topic", "problem", "method", "artifact", "positive", "negative"}
ALLOWED_EXPLICITNESS = {"explicit", "inferred"}
RESEARCH_HINTS = re.compile(
    r"\b(paper|research|arxiv|study|hypothesis|experiment|evaluation|benchmark|dataset|"
    r"model|agent|algorithm|method|architecture|training|inference|embedding|retrieval|"
    r"reasoning|scientific|literature|theorem|proof|simulation|biology|protein|medicine|"
    r"physics|chemistry|economics|robot|vision|language|learning)\b",
    re.IGNORECASE,
)
SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(
        r"(?i)\b(api[_ -]?key|access[_ -]?token|secret|password)\b\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)
TOKEN_RE = re.compile(r"[a-z][a-z0-9-]{2,}", re.IGNORECASE)
STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "been",
    "being",
    "build",
    "could",
    "from",
    "have",
    "into",
    "more",
    "paper",
    "papers",
    "research",
    "should",
    "that",
    "their",
    "there",
    "these",
    "this",
    "those",
    "using",
    "want",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}


@dataclass(frozen=True)
class HistorySession:
    source: str
    path: Path
    fingerprint: str
    content_digest: str
    observed_at: str


def configure_preference_sources(
    service: PaperTrail,
    enabled_sources: Iterable[str],
    *,
    history_paths: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    service.initialize()
    enabled = set(enabled_sources)
    invalid = enabled - ALLOWED_SOURCES
    if invalid:
        raise ValueError(f"Unknown preference source: {', '.join(sorted(invalid))}")
    now = utc_now()
    paths = history_paths or {}
    with transaction(service.settings.database_path) as db:
        for source in sorted(ALLOWED_SOURCES):
            current = db.execute(
                "SELECT consented_at, history_path FROM preference_sources WHERE source = ?",
                (source,),
            ).fetchone()
            is_enabled = source in enabled
            consented = (current["consented_at"] if current else None) or (
                now if is_enabled else None
            )
            history_path = paths.get(source)
            if history_path is None and current:
                history_path = current["history_path"]
            db.execute(
                """
                INSERT INTO preference_sources (
                    source, enabled, consented_at, history_path, status
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    enabled = excluded.enabled,
                    consented_at = excluded.consented_at,
                    history_path = excluded.history_path,
                    status = excluded.status,
                    error_summary = NULL
                """,
                (
                    source,
                    int(is_enabled),
                    consented,
                    history_path,
                    "ready" if is_enabled else "disabled",
                ),
            )
    return preference_sources(service)


def preference_sources(service: PaperTrail) -> list[dict[str, Any]]:
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        rows = db.execute(
            """
            SELECT s.source, s.enabled, s.consented_at, s.history_path, s.last_scanned_at,
                   s.status, s.error_summary,
                   count(DISTINCT x.fingerprint) AS session_count,
                   count(e.id) AS event_count
            FROM preference_sources s
            LEFT JOIN preference_sessions x ON x.source = s.source
            LEFT JOIN preference_events e ON e.session_fingerprint = x.fingerprint
            GROUP BY s.source ORDER BY s.source
            """
        ).fetchall()
    return [
        {
            **dict(row),
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]


def set_preference_source(service: PaperTrail, source: str, enabled: bool) -> dict[str, Any]:
    _validate_source(source)
    service.initialize()
    now = utc_now()
    with transaction(service.settings.database_path) as db:
        current = db.execute(
            "SELECT consented_at FROM preference_sources WHERE source = ?", (source,)
        ).fetchone()
        consented = (current["consented_at"] if current else None) or (
            now if enabled else None
        )
        db.execute(
            """
            INSERT INTO preference_sources (source, enabled, consented_at, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET enabled = excluded.enabled,
                consented_at = excluded.consented_at, status = excluded.status,
                error_summary = NULL
            """,
            (source, int(enabled), consented, "ready" if enabled else "disabled"),
        )
    return next(item for item in preference_sources(service) if item["source"] == source)


def forget_preferences(service: PaperTrail, source: str) -> dict[str, Any]:
    if source != "all":
        _validate_source(source)
    service.initialize()
    targets = sorted(ALLOWED_SOURCES) if source == "all" else [source]
    with transaction(service.settings.database_path) as db:
        marks = ",".join("?" for _ in targets)
        deleted_events = db.execute(
            f"SELECT count(*) FROM preference_events WHERE source IN ({marks})", targets
        ).fetchone()[0]
        db.execute(f"DELETE FROM preference_sessions WHERE source IN ({marks})", targets)
        db.execute(
            f"""
            UPDATE preference_sources SET enabled = 0, consented_at = NULL,
                last_scanned_at = NULL, status = 'disabled', error_summary = NULL
            WHERE source IN ({marks})
            """,
            targets,
        )
    return {"status": "forgotten", "sources": targets, "deleted_events": deleted_events}


def rebuild_preferences(service: PaperTrail) -> dict[str, Any]:
    service.initialize()
    with transaction(service.settings.database_path) as db:
        enabled = [
            row["source"]
            for row in db.execute("SELECT source FROM preference_sources WHERE enabled = 1")
        ]
        marks = ",".join("?" for _ in enabled)
        deleted = 0
        if enabled:
            deleted = db.execute(
                f"SELECT count(*) FROM preference_events WHERE source IN ({marks})", enabled
            ).fetchone()[0]
            db.execute(f"DELETE FROM preference_sessions WHERE source IN ({marks})", enabled)
            db.execute(
                f"UPDATE preference_sources SET last_scanned_at = NULL, status = 'ready', "
                f"error_summary = NULL WHERE source IN ({marks})",
                enabled,
            )
    return {"status": "ready_for_rebuild", "sources": enabled, "deleted_events": deleted}


def sync_preferences(
    service: PaperTrail,
    provider: IntelligenceProvider,
    *,
    user_home: Path | None = None,
) -> dict[str, Any]:
    service.initialize()
    sources = [item for item in preference_sources(service) if item["enabled"]]
    result: dict[str, Any] = {
        "status": "complete",
        "sources": [],
        "processed_sessions": 0,
        "unchanged_sessions": 0,
        "accepted_events": 0,
        "failed_sessions": 0,
    }
    for source_config in sources:
        source = source_config["source"]
        source_result = _sync_source(
            service,
            provider,
            source,
            history_path=source_config.get("history_path"),
            user_home=user_home,
        )
        result["sources"].append(source_result)
        for key in (
            "processed_sessions",
            "unchanged_sessions",
            "accepted_events",
            "failed_sessions",
        ):
            result[key] += source_result[key]
    if result["failed_sessions"]:
        result["status"] = "partial"
    result["profile"] = aggregate_profile(service, persist=True)
    return result


def _sync_source(
    service: PaperTrail,
    provider: IntelligenceProvider,
    source: str,
    *,
    history_path: str | None,
    user_home: Path | None,
) -> dict[str, Any]:
    files = discover_history_files(
        source,
        history_path=Path(history_path).expanduser() if history_path else None,
        user_home=user_home,
    )
    outcome = {
        "source": source,
        "found_sessions": len(files),
        "processed_sessions": 0,
        "unchanged_sessions": 0,
        "accepted_events": 0,
        "failed_sessions": 0,
        "errors": [],
    }
    for path in files:
        try:
            session = _session_ref(source, path)
            with closing(connect(service.settings.database_path)) as db:
                existing = db.execute(
                    "SELECT content_digest FROM preference_sessions WHERE fingerprint = ?",
                    (session.fingerprint,),
                ).fetchone()
            if existing and existing["content_digest"] == session.content_digest:
                outcome["unchanged_sessions"] += 1
                continue
            turns = [redact_secrets(text) for text in read_research_turns(source, path)]
            turns = [text for text in turns if text and RESEARCH_HINTS.search(text)]
            events = _extract_events(provider, turns, session.observed_at) if turns else []
            _replace_session_events(service, session, events)
            outcome["processed_sessions"] += 1
            outcome["accepted_events"] += len(events)
        except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError, ValueError) as error:
            outcome["failed_sessions"] += 1
            outcome["errors"].append(
                {"fingerprint": _opaque_path_fingerprint(source, path), "error": str(error)[:240]}
            )
    status = "unavailable" if not files else ("partial" if outcome["failed_sessions"] else "ready")
    error_summary = (
        f"{outcome['failed_sessions']} session(s) failed" if outcome["failed_sessions"] else None
    )
    with transaction(service.settings.database_path) as db:
        db.execute(
            """
            UPDATE preference_sources SET last_scanned_at = ?, status = ?, error_summary = ?
            WHERE source = ?
            """,
            (utc_now(), status, error_summary, source),
        )
    outcome["status"] = status
    return outcome


def discover_history_files(
    source: str,
    *,
    history_path: Path | None = None,
    user_home: Path | None = None,
) -> list[Path]:
    _validate_source(source)
    roots: list[Path]
    if history_path:
        roots = [history_path]
    else:
        home = (user_home or Path.home()).expanduser()
        roots = (
            [home / ".codex" / "sessions", home / ".codex" / "history.jsonl"]
            if source == "codex"
            else [home / ".claude" / "projects", home / ".claude" / "history.jsonl"]
        )
    found: dict[str, Path] = {}
    for root in roots:
        if root.is_file() and root.suffix in {".jsonl", ".json"}:
            found[str(root.resolve())] = root.resolve()
        elif root.is_dir():
            for suffix in ("*.jsonl", "*.json"):
                for path in root.rglob(suffix):
                    if path.is_file():
                        found[str(path.resolve())] = path.resolve()
    return sorted(found.values(), key=lambda item: (item.stat().st_mtime_ns, str(item)))


def read_research_turns(source: str, path: Path) -> list[str]:
    _validate_source(source)
    turns: list[str] = []
    with path.open(errors="replace") as stream:
        if path.suffix == ".json":
            value = json.load(stream)
            values = value if isinstance(value, list) else [value]
        else:
            values = []
            for line in stream:
                if not line.strip():
                    continue
                try:
                    values.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    for value in values:
        text = _user_text_from_event(source, value)
        if text:
            normalized = " ".join(text.split())
            if 20 <= len(normalized) <= 12_000:
                turns.append(normalized)
    return turns[-80:]


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _user_text_from_event(source: str, value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    role = value.get("role")
    content = value.get("content")
    if role == "user":
        return _content_text(content)
    if source == "codex" and isinstance(value.get("text"), str) and value.get("session_id"):
        return value["text"]
    payload = value.get("payload")
    if value.get("type") == "response_item" and isinstance(payload, dict):
        if payload.get("role") == "user" or (
            payload.get("type") == "message" and payload.get("role") == "user"
        ):
            return _content_text(payload.get("content"))
    message = value.get("message")
    if isinstance(message, dict) and message.get("role") == "user":
        return _content_text(message.get("content"))
    if source == "claude" and value.get("type") == "user" and isinstance(message, dict):
        return _content_text(message.get("content"))
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    values: list[str] = []
    for item in content:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
            text = item.get("text")
            if isinstance(text, str):
                values.append(text)
    return "\n".join(values)


def _session_ref(source: str, path: Path) -> HistorySession:
    content = path.read_bytes()
    stat = path.stat()
    fingerprint = _opaque_path_fingerprint(source, path)
    digest = hashlib.sha256(content).hexdigest()
    observed = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return HistorySession(source, path, fingerprint, digest, observed)


def _opaque_path_fingerprint(source: str, path: Path) -> str:
    return stable_id("history", source, str(path.expanduser().resolve()))


def _extract_events(
    provider: IntelligenceProvider, turns: list[str], observed_at: str
) -> list[dict[str, Any]]:
    if not turns:
        return []
    bounded: list[str] = []
    remaining = 24_000
    for turn in reversed(turns):
        clipped = turn[:4000]
        if len(clipped) > remaining:
            clipped = clipped[:remaining]
        if not clipped:
            break
        bounded.append(clipped)
        remaining -= len(clipped)
        if remaining <= 0:
            break
    bounded.reverse()
    prompt = json.dumps(
        {"user_research_turns": bounded}, ensure_ascii=False, separators=(",", ":")
    )
    output = provider.structured(
        system=(
            "Extract durable research preferences from user-authored turns. Return only research "
            "topics, problems, methods, artifacts, explicit interests, or explicit dislikes. "
            "Do not infer sensitive personal traits. Do not treat assistant suggestions or "
            "scientific claims as preferences. Merge duplicates and use concise labels."
        ),
        prompt=prompt,
        schema=PREFERENCE_SCHEMA,
    )
    raw_events = output.get("events")
    if not isinstance(raw_events, list):
        raise RuntimeError("Preference extractor returned an invalid event list")
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_events[:24]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        explicitness = item.get("explicitness")
        label = _clean_label(item.get("label"))
        context = _clean_context(item.get("context"))
        confidence = item.get("confidence")
        if (
            kind not in ALLOWED_KINDS
            or explicitness not in ALLOWED_EXPLICITNESS
            or not label
            or not isinstance(confidence, (int, float))
        ):
            continue
        key = (str(kind), label.casefold())
        if key in seen:
            continue
        seen.add(key)
        events.append(
            {
                "kind": str(kind),
                "label": label,
                "context": context,
                "confidence": max(0.0, min(1.0, float(confidence))),
                "explicitness": str(explicitness),
                "observed_at": observed_at,
            }
        )
    return events


def _replace_session_events(
    service: PaperTrail, session: HistorySession, events: list[dict[str, Any]]
) -> None:
    created = utc_now()
    with transaction(service.settings.database_path) as db:
        db.execute(
            """
            INSERT INTO preference_sessions (
                fingerprint, source, content_digest, observed_at, processed_at, event_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                content_digest = excluded.content_digest,
                observed_at = excluded.observed_at,
                processed_at = excluded.processed_at,
                event_count = excluded.event_count
            """,
            (
                session.fingerprint,
                session.source,
                session.content_digest,
                session.observed_at,
                created,
                len(events),
            ),
        )
        db.execute(
            "DELETE FROM preference_events WHERE session_fingerprint = ?",
            (session.fingerprint,),
        )
        for item in events:
            event_id = stable_id(
                "preference",
                session.fingerprint,
                item["kind"],
                item["label"].casefold(),
            )
            db.execute(
                """
                INSERT INTO preference_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session.source,
                    session.fingerprint,
                    item["kind"],
                    item["label"],
                    item["context"],
                    item["confidence"],
                    item["explicitness"],
                    item["observed_at"],
                    created,
                ),
            )


def aggregate_profile(service: PaperTrail, *, persist: bool = False) -> dict[str, Any]:
    service.initialize()
    with closing(connect(service.settings.database_path)) as db:
        events = db.execute(
            """
            SELECT e.* FROM preference_events e ORDER BY e.observed_at DESC
            """
        ).fetchall()
        favorites = db.execute(
            """
            SELECT p.id, p.canonical_title, p.abstract
            FROM paper_favorites f JOIN papers p ON p.id = f.paper_id
            ORDER BY f.created_at DESC
            """
        ).fetchall()
    weights: dict[str, float] = defaultdict(float)
    labels: dict[str, str] = {}
    negative: dict[str, float] = defaultdict(float)
    explicit_count = 0
    session_ids: set[str] = set()
    now = datetime.now(timezone.utc)
    for event in events:
        label_key = event["label"].casefold()
        labels[label_key] = event["label"]
        observed = _parse_datetime(event["observed_at"])
        age_days = max(0.0, (now - observed).total_seconds() / 86400.0)
        decay = 0.5 ** (age_days / 180.0)
        authority = 1.0 if event["explicitness"] == "explicit" else 0.65
        score = float(event["confidence"]) * authority * decay
        if event["kind"] == "negative":
            negative[label_key] += score
        else:
            weights[label_key] += score
        if event["explicitness"] == "explicit" and event["kind"] != "negative":
            explicit_count += 1
        session_ids.add(event["session_fingerprint"])
    for favorite in favorites:
        for token in _tokens(f"{favorite['canonical_title']} {favorite['abstract']}"):
            labels.setdefault(token, token)
            weights[token] += 1.5
    concepts = []
    for key in set(weights) | set(negative):
        net = max(-3.0, min(6.0, weights[key] - negative[key]))
        if abs(net) >= 0.15:
            concepts.append(
                {
                    "label": labels.get(key, key),
                    "weight": round(net, 4),
                    "polarity": "negative" if net < 0 else "positive",
                }
            )
    concepts.sort(key=lambda item: (-abs(item["weight"]), item["label"].casefold()))
    active_for_ingestion = len(favorites) >= 3 or (
        len(events) >= 8 and len(session_ids) >= 3 and explicit_count >= 1
    )
    active = bool(favorites or events)
    summary = {
        "active": active,
        "active_for_ingestion": active_for_ingestion,
        "favorite_count": len(favorites),
        "event_count": len(events),
        "session_count": len(session_ids),
        "explicit_event_count": explicit_count,
        "concepts": concepts[:30],
        "positive_labels": [
            item["label"] for item in concepts if item["polarity"] == "positive"
        ][:24],
        "negative_labels": [
            item["label"] for item in concepts if item["polarity"] == "negative"
        ][:12],
    }
    canonical = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    version_id = stable_id("profile", canonical)
    summary["profile_version_id"] = version_id
    if persist:
        with transaction(service.settings.database_path) as db:
            db.execute(
                "INSERT OR IGNORE INTO preference_profile_versions VALUES (?, ?, ?, ?)",
                (version_id, canonical, int(active_for_ingestion), utc_now()),
            )
    return summary


def inspect_preferences(service: PaperTrail) -> dict[str, Any]:
    return {
        "profile": aggregate_profile(service, persist=False),
        "sources": preference_sources(service),
        "privacy": {
            "raw_conversations_stored": False,
            "assistant_turns_used": False,
            "sensitive_traits_inferred": False,
        },
    }


def prioritize_discoveries(
    service: PaperTrail,
    provider: IntelligenceProvider,
    *,
    group_id: str,
    budget: int,
    primary_only: bool = True,
    preference_share: float = 0.6,
    frontier_share: float = 0.2,
    exploration_share: float = 0.2,
    personalized: bool = True,
) -> dict[str, Any]:
    if budget < 0:
        raise ValueError("daily enrichment budget cannot be negative")
    if not math.isclose(preference_share + frontier_share + exploration_share, 1.0, abs_tol=0.001):
        raise ValueError("ingestion lane shares must sum to 1")
    profile = aggregate_profile(service, persist=True)
    category_filter = "AND d.primary_category = json_extract(g.query_json, '$.category')" if primary_only else ""
    with closing(connect(service.settings.database_path)) as db:
        records = [
            dict(row)
            for row in db.execute(
                f"""
                SELECT d.* FROM discovery_records d
                JOIN ingestion_group_runs gr ON gr.run_id = d.run_id
                JOIN ingestion_groups g ON g.id = gr.group_id
                WHERE gr.group_id = ? AND d.status IN ('discovered', 'failed')
                {category_filter}
                ORDER BY d.published_date DESC, d.source_id
                """,
                (group_id,),
            )
        ]
    if not records:
        return {
            "profile": profile,
            "selected_discovery_ids": [],
            "lane_counts": {},
            "candidate_count": 0,
            "budget": budget,
        }
    positive_labels = profile["positive_labels"] if personalized else []
    profile_tokens = set(_tokens(" ".join(positive_labels)))
    texts = [f"{item['title']}\n{item['abstract']}" for item in records]
    semantic: list[float | None] = [None] * len(records)
    if positive_labels:
        try:
            profile_vector = provider.embed(["; ".join(positive_labels)])[0]
            vectors: list[list[float]] = []
            for start in range(0, len(texts), 64):
                vectors.extend(provider.embed(texts[start : start + 64]))
            if len(vectors) == len(records):
                semantic = [max(0.0, _cosine(profile_vector, vector)) for vector in vectors]
        except (RuntimeError, ValueError, IndexError):
            semantic = [None] * len(records)
    document_tokens = [set(_tokens(text)) for text in texts]
    frequencies = Counter(token for tokens in document_tokens for token in tokens)
    scored: list[dict[str, Any]] = []
    for index, (record, tokens) in enumerate(zip(records, document_tokens, strict=True)):
        overlap = sorted(profile_tokens & tokens)
        lexical = len(overlap) / max(1.0, math.sqrt(len(profile_tokens) * len(tokens)))
        affinity = (
            0.8 * float(semantic[index]) + 0.2 * lexical
            if semantic[index] is not None
            else lexical
        )
        topical = [frequencies[token] for token in tokens if frequencies[token] > 1]
        trend = min(1.0, (sum(topical) / max(1, len(tokens))) / 4.0)
        rarity = sum(1.0 / frequencies[token] for token in tokens) / max(1, len(tokens))
        quality = min(1.0, len(record["abstract"]) / 1200.0)
        frontier = 0.45 * trend + 0.35 * rarity + 0.2 * quality
        jitter = int(hashlib.sha256(record["id"].encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        exploration = (1.0 - affinity) * (0.8 + 0.2 * jitter)
        scored.append(
            {
                "record": record,
                "affinity": round(affinity, 6),
                "frontier": round(frontier, 6),
                "exploration": round(exploration, 6),
                "matches": overlap[:6],
            }
        )
    selected: list[tuple[dict[str, Any], str]] = []
    chosen: set[str] = set()
    target = len(scored) if budget == 0 else min(budget, len(scored))
    active = bool(personalized and profile["active_for_ingestion"])
    preference_count = round(target * preference_share)
    frontier_count = round(target * frontier_share)
    exploration_count = target - preference_count - frontier_count
    if target >= 2 and exploration_count == 0:
        exploration_count = 1
        preference_count = max(0, preference_count - 1)
    if not active:
        frontier_count += preference_count
        preference_count = 0

    def take(items: list[dict[str, Any]], count: int, lane: str) -> None:
        if count <= 0:
            return
        for item in items:
            discovery_id = item["record"]["id"]
            if discovery_id in chosen:
                continue
            selected.append((item, lane))
            chosen.add(discovery_id)
            if sum(1 for _, selected_lane in selected if selected_lane == lane) >= count:
                break

    take(sorted(scored, key=lambda item: (-item["affinity"], item["record"]["id"])), preference_count, "preference")
    take(sorted(scored, key=lambda item: (-item["frontier"], item["record"]["id"])), frontier_count, "frontier")
    take(sorted(scored, key=lambda item: (-item["exploration"], item["record"]["id"])), exploration_count, "exploration")
    if len(selected) < target:
        take(
            sorted(
                scored,
                key=lambda item: (
                    -max(item["affinity"], item["frontier"], item["exploration"]),
                    item["record"]["id"],
                ),
            ),
            target - len(selected),
            "editorial",
        )
    selected_lanes = {item["record"]["id"]: lane for item, lane in selected}
    profile_id = profile["profile_version_id"]
    with transaction(service.settings.database_path) as db:
        for item in scored:
            discovery_id = item["record"]["id"]
            lane = selected_lanes.get(discovery_id, "deferred")
            matches = item["matches"]
            explanation = (
                f"Matches {', '.join(matches)}" if lane == "preference" and matches
                else "Trending or unusually novel within today's paper surplus"
                if lane == "frontier"
                else "Deliberately broadens the current research profile"
                if lane == "exploration"
                else "Retained as lightweight metadata for possible future promotion"
                if lane == "deferred"
                else "Selected by broad editorial ranking"
            )
            final_score = max(item["affinity"], item["frontier"], item["exploration"])
            db.execute(
                """
                INSERT OR REPLACE INTO paper_priority_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    discovery_id,
                    profile_id,
                    item["affinity"],
                    item["frontier"],
                    item["exploration"],
                    final_score,
                    lane,
                    explanation,
                    utc_now(),
                ),
            )
    lane_counts = Counter(lane for _, lane in selected)
    return {
        "profile": profile,
        "selected_discovery_ids": [item["record"]["id"] for item, _ in selected],
        "lane_counts": dict(lane_counts),
        "candidate_count": len(records),
        "budget": budget,
    }


def _tokens(text: str) -> list[str]:
    return [
        token.casefold()
        for token in TOKEN_RE.findall(text)
        if token.casefold() not in STOPWORDS
    ]


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clean_label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:120].strip(" .,:;-")


def _clean_context(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return redact_secrets(" ".join(value.split()))[:240]


def _validate_source(source: str) -> None:
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"Unknown preference source: {source}")
