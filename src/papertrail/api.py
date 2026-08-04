from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .service import PaperTrail
from .daily_digest import dashboard_data, figure_path, get_blog, paper_artifact_path
from .organization import latest_organization
from .intelligence import ResearchIntelligence


class PaperTrailHandler(BaseHTTPRequestHandler):
    service: PaperTrail
    intelligence: ResearchIntelligence | None = None
    server_version = "PaperTrail/0.5"

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(value, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        requested = self.headers.get("Range", "")
        if requested.startswith("bytes="):
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested[6:])
            if match and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2) or end)
                else:
                    length = int(match.group(2))
                    start = max(0, size - length)
                end = min(end, size - 1)
                if start > end:
                    self.send_error(416)
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        remaining = end - start + 1
        with path.open("rb") as stream:
            stream.seek(start)
            while remaining:
                chunk = stream.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in {"/", "/index.html"}:
                self._static("index.html")
                return
            if parsed.path.startswith("/assets/"):
                self._static(parsed.path.removeprefix("/assets/"))
                return
            if parsed.path == "/health":
                self._json(200, {"status": "ok", "service": "papertrail-local"})
                return
            if parsed.path == "/v1/dashboard":
                self._json(200, dashboard_data(self.service))
                return
            if parsed.path == "/v1/organization/latest":
                self._json(200, latest_organization(self.service) or {"status": "not_ready"})
                return
            if parsed.path.startswith("/v1/blogs/"):
                self._json(200, get_blog(self.service, parsed.path.rsplit("/", 1)[-1]))
                return
            if parsed.path.startswith("/v1/figures/") and parsed.path.endswith("/image"):
                figure_id = parsed.path.split("/")[3]
                path, content_type = figure_path(self.service, figure_id)
                self._file(path, content_type)
                return
            if parsed.path.startswith("/v1/papers/") and parsed.path.endswith("/artifact"):
                paper_id = parsed.path.split("/")[3]
                path, content_type = paper_artifact_path(self.service, paper_id)
                self._file(path, content_type)
                return
            if parsed.path.startswith("/v1/papers/"):
                paper_id = parsed.path.rsplit("/", 1)[-1]
                self._json(200, self.service.get_paper(paper_id, snapshot_id=_one(query, "snapshot_id")))
                return
            if parsed.path.startswith("/v1/snapshots/"):
                snapshot_id = parsed.path.rsplit("/", 1)[-1]
                self._json(200, self.service.snapshot_info(snapshot_id))
                return
            self._json(404, {"error": "not_found", "message": "Unknown route"})
        except (KeyError, ValueError) as error:
            self._json(404 if isinstance(error, KeyError) else 400, _error(error))
        except Exception as error:
            self._json(500, _error(error))

    def _static(self, name: str) -> None:
        if name not in {"index.html", "app.js", "styles.css"}:
            self._json(404, {"error": "not_found", "message": "Unknown asset"})
            return
        path = Path(__file__).with_name("web") / name
        if not path.is_file():
            self._json(404, {"error": "not_found", "message": "Dashboard asset missing"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in {".html", ".js", ".css"}:
            content_type += "; charset=utf-8"
        self._bytes(200, path.read_bytes(), content_type)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            if self.path == "/v1/search":
                self._json(
                    200,
                    self.service.search(
                        body.get("query", ""),
                        snapshot_id=body.get("snapshot_id"),
                        limit=int(body.get("limit", 20)),
                    ),
                )
                return
            if self.path == "/v1/evidence":
                self._json(200, self.service.get_evidence(body.get("evidence_ids", [])))
                return
            if self.path in {"/v1/hybrid-search", "/v1/ideas/novelty-check", "/v1/opportunities/discover"}:
                if self.intelligence is None:
                    raise RuntimeError("Research intelligence provider is not configured")
                if self.path == "/v1/hybrid-search":
                    value = self.intelligence.hybrid_search(
                        body.get("query", ""),
                        snapshot_id=body.get("snapshot_id"),
                        limit=int(body.get("limit", 20)),
                    )
                elif self.path == "/v1/ideas/novelty-check":
                    value = self.intelligence.novelty_check(
                        body.get("idea", ""),
                        snapshot_id=body.get("snapshot_id"),
                        nearest_papers=int(body.get("nearest_papers", 12)),
                    )
                else:
                    value = self.intelligence.discover_opportunities(
                        body.get("topic", ""),
                        snapshot_id=body.get("snapshot_id"),
                        limit=int(body.get("limit", 3)),
                    )
                self._json(200, value)
                return
            self._json(404, {"error": "not_found", "message": "Unknown route"})
        except (json.JSONDecodeError, ValueError) as error:
            self._json(400, _error(error))
        except KeyError as error:
            self._json(404, _error(error))
        except Exception as error:
            self._json(500, _error(error))

    def log_message(self, format: str, *args: Any) -> None:
        print(f"papertrail-api: {format % args}")


def _one(values: dict[str, list[str]], key: str) -> str | None:
    return values.get(key, [None])[0]


def _error(error: Exception) -> dict[str, str]:
    return {"error": error.__class__.__name__, "message": str(error)}


def serve(
    service: PaperTrail,
    host: str = "127.0.0.1",
    port: int = 8765,
    intelligence: ResearchIntelligence | None = None,
) -> None:
    handler = type(
        "ConfiguredPaperTrailHandler",
        (PaperTrailHandler,),
        {"service": service, "intelligence": intelligence},
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"PaperTrail API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
