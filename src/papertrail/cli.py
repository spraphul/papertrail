from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .agent_setup import connect_agent
from .api import serve
from .arxiv import import_paper
from .arxiv_batch import ArxivBatchConfig, ArxivBatchIngestor
from .config import settings
from .daily_digest import generate_daily_digest
from .mcp import run as run_mcp
from .organization import organize_snapshot
from .profile import configure_runtime, load_profile, save_profile
from .intelligence import EXTRACTION_TYPES, ResearchIntelligence
from .providers import provider_from_settings
from .scheduler import install_daily_schedule, install_dashboard_service
from .service import PaperTrail


def _add_daily_setup_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--category", default="cs.AI")
    command.add_argument("--daily-at", default="06:00", metavar="HH:MM")
    command.add_argument("--lookback-days", type=int, default=3)
    command.add_argument("--rolling-window-days", type=int, default=365)
    command.add_argument("--workers", type=int, default=3)
    command.add_argument(
        "--embedding-provider", choices=("ollama", "openai"), default="ollama"
    )
    command.add_argument(
        "--reasoning-provider", choices=("ollama", "openai"), default="ollama"
    )
    command.add_argument("--embedding-model")
    command.add_argument("--reasoning-model")
    command.add_argument(
        "--openai-base-url",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI API or compatible /v1 base URL",
    )
    command.add_argument(
        "--openai-api-key-file",
        type=Path,
        help="path to a private API-key file for MCP, dashboard, or scheduled runs",
    )
    command.add_argument("--client", choices=("codex", "claude"), action="append")
    command.add_argument(
        "--analyst",
        choices=("codex", "claude"),
        default="codex",
        help="CLI used for the daily trend analysis and deep dives (default: codex)",
    )
    command.add_argument("--analyst-model", help="optional model override for the analyst CLI")
    command.add_argument("--daily-blogs", type=int, choices=(1, 2, 3), default=3)
    command.add_argument(
        "--no-personalized-blogs",
        action="store_true",
        help="ignore favourites when selecting daily deep dives",
    )
    command.add_argument(
        "--max-clusters",
        type=int,
        default=48,
        help="maximum hybrid candidate neighborhoods before LLM refinement",
    )
    command.add_argument(
        "--cluster-threshold",
        type=float,
        default=0.58,
        help="hybrid candidate similarity threshold before LLM refinement",
    )
    command.add_argument("--dashboard-port", type=int, default=8765)
    command.add_argument("--scope", choices=("user", "project"), default="user")
    command.add_argument("--no-schedule", action="store_true")
    command.add_argument("--no-dashboard", action="store_true")
    command.add_argument("--force", action="store_true")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="papertrail", description="Local research evidence index")
    root.add_argument("--home", help="data directory (default: $PAPERTRAIL_HOME or .papertrail)")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="initialize a local PaperTrail library")
    commands.add_parser("doctor", help="check local capabilities")

    setup = commands.add_parser(
        "setup", help="configure model providers, daily sync, dashboard, and research clients"
    )
    _add_daily_setup_arguments(setup)

    commands.add_parser("daily", help="ingest the configured daily surplus and refresh the snapshot")
    organize = commands.add_parser(
        "organize", help="hybrid-cluster snapshot papers by the problems they target"
    )
    organize.add_argument("--snapshot", required=True)
    organize.add_argument(
        "--max-clusters",
        type=int,
        default=48,
        help="maximum hybrid candidate neighborhoods before LLM refinement",
    )
    organize.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.58,
        help="hybrid candidate similarity threshold before LLM refinement",
    )

    add_pdf = commands.add_parser("add-pdf", help="ingest a local PDF")
    _add_metadata_arguments(add_pdf)
    add_pdf.add_argument("path", type=Path)

    add_text = commands.add_parser("add-text", help="ingest a UTF-8 text file")
    _add_metadata_arguments(add_text)
    add_text.add_argument("path", type=Path)

    add_arxiv = commands.add_parser("add-arxiv", help="download and ingest one arXiv paper")
    add_arxiv.add_argument("arxiv_id")

    arxiv_batch = commands.add_parser("arxiv", help="resumable, rate-limited arXiv corpus ingestion")
    arxiv_commands = arxiv_batch.add_subparsers(dest="arxiv_command", required=True)
    arxiv_discover = arxiv_commands.add_parser("discover", help="cache matching paper metadata")
    arxiv_discover.add_argument("--category", default="cs.AI")
    arxiv_discover.add_argument("--from-date", required=True)
    arxiv_discover.add_argument("--to-date", required=True)
    arxiv_discover.add_argument("--page-size", type=int, default=250)
    arxiv_discover.add_argument("--limit", type=int)
    arxiv_discover.add_argument(
        "--monthly",
        action="store_true",
        help="partition the date range by month (required for result sets above 30,000)",
    )
    arxiv_acquire = arxiv_commands.add_parser("acquire", help="download and process discovered PDFs")
    arxiv_acquire.add_argument("target_id", help="run ID, or group ID with --group")
    arxiv_acquire.add_argument("--group", action="store_true")
    arxiv_acquire.add_argument("--limit", type=int)
    arxiv_acquire.add_argument(
        "--all",
        action="store_true",
        help="explicitly acquire every pending paper (otherwise --limit is required)",
    )
    arxiv_acquire.add_argument(
        "--primary-only",
        action="store_true",
        help="only acquire papers whose primary category matches the discovery category",
    )
    arxiv_acquire.add_argument(
        "--min-free-gb",
        type=float,
        default=5.0,
        help="stop cleanly before free disk falls below this value (default: 5)",
    )
    arxiv_acquire.add_argument("--retry-failed", action="store_true")
    arxiv_acquire.add_argument("--workers", type=int, default=3)
    arxiv_status = arxiv_commands.add_parser("status", help="inspect one ingestion run")
    arxiv_status.add_argument("target_id", nargs="?")
    arxiv_status.add_argument("--group", action="store_true")
    arxiv_search = arxiv_commands.add_parser("search", help="search discovered titles and abstracts")
    arxiv_search.add_argument("query")
    arxiv_search.add_argument("--group", dest="group_id")
    arxiv_search.add_argument("--primary-only", action="store_true")
    arxiv_search.add_argument("--limit", type=int, default=20)
    arxiv_ingest = arxiv_commands.add_parser(
        "ingest", help="discover, acquire, index figures, embed, and extract end to end"
    )
    arxiv_ingest.add_argument("--category", default="cs.AI")
    arxiv_ingest.add_argument("--from-date", required=True)
    arxiv_ingest.add_argument("--to-date", required=True)
    arxiv_ingest.add_argument("--page-size", type=int, default=500)
    arxiv_ingest.add_argument("--limit", type=int)
    arxiv_ingest.add_argument("--all", action="store_true")
    arxiv_ingest.add_argument("--primary-only", action="store_true")
    arxiv_ingest.add_argument("--min-free-gb", type=float, default=5.0)
    arxiv_ingest.add_argument("--retry-failed", action="store_true")
    arxiv_ingest.add_argument("--workers", type=int, default=3)
    arxiv_ingest.add_argument(
        "--enrichment",
        choices=("none", "embeddings", "full"),
        default="full",
        help="enrichment stage after acquisition (default: full)",
    )
    arxiv_ingest.add_argument(
        "--types",
        default=",".join(EXTRACTION_TYPES),
        help="comma-separated scientific record types",
    )

    search = commands.add_parser("search", help="search evidence passages")
    search.add_argument("query")
    search.add_argument("--snapshot")
    search.add_argument("--limit", type=int, default=10)

    figure_search = commands.add_parser(
        "search-figures", help="search figure captions and page-render evidence"
    )
    figure_search.add_argument("query")
    figure_search.add_argument("--limit", type=int, default=10)

    enrich = commands.add_parser("enrich", help="embed passages and extract evidence-bound scientific records")
    enrich.add_argument("--paper-id")
    enrich.add_argument("--from-date")
    enrich.add_argument("--to-date")
    enrich.add_argument("--limit", type=int)
    enrich.add_argument(
        "--embeddings-only",
        action="store_true",
        help="create missing passage embeddings without model extraction",
    )
    enrich.add_argument(
        "--types",
        default=",".join(EXTRACTION_TYPES),
        help="comma-separated extraction types",
    )

    hybrid = commands.add_parser("hybrid-search", help="search lexically and conceptually")
    hybrid.add_argument("query")
    hybrid.add_argument("--snapshot")
    hybrid.add_argument("--limit", type=int, default=10)

    novelty = commands.add_parser("novelty", help="challenge an idea against indexed prior work")
    novelty.add_argument("idea")
    novelty.add_argument("--snapshot")
    novelty.add_argument("--nearest-papers", type=int, default=12)

    discover = commands.add_parser("discover", help="propose and challenge falsifiable research opportunities")
    discover.add_argument("topic")
    discover.add_argument("--snapshot")
    discover.add_argument("--limit", type=int, default=3)

    get_paper = commands.add_parser("get-paper", help="show one paper")
    get_paper.add_argument("paper_id")
    get_paper.add_argument("--snapshot")

    get_evidence = commands.add_parser("get-evidence", help="show exact evidence passages")
    get_evidence.add_argument("evidence_ids", nargs="+")

    snapshot = commands.add_parser("snapshot", help="create or inspect immutable snapshots")
    snapshot_commands = snapshot.add_subparsers(dest="snapshot_command", required=True)
    create = snapshot_commands.add_parser("create")
    create.add_argument("snapshot_id")
    create.add_argument("--from-date")
    create.add_argument("--to-date")
    create.add_argument("--include-synthetic", action="store_true")
    create.add_argument("--category")
    create.add_argument("--primary-only", action="store_true")
    info = snapshot_commands.add_parser("info")
    info.add_argument("snapshot_id", nargs="?")

    api = commands.add_parser("serve", help="start the local read-only HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8765)
    commands.add_parser("mcp", help="start the read-only MCP server over stdio")
    connect = commands.add_parser(
        "connect", help="install the research skill and configure the local MCP server"
    )
    connect.add_argument("client", choices=("codex", "claude"))
    connect.add_argument("--scope", choices=("user", "project"), default="user")
    connect.add_argument("--force", action="store_true", help="replace an existing skill copy")
    return root


def _add_metadata_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--title", required=True)
    command.add_argument("--authors", default="", help="comma-separated authors")
    command.add_argument("--abstract", default="")
    command.add_argument("--published", help="publication date, YYYY-MM-DD")
    command.add_argument("--source-url")
    command.add_argument("--source-class", default="preprint")


def _metadata(arguments: argparse.Namespace) -> dict[str, Any]:
    return {
        "title": arguments.title,
        "authors": [item.strip() for item in arguments.authors.split(",") if item.strip()],
        "abstract": arguments.abstract,
        "published_date": arguments.published,
        "source_url": arguments.source_url,
        "source_class": arguments.source_class,
    }


def main(argv: list[str] | None = None) -> None:
    arguments = parser().parse_args(argv)
    resolved_home = Path(
        arguments.home or os.environ.get("PAPERTRAIL_HOME", ".papertrail")
    ).expanduser().resolve()
    configure_runtime(resolved_home)
    config = settings(arguments.home)
    service = PaperTrail(config)
    try:
        if arguments.command == "init":
            service.initialize()
            value: Any = {
                "status": "ready",
                "home": str(config.home),
                "database": str(config.database_path),
            }
        elif arguments.command == "setup":
            value = _setup_local(arguments, service)
        elif arguments.command == "daily":
            value = _run_daily(service)
        elif arguments.command == "organize":
            value = organize_snapshot(
                service,
                arguments.snapshot,
                max_clusters=arguments.max_clusters,
                similarity_threshold=arguments.similarity_threshold,
                label_provider=provider_from_settings(service.settings),
            )
        elif arguments.command == "doctor":
            value = doctor(service)
        elif arguments.command == "add-pdf":
            metadata = _metadata(arguments)
            if not metadata["source_url"]:
                metadata["source_url"] = arguments.path.resolve().as_uri()
            value = service.ingest_pdf(arguments.path, **metadata)
        elif arguments.command == "add-text":
            metadata = _metadata(arguments)
            if not metadata["source_url"]:
                metadata["source_url"] = arguments.path.resolve().as_uri()
            value = service.ingest_text(arguments.path.read_text(), **metadata)
        elif arguments.command == "add-arxiv":
            value = import_paper(service, arguments.arxiv_id)
        elif arguments.command == "arxiv":
            ingestor = ArxivBatchIngestor(service, progress=_progress)
            if arguments.arxiv_command == "discover":
                batch_config = ArxivBatchConfig(
                    category=arguments.category,
                    from_date=date.fromisoformat(arguments.from_date),
                    to_date=date.fromisoformat(arguments.to_date),
                    page_size=arguments.page_size,
                    limit=arguments.limit,
                )
                value = (
                    ingestor.discover_monthly(batch_config)
                    if arguments.monthly
                    else ingestor.discover(batch_config)
                )
            elif arguments.arxiv_command == "acquire":
                if arguments.limit is None and not arguments.all:
                    raise ValueError("arxiv acquire requires --limit N or an explicit --all")
                value = (
                    ingestor.acquire_group(
                        arguments.target_id,
                        limit=arguments.limit,
                        retry_failed=arguments.retry_failed,
                        primary_only=arguments.primary_only,
                        min_free_gb=arguments.min_free_gb,
                        workers=arguments.workers,
                    )
                    if arguments.group
                    else ingestor.acquire(
                        arguments.target_id,
                        limit=arguments.limit,
                        retry_failed=arguments.retry_failed,
                        primary_only=arguments.primary_only,
                        min_free_gb=arguments.min_free_gb,
                        workers=arguments.workers,
                    )
                )
            elif arguments.arxiv_command == "search":
                value = ingestor.search_metadata(
                    arguments.query,
                    group_id=arguments.group_id,
                    primary_only=arguments.primary_only,
                    limit=arguments.limit,
                )
            elif arguments.arxiv_command == "ingest":
                if arguments.limit is None and not arguments.all:
                    raise ValueError("arxiv ingest requires --limit N or an explicit --all")
                value = _run_arxiv_ingest(
                    service,
                    category=arguments.category,
                    from_date=date.fromisoformat(arguments.from_date),
                    to_date=date.fromisoformat(arguments.to_date),
                    page_size=arguments.page_size,
                    limit=arguments.limit,
                    retry_failed=arguments.retry_failed,
                    primary_only=arguments.primary_only,
                    min_free_gb=arguments.min_free_gb,
                    workers=arguments.workers,
                    enrichment=arguments.enrichment,
                    extraction_types=tuple(
                        item.strip() for item in arguments.types.split(",") if item.strip()
                    ),
                )
            elif arguments.target_id:
                value = (
                    ingestor.group_status(arguments.target_id)
                    if arguments.group
                    else ingestor.status(arguments.target_id)
                )
            else:
                value = ingestor.list_runs()
        elif arguments.command == "search":
            value = service.search(arguments.query, snapshot_id=arguments.snapshot, limit=arguments.limit)
        elif arguments.command == "search-figures":
            value = service.search_figures(arguments.query, limit=arguments.limit)
        elif arguments.command == "enrich":
            intelligence = ResearchIntelligence.from_settings(service, progress=_progress)
            extraction_types = (
                ()
                if arguments.embeddings_only
                else tuple(item.strip() for item in arguments.types.split(",") if item.strip())
            )
            value = intelligence.enrich(
                paper_id=arguments.paper_id,
                from_date=arguments.from_date,
                to_date=arguments.to_date,
                limit=arguments.limit,
                extraction_types=extraction_types,
            )
        elif arguments.command == "hybrid-search":
            intelligence = ResearchIntelligence.from_settings(service)
            value = intelligence.hybrid_search(
                arguments.query, snapshot_id=arguments.snapshot, limit=arguments.limit
            )
        elif arguments.command == "novelty":
            intelligence = ResearchIntelligence.from_settings(service)
            value = intelligence.novelty_check(
                arguments.idea,
                snapshot_id=arguments.snapshot,
                nearest_papers=arguments.nearest_papers,
            )
        elif arguments.command == "discover":
            intelligence = ResearchIntelligence.from_settings(service)
            value = intelligence.discover_opportunities(
                arguments.topic, snapshot_id=arguments.snapshot, limit=arguments.limit
            )
        elif arguments.command == "get-paper":
            value = service.get_paper(arguments.paper_id, snapshot_id=arguments.snapshot)
        elif arguments.command == "get-evidence":
            value = service.get_evidence(arguments.evidence_ids)
        elif arguments.command == "snapshot" and arguments.snapshot_command == "create":
            value = service.create_snapshot(
                arguments.snapshot_id,
                from_date=arguments.from_date,
                to_date=arguments.to_date,
                include_synthetic=arguments.include_synthetic,
                category=arguments.category,
                primary_only=arguments.primary_only,
            )
        elif arguments.command == "snapshot" and arguments.snapshot_command == "info":
            value = service.snapshot_info(arguments.snapshot_id)
        elif arguments.command == "serve":
            service.initialize()
            serve(
                service,
                arguments.host,
                arguments.port,
                intelligence=ResearchIntelligence.from_settings(service),
            )
            return
        elif arguments.command == "mcp":
            service.initialize()
            run_mcp(service, ResearchIntelligence.from_settings(service))
            return
        elif arguments.command == "connect":
            value = connect_agent(
                arguments.client,
                arguments.scope,
                config,
                force=arguments.force,
            )
        else:
            raise ValueError("Unknown command")
        print(json.dumps(value, indent=2))
    except (ValueError, KeyError, RuntimeError, OSError, sqlite3.Error) as error:
        print(json.dumps({"error": error.__class__.__name__, "message": str(error)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from error


def _setup_local(arguments: argparse.Namespace, service: PaperTrail) -> dict[str, Any]:
    analyst_executable = shutil.which(arguments.analyst)
    if not analyst_executable:
        raise RuntimeError(
            f"{arguments.analyst.title()} CLI is required for daily deep dives; "
            f"install and authenticate it, or choose --analyst {'claude' if arguments.analyst == 'codex' else 'codex'}"
        )
    try:
        hour_text, minute_text = arguments.daily_at.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as error:
        raise ValueError("--daily-at must use HH:MM in 24-hour time") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("--daily-at must use HH:MM in 24-hour time")
    if arguments.lookback_days < 1 or arguments.rolling_window_days < 1:
        raise ValueError("lookback and rolling-window days must be positive")
    if not 1 <= arguments.workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if not 2 <= arguments.max_clusters <= 100:
        raise ValueError("max-clusters must be between 2 and 100")
    if not 0.0 < arguments.cluster_threshold < 1.0:
        raise ValueError("cluster-threshold must be between 0 and 1")

    embedding_model = arguments.embedding_model or (
        "text-embedding-3-small"
        if arguments.embedding_provider == "openai"
        else "embeddinggemma"
    )
    reasoning_model = arguments.reasoning_model or (
        "gpt-5.6" if arguments.reasoning_provider == "openai" else "qwen2.5:7b"
    )
    uses_openai = "openai" in {
        arguments.embedding_provider,
        arguments.reasoning_provider,
    }
    openai_key_file = None
    if arguments.openai_api_key_file:
        key_path = arguments.openai_api_key_file.expanduser().resolve()
        if not key_path.is_file() or not key_path.read_text().strip():
            raise ValueError("--openai-api-key-file must point to a non-empty file")
        openai_key_file = str(key_path)
    official_openai = arguments.openai_base_url.rstrip("/") == "https://api.openai.com/v1"
    if uses_openai and official_openai and not (os.environ.get("OPENAI_API_KEY") or openai_key_file):
        raise RuntimeError(
            "OpenAI requires OPENAI_API_KEY or --openai-api-key-file; PaperTrail never "
            "stores the key itself in profile.json"
        )
    if uses_openai and not arguments.no_schedule and not openai_key_file:
        raise RuntimeError(
            "Scheduled OpenAI runs require --openai-api-key-file because launchd does not "
            "load shell environment variables; use --no-schedule for environment-only use"
        )

    profile = {
        "profile": "local",
        "providers": {
            "embedding_provider": arguments.embedding_provider,
            "reasoning_provider": arguments.reasoning_provider,
            "embedding_model": embedding_model,
            "reasoning_model": reasoning_model,
            "openai_base_url": arguments.openai_base_url.rstrip("/"),
            "openai_api_key_file": openai_key_file,
        },
        "daily": {
            "category": arguments.category,
            "hour": hour,
            "minute": minute,
            "lookback_days": arguments.lookback_days,
            "rolling_window_days": arguments.rolling_window_days,
            "workers": arguments.workers,
            "enrichment": "full",
            "primary_only": True,
            "analyst": arguments.analyst,
            "analyst_executable": analyst_executable,
            "analyst_model": arguments.analyst_model,
            "blog_count": arguments.daily_blogs,
            "personalized_blogs": not arguments.no_personalized_blogs,
            "max_clusters": arguments.max_clusters,
            "cluster_similarity_threshold": arguments.cluster_threshold,
        },
    }
    profile_path = save_profile(service.settings.home, profile)
    configure_runtime(service.settings.home)
    configured_service = PaperTrail(settings(service.settings.home))
    configured_service.initialize()

    clients = arguments.client or ["codex", "claude"]
    connections = [
        connect_agent(
            client,
            arguments.scope,
            configured_service.settings,
            force=arguments.force,
        )
        for client in clients
    ]
    schedule = None
    if not arguments.no_schedule:
        schedule = install_daily_schedule(
            configured_service.settings.home, hour=hour, minute=minute
        )
    dashboard = None
    if not arguments.no_dashboard:
        dashboard = install_dashboard_service(
            configured_service.settings.home, port=arguments.dashboard_port
        )
    return {
        "status": "ready",
        "profile": "local",
        "home": str(configured_service.settings.home),
        "profile_file": str(profile_path),
        "providers": {
            "embedding": arguments.embedding_provider,
            "reasoning": arguments.reasoning_provider,
            "embedding_model": embedding_model,
            "reasoning_model": reasoning_model,
            "openai_base_url": arguments.openai_base_url.rstrip("/") if uses_openai else None,
            "credential": "key-file" if openai_key_file else "environment",
        },
        "daily": profile["daily"],
        "schedule": schedule,
        "dashboard": dashboard,
        "connections": connections,
        "next_step": "Restart Codex or Claude, then invoke $papertrail-deep-research.",
    }


def _run_daily(service: PaperTrail) -> dict[str, Any]:
    profile = load_profile(service.settings.home)
    daily = profile.get("daily")
    if not isinstance(daily, dict):
        raise RuntimeError("No daily profile is configured; run papertrail setup")
    today = date.today()
    to_date = today - timedelta(days=1)
    from_date = to_date - timedelta(days=int(daily.get("lookback_days", 3)) - 1)
    ingestion = _run_arxiv_ingest(
        service,
        category=str(daily.get("category", "cs.AI")),
        from_date=from_date,
        to_date=to_date,
        page_size=500,
        limit=None,
        retry_failed=True,
        primary_only=bool(daily.get("primary_only", True)),
        min_free_gb=5.0,
        workers=int(daily.get("workers", 3)),
        enrichment=str(daily.get("enrichment", "full")),
        extraction_types=EXTRACTION_TYPES,
    )
    if ingestion["status"] != "complete":
        return {
            "status": "partial",
            "window": {"from_date": from_date.isoformat(), "to_date": to_date.isoformat()},
            "ingestion": ingestion,
            "snapshot": None,
        }
    rolling_from = to_date - timedelta(days=int(daily.get("rolling_window_days", 365)) - 1)
    snapshot = service.create_snapshot(
        f"{str(daily.get('category', 'cs.AI')).replace('.', '-')}-{today.isoformat()}",
        from_date=rolling_from.isoformat(),
        to_date=to_date.isoformat(),
        category=str(daily.get("category", "cs.AI")),
        primary_only=bool(daily.get("primary_only", True)),
    )
    analyst = str(daily.get("analyst", "codex"))
    candidate_paper_ids = _daily_candidate_paper_ids(
        service,
        ingestion=ingestion,
        snapshot_id=snapshot["snapshot_id"],
        agent_client=analyst,
    )
    ingestion["acquisition"]["daily_candidate_paper_ids"] = candidate_paper_ids
    try:
        organization = organize_snapshot(
            service,
            snapshot["snapshot_id"],
            new_paper_ids=candidate_paper_ids,
            max_clusters=int(daily.get("max_clusters", 48)),
            similarity_threshold=float(daily.get("cluster_similarity_threshold", 0.58)),
            label_provider=provider_from_settings(service.settings),
        )
    except (ValueError, KeyError, RuntimeError, OSError, sqlite3.Error) as error:
        organization = {
            "status": "failed",
            "error": error.__class__.__name__,
            "message": str(error),
            "groups": [],
        }
    try:
        analysis = generate_daily_digest(
            service,
            snapshot_id=snapshot["snapshot_id"],
            paper_ids=candidate_paper_ids,
            agent_client=analyst,
            agent_model=daily.get("analyst_model"),
            agent_executable=daily.get("analyst_executable"),
            max_blogs=int(daily.get("blog_count", 3)),
            organization=organization,
            personalize=bool(daily.get("personalized_blogs", True)),
        )
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        analysis = {
            "status": "failed",
            "error": error.__class__.__name__,
            "message": str(error),
        }
    return {
        "status": (
            "complete"
            if organization["status"] == "complete"
            and analysis["status"] in {"complete", "already_complete", "no_new_papers"}
            else "partial"
        ),
        "window": {"from_date": from_date.isoformat(), "to_date": to_date.isoformat()},
        "ingestion": ingestion,
        "snapshot": snapshot,
        "organization": organization,
        "analysis": analysis,
    }


def _daily_candidate_paper_ids(
    service: PaperTrail,
    *,
    ingestion: dict[str, Any],
    snapshot_id: str,
    agent_client: str,
) -> list[str]:
    current = list(
        dict.fromkeys(ingestion["acquisition"].get("new_paper_ids", []))
    )
    if current:
        return current
    with sqlite3.connect(service.settings.database_path) as db:
        db.row_factory = sqlite3.Row
        digest = db.execute(
            """
            SELECT status, candidate_paper_ids_json
            FROM daily_digest_runs
            WHERE snapshot_id = ? AND agent_client = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (snapshot_id, agent_client),
        ).fetchone()
        if digest and digest["status"] == "complete":
            return []
        if digest and digest["status"] in {"running", "failed"}:
            return list(dict.fromkeys(json.loads(digest["candidate_paper_ids_json"])))
        rows = db.execute(
            """
            SELECT DISTINCT d.paper_id
            FROM ingestion_groups g
            JOIN ingestion_group_runs gr ON gr.group_id = g.id
            JOIN discovery_records d ON d.run_id = gr.run_id
            JOIN papers p ON p.id = d.paper_id
            WHERE g.id = ? AND d.status = 'acquired' AND d.paper_id IS NOT NULL
              AND p.created_at >= g.created_at
            ORDER BY d.published_date, d.source_id
            """,
            (ingestion["group_id"],),
        ).fetchall()
    return [row["paper_id"] for row in rows]


def _run_arxiv_ingest(
    service: PaperTrail,
    *,
    category: str,
    from_date: date,
    to_date: date,
    page_size: int,
    limit: int | None,
    retry_failed: bool,
    primary_only: bool,
    min_free_gb: float,
    workers: int,
    enrichment: str,
    extraction_types: tuple[str, ...],
) -> dict[str, Any]:
    ingestor = ArxivBatchIngestor(service, progress=_progress)
    group = ingestor.discover_monthly(
        ArxivBatchConfig(
            category=category,
            from_date=from_date,
            to_date=to_date,
            page_size=page_size,
        )
    )
    acquisition = ingestor.acquire_group(
        group["id"],
        limit=limit,
        retry_failed=retry_failed,
        primary_only=primary_only,
        min_free_gb=min_free_gb,
        workers=workers,
    )
    acquired_paper_ids = acquisition.pop("acquired_paper_ids", [])
    new_paper_ids = list(dict.fromkeys(acquisition.pop("newly_acquired_paper_ids", [])))
    with sqlite3.connect(service.settings.database_path) as db:
        paper_ids = [
            row[0]
            for row in db.execute(
                """
                SELECT DISTINCT d.paper_id
                FROM ingestion_group_runs gr
                JOIN discovery_records d ON d.run_id = gr.run_id
                WHERE gr.group_id = ? AND d.status = 'acquired'
                  AND d.paper_id IS NOT NULL
                  AND (? = 0 OR d.primary_category = ?)
                ORDER BY d.published_date, d.source_id
                """,
                (group["id"], int(primary_only), category),
            )
        ]
    enrichment_results: list[dict[str, Any]] = []
    enrichment_error = None
    if enrichment != "none":
        intelligence = ResearchIntelligence.from_settings(service, progress=_progress)
        selected_types = () if enrichment == "embeddings" else extraction_types
        for index, paper_id in enumerate(paper_ids, start=1):
            try:
                _progress(
                    {
                        "stage": "enrichment",
                        "paper": index,
                        "total": len(paper_ids),
                        "paper_id": paper_id,
                    }
                )
                enrichment_results.append(
                    intelligence.enrich(
                        paper_id=paper_id,
                        extraction_types=selected_types,
                        skip_completed=True,
                    )
                )
            except (ValueError, KeyError, RuntimeError, OSError, sqlite3.Error) as error:
                enrichment_error = {
                    "paper_id": paper_id,
                    "error": error.__class__.__name__,
                    "message": str(error),
                }
                break
    with sqlite3.connect(service.settings.database_path) as db:
        visual_count = db.execute(
            f"SELECT count(*) FROM visual_evidence WHERE paper_id IN ({','.join('?' for _ in new_paper_ids)})"
            if new_paper_ids
            else "SELECT 0",
            new_paper_ids,
        ).fetchone()[0]
    return {
        "status": "complete" if enrichment_error is None else "partial",
        "group_id": group["id"],
        "discovery": {
            "total_expected": group["total_expected"],
            "discovered_count": group["discovered_count"],
        },
        "acquisition": {
            "requested_limit": limit,
            "processed_this_run": len(acquired_paper_ids),
            "new_papers_this_run": len(new_paper_ids),
            "new_paper_ids": new_paper_ids,
            "acquired_total": acquisition["acquired_count"],
            "pending_total": acquisition["pending_count"],
            "failed_total": acquisition["failed_count"],
            "visual_evidence_this_run": visual_count,
        },
        "enrichment": {
            "completed_this_run": len(enrichment_results),
            "eligible_papers": len(paper_ids),
            "paper_ids": paper_ids[: len(enrichment_results)],
            "embedded_passages": sum(item["embedded_passages"] for item in enrichment_results),
            "accepted_records": sum(item["accepted_records"] for item in enrichment_results),
            "rejected_records": sum(item["rejected_records"] for item in enrichment_results),
            "error": enrichment_error,
            "mode": enrichment,
            "embedding_provider": service.settings.embedding_provider,
            "reasoning_provider": service.settings.reasoning_provider,
            "embedding_model": service.settings.embedding_model,
            "reasoning_model": service.settings.reasoning_model,
            "types": list(extraction_types),
        },
    }


def doctor(service: PaperTrail) -> dict[str, Any]:
    checks: dict[str, Any] = {"python": sys.version.split()[0], "sqlite_fts5": False}
    service.initialize()
    with sqlite3.connect(":memory:") as db:
        try:
            db.execute("CREATE VIRTUAL TABLE check_fts USING fts5(body)")
            checks["sqlite_fts5"] = True
        except sqlite3.Error:
            pass
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.enable_load_extension(False)
            vec_version = db.execute("SELECT vec_version()").fetchone()[0]
            checks["vector_accelerator"] = f"sqlite-vec:{vec_version}"
        except (ImportError, AttributeError, OSError, sqlite3.Error):
            checks["vector_accelerator"] = "python-cosine:fallback"
    try:
        import pypdf  # type: ignore[import-not-found]  # noqa: F401

        checks["pdf_extractor"] = "pypdf"
    except ImportError:
        checks["pdf_extractor"] = "ghostscript" if shutil.which("gs") else None
    checks["database"] = str(service.settings.database_path)
    provider = provider_from_settings(service.settings)
    checks["intelligence_providers"] = {
        "embedding": service.settings.embedding_provider,
        "reasoning": service.settings.reasoning_provider,
    }
    provider_health = provider.health()
    checks["provider_health"] = provider_health
    if "embedding" in provider_health and "reasoning" in provider_health:
        checks["intelligence_ready"] = all(
            bool(item.get("available", item.get("configured", False)))
            for item in (provider_health["embedding"], provider_health["reasoning"])
        )
    else:
        checks["intelligence_ready"] = bool(
            provider_health.get("available", provider_health.get("configured", False))
        )
    checks["configured_models"] = {
        "embedding": service.settings.embedding_model,
        "reasoning": service.settings.reasoning_model,
    }
    checks["status"] = (
        "ok"
        if checks["sqlite_fts5"] and checks["intelligence_ready"]
        else "degraded"
        if checks["sqlite_fts5"]
        else "failed"
    )
    if not checks["pdf_extractor"]:
        checks["warning"] = "PDF import unavailable; text import still works"
    return checks


def _progress(event: dict[str, Any]) -> None:
    print(json.dumps(event, separators=(",", ":")), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
