import json

import pytest

from scripts.demo.record_codex import extract_evidence_ids, redact_transcript, validate_audit


def test_codex_transcript_keeps_evidence_and_removes_private_paths() -> None:
    raw = (
        "Found ev_abc123, `paper_deadbeef` in /Users/private/.papertrail "
        "using token sk-secretvalue"
    )
    clean = redact_transcript(raw, replacements={"/Users/private": "<demo-home>"})
    assert extract_evidence_ids(clean) == {"ev_abc123"}
    assert "/Users/private" not in clean
    assert "sk-secretvalue" not in clean
    assert "paper_deadbeef" not in clean


def test_codex_transcript_requires_evidence() -> None:
    assert extract_evidence_ids("unsupported answer") == set()


def test_audit_requires_mcp_and_rejects_database_bypass() -> None:
    calls = [
        {"item": {"type": "mcp_tool_call", "server": "papertrail", "result": {}}}
        for _ in range(5)
    ]
    calls.append({"item": {"type": "agent_message", "text": '{"full_text_papers": 40}'}})
    validate_audit("\n".join(json.dumps(item) for item in calls))
    calls.append({"item": {"type": "command_execution", "command": "sqlite3 papertrail.db"}})
    with pytest.raises(RuntimeError, match="bypassed MCP"):
        validate_audit("\n".join(json.dumps(item) for item in calls))
