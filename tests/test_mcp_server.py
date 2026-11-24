"""The MCP adapter: the same two views as `cadence analyze`, as read-only tools."""

from __future__ import annotations

import asyncio
import json
import os

import psycopg
import pytest
from psycopg.rows import dict_row

pytest.importorskip("mcp")

from cadence import analysis as an  # noqa: E402
from cadence.mcp_server import READ_ONLY, build_server  # noqa: E402
from conftest import TEST_DB, source  # noqa: E402
from test_analysis import _db_available  # noqa: E402

pytestmark = pytest.mark.skipif(not _db_available(), reason="no test database")


def call(server, name: str, args: dict) -> dict:
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result.content
    # Structured for clients that read it; the same JSON as text for those that do not.
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


@pytest.fixture
def server(tmp_path, monkeypatch):
    snap = source().save(tmp_path)

    async def fake_capture(repo, **_kw):
        if repo != "acme/contract":
            raise an.AnalysisError("github_access", f"{repo}: not found")
        return source(), str(snap)

    monkeypatch.setattr(an, "capture", fake_capture)
    return build_server(cache_dir=tmp_path, database_url=TEST_DB, token="unused")


def test_two_read_only_tools(server):
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"cadence_analyze", "cadence_finding"}
    for tool in tools.values():
        assert tool.annotations == READ_ONLY
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


def test_analyze_then_finding(seeded, server):
    summary = call(server, "cadence_analyze", {"repo": "acme/contract", "window_days": 30})
    assert summary["schema_version"] == "cadence.analysis/1"
    fid = summary["findings"][0]["id"]
    detail = call(server, "cadence_finding",
                  {"finding_id": fid, "analysis_id": summary["analysis_id"]})
    assert detail["finding"]["id"] == fid
    assert detail["analysis_id"] == summary["analysis_id"]
    assert detail["finding"]["evidence"][0]["payload"]["p50_seconds"] == 600.0


def test_the_cli_and_the_tool_return_the_same_view(seeded, server):
    via_tool = call(server, "cadence_analyze", {"repo": "acme/contract", "window_days": 30})
    with psycopg.connect(TEST_DB, row_factory=dict_row) as conn:
        direct = an.summary_view(an.analyze(conn, source(), window_days=30,
                                            source_path=via_tool["source"]["snapshot"]))
    for view in (via_tool, direct):
        view.pop("analyzed_at")
    assert via_tool == direct


def test_refusals_are_structured_not_exceptions(seeded, server):
    err = call(server, "cadence_analyze", {"repo": "acme/missing"})
    assert err["error"]["code"] == "github_access"
    err = call(server, "cadence_finding", {"finding_id": "f_x", "repo": "acme/contract"})
    assert err["error"]["code"] == "no_analysis"
    assert err["error"]["hint"] == "call cadence_analyze first"


def test_an_unknown_finding_in_a_known_analysis(seeded, server):
    summary = call(server, "cadence_analyze", {"repo": "acme/contract", "window_days": 30})
    err = call(server, "cadence_finding",
               {"finding_id": "f_nope", "analysis_id": summary["analysis_id"]})
    assert err["error"]["code"] == "finding_not_found"


def test_stdio_stdout_carries_only_the_protocol(tmp_path):
    """A log line on stdout would corrupt the session; the client must still list tools."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def go():
        params = StdioServerParameters(
            command="uv", args=["run", "cadence", "mcp"],
            env={**os.environ, "CADENCE_DATABASE_URL": TEST_DB})
        async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            return sorted(t.name for t in (await s.list_tools()).tools)

    assert asyncio.run(go()) == ["cadence_analyze", "cadence_finding"]
