from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import unquote, urlparse

from .service import PaperTrail
from .intelligence import ResearchIntelligence
from .organization import latest_organization


TOOLS = [
    {
        "name": "search_catalog",
        "description": "Search arXiv titles and abstracts, including papers whose full text has not yet been acquired. Results explicitly report full-text status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "primary_only": {"type": "boolean"},
                "from_date": {"type": "string", "description": "Inclusive YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "Inclusive YYYY-MM-DD"},
                "snapshot_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_papers",
        "description": "Search exact evidence passages in the local PaperTrail corpus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "include_synthetic": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_figures",
        "description": "Search figure captions and retrieve immutable rendered-page visual evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "hybrid_search",
        "description": "Hybrid lexical and conceptual evidence retrieval with reciprocal-rank fusion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "include_synthetic": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_idea_novelty",
        "description": "Challenge an idea against nearest prior work, limitations, and counterevidence. Does not guarantee novelty.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "idea": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "nearest_papers": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["idea"],
        },
    },
    {
        "name": "discover_opportunities",
        "description": "Generate falsifiable research opportunities from limitations and evidence, then challenge each against prior work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "get_paper",
        "description": "Get paper metadata and its section list.",
        "inputSchema": {
            "type": "object",
            "properties": {"paper_id": {"type": "string"}, "snapshot_id": {"type": "string"}},
            "required": ["paper_id"],
        },
    },
    {
        "name": "find_related_papers",
        "description": "Find catalog papers related to an acquired paper using its title and abstract.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "get_scientific_records",
        "description": "Get evidence-bound contributions, methods, assumptions, results, limitations, and future work extracted from acquired papers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "record_types": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    },
    {
        "name": "get_figure",
        "description": "Retrieve figure metadata plus the immutable rendered page as image content for visual inspection.",
        "inputSchema": {
            "type": "object",
            "properties": {"figure_id": {"type": "string"}},
            "required": ["figure_id"],
        },
    },
    {
        "name": "get_evidence",
        "description": "Retrieve exact passages by stable evidence ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "evidence_ids": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["evidence_ids"],
        },
    },
    {
        "name": "get_snapshot_info",
        "description": "Inspect the corpus coverage and known gaps for a snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {"snapshot_id": {"type": "string"}},
        },
    },
    {
        "name": "get_corpus_status",
        "description": "Inspect current metadata, full-text, passage, embedding, scientific-record, visual-evidence, storage, and coverage status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_research_groups",
        "description": "Inspect the latest hybrid semantic/lexical organization of papers into related problem neighborhoods.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

for tool in TOOLS:
    tool["annotations"] = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def run(
    service: PaperTrail,
    intelligence: ResearchIntelligence | None = None,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    """Run a minimal MCP 2025-03-26 JSON-RPC server over newline-delimited stdio."""
    for line in input_stream:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            response = dispatch(service, request, intelligence)
        except Exception as error:
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32603, "message": str(error)},
            }
        output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
        output_stream.flush()


def dispatch(
    service: PaperTrail,
    request: dict[str, Any],
    intelligence: ResearchIntelligence | None = None,
) -> dict[str, Any]:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "papertrail-local", "version": "0.5.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params", {})
        result = call_tool(
            service, params.get("name", ""), params.get("arguments", {}), intelligence
        )
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def call_tool(
    service: PaperTrail,
    name: str,
    arguments: dict[str, Any],
    intelligence: ResearchIntelligence | None = None,
) -> dict[str, Any]:
    if name == "search_catalog":
        value = service.search_catalog(
            arguments["query"],
            category=arguments.get("category"),
            primary_only=bool(arguments.get("primary_only", False)),
            from_date=arguments.get("from_date"),
            to_date=arguments.get("to_date"),
            snapshot_id=arguments.get("snapshot_id"),
            limit=int(arguments.get("limit", 20)),
        )
    elif name == "search_papers":
        value = service.search(
            arguments["query"],
            snapshot_id=arguments.get("snapshot_id"),
            include_synthetic=bool(arguments.get("include_synthetic", False)),
            limit=int(arguments.get("limit", 20)),
        )
    elif name == "search_figures":
        value = service.search_figures(
            arguments["query"],
            snapshot_id=arguments.get("snapshot_id"),
            limit=int(arguments.get("limit", 20)),
        )
    elif name == "get_paper":
        value = service.get_paper(arguments["paper_id"], snapshot_id=arguments.get("snapshot_id"))
    elif name == "find_related_papers":
        value = service.related_papers(
            arguments["paper_id"],
            snapshot_id=arguments.get("snapshot_id"),
            limit=int(arguments.get("limit", 20)),
        )
    elif name == "get_scientific_records":
        value = service.get_scientific_records(
            paper_id=arguments.get("paper_id"),
            record_types=arguments.get("record_types"),
            snapshot_id=arguments.get("snapshot_id"),
            limit=int(arguments.get("limit", 100)),
        )
    elif name == "get_figure":
        value = service.get_figure(arguments["figure_id"])
    elif name == "get_evidence":
        value = service.get_evidence(arguments["evidence_ids"])
    elif name == "get_snapshot_info":
        value = service.snapshot_info(arguments.get("snapshot_id"))
    elif name == "get_corpus_status":
        value = service.corpus_status()
    elif name == "get_research_groups":
        value = latest_organization(service) or {"status": "not_ready", "groups": []}
    elif name in {"hybrid_search", "check_idea_novelty", "discover_opportunities"}:
        if intelligence is None:
            raise RuntimeError("Research intelligence provider is not configured")
        if name == "hybrid_search":
            value = intelligence.hybrid_search(
                arguments["query"],
                snapshot_id=arguments.get("snapshot_id"),
                include_synthetic=bool(arguments.get("include_synthetic", False)),
                limit=int(arguments.get("limit", 20)),
            )
        elif name == "check_idea_novelty":
            value = intelligence.novelty_check(
                arguments["idea"],
                snapshot_id=arguments.get("snapshot_id"),
                nearest_papers=int(arguments.get("nearest_papers", 12)),
                persist=False,
            )
        else:
            value = intelligence.discover_opportunities(
                arguments["topic"],
                snapshot_id=arguments.get("snapshot_id"),
                limit=int(arguments.get("limit", 3)),
                persist=False,
            )
    else:
        raise ValueError(f"Unknown tool: {name}")
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(value, indent=2)}
    ]
    if name == "get_figure":
        image_path = _local_artifact_path(service, value["artifact_uri"])
        content.append(
            {
                "type": "image",
                "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                "mimeType": "image/png",
            }
        )
    return {"content": content, "structuredContent": value}


def _local_artifact_path(service: PaperTrail, artifact_uri: str) -> Path:
    parsed = urlparse(artifact_uri)
    if parsed.scheme != "file":
        raise ValueError("Figure artifact is not a local file URI")
    path = Path(unquote(parsed.path)).resolve()
    artifact_root = service.settings.artifacts_path.resolve()
    if path != artifact_root and artifact_root not in path.parents:
        raise ValueError("Figure artifact is outside the PaperTrail artifact store")
    if not path.is_file():
        raise FileNotFoundError(f"Figure artifact is missing: {path}")
    return path
