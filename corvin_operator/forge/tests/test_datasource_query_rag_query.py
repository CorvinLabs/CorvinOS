"""Chat tools ``datasource_query`` and ``rag_query`` over the REAL MCP boundary.

A real ``forge.py mcp`` subprocess (stdio JSON-RPC, the transport Claude Code uses) is
started with the same ``PYTHONPATH`` the persona resolver gives it (``corvin_operator/forge``
only), a temp ``CORVIN_HOME`` and a temp secret vault. Nothing in the server is patched.

* ``datasource_query`` runs ONE read-only SELECT against a registered connection and returns
  rows; write statements, unknown connections and ``data_residency: local`` are refused; the
  audit record carries a hash of the SQL, never the SQL.
* ``rag_query`` queries the registered knowledge providers over HTTP (a real BM25 server on a
  loopback port) through the same service as the console's Query Tester.

PostgreSQL / MySQL over the same boundary run when ``CORVIN_TEST_PG`` /
``CORVIN_TEST_MYSQL`` carry ``host:port:database:user:password`` (opt-in, like the synthetic
dataset's SQL sidecar tests); they need the Member tier, which the child process cannot
pin, so they also need ``CORVIN_LICENSE_KEY``.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FORGE = REPO / "corvin_operator" / "forge"
SIDECAR_DIR = REPO / "tests" / "e2e" / "release_demo" / "sidecars"

CSV = (
    "track_uri,track_name,country,streams\n"
    "t1,Glasswind Cobbleway,DE,154711737\n"
    "t2,Wintergreen Beacon,DE,118855189\n"
    "t3,Hollowed Flintway,SE,34675356\n"
)


class Mcp:
    """Newline-delimited JSON-RPC over the stdio of a `forge.py mcp` subprocess."""

    def __init__(self, env: dict[str, str], root: Path) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(FORGE / "forge.py"), "--root", str(root), "mcp",
             "--permission-mode", "yes"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, cwd=str(FORGE), env=env,
        )
        self._id = 0
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "pytest", "version": "0"}})
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self._id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if msg.get("id") == self._id:
                return msg
        raise AssertionError(f"no reply to {method}: {self.proc.stderr.read() if self.proc.stderr else ''}")

    def call(self, name: str, arguments: dict) -> tuple[dict, bool]:
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        if "result" not in reply:
            raise AssertionError(f"tools/call {name} returned an error: {reply.get('error')}")
        result = reply["result"]
        text = result["content"][0]["text"]
        return json.loads(text), bool(result.get("isError"))

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def _env(tmp: Path) -> dict[str, str]:
    home = tmp / "corvin_home"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True, exist_ok=True)
    anchor = tmp / "anchor.key"
    anchor.write_bytes(os.urandom(32))
    anchor.chmod(0o600)
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP")}
    env.update({
        "HOME": str(tmp / "home"),
        "CORVIN_HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp / "xdg"),
        "CORVIN_SECRET_VAULT": str(tmp / "xdg" / "corvin-voice" / "secrets.json"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(anchor),
        # exactly what the persona resolver gives the forge MCP server
        "PYTHONPATH": str(FORGE),
        "CORVIN_TELEMETRY_OPTIN": "false",
    })
    (tmp / "home").mkdir(exist_ok=True)
    return env


def _register(home: Path, name: str, adapter: str, config: dict, *, residency: str = "any",
              classification: str = "INTERNAL", secrets: list[str] | None = None) -> None:
    d = home / "tenants" / "_default" / "datasource_connections"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({
        "dsi_version": "1", "name": name, "adapter": adapter, "config": config,
        "data_classification": classification, "data_residency": residency,
        "secrets": secrets or [], "read_only": True,
    }))


def _chain(home: Path) -> list[dict]:
    p = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


@pytest.fixture()
def mcp(tmp_path):
    env = _env(tmp_path)
    csv_path = tmp_path / "streams.csv"
    csv_path.write_text(CSV)
    home = Path(env["CORVIN_HOME"])
    _register(home, "streams-csv", "local_file", {"path": str(csv_path)})
    _register(home, "streams-local", "local_file", {"path": str(csv_path)}, residency="local")
    _register(home, "streams-pg", "postgresql", {"host": "127.0.0.1", "port": 1, "database": "x"},
              secrets=["PGUSER", "PGPASSWORD"])
    client = Mcp(env, tmp_path / "ws")
    yield client, home
    client.close()


def test_tools_are_advertised(mcp):
    client, _home = mcp
    names = {t["name"] for t in client.request("tools/list")["result"]["tools"]}
    if not {"datasource_query", "rag_query"} <= names:
        client.close()
        err = client.proc.stderr.read() if client.proc.stderr else ""
        raise AssertionError(f"tools missing: {sorted(names)}\nserver stderr:\n{err[-3000:]}")


def test_select_returns_the_rows(mcp):
    client, home = mcp
    out, is_error = client.call("datasource_query", {
        "name": "streams-csv",
        "sql": "SELECT track_name, SUM(streams) AS total FROM data WHERE country = 'DE' "
               "GROUP BY track_name ORDER BY total DESC LIMIT 1",
    })
    assert not is_error, out
    assert out["status"] == "ok"
    assert out["columns"] == ["track_name", "total"]
    assert out["rows"] == [["Glasswind Cobbleway", 154711737]]
    executed = [e for e in _chain(home) if e.get("event_type") == "datasource.query_executed"]
    assert executed, "every query is audited"
    details = executed[-1]["details"]
    assert details["name"] == "streams-csv" and details["row_count"] == 1
    assert len(details["sql_sha256"]) == 16
    assert "SELECT" not in json.dumps(executed[-1]), "the SQL text is never in the chain"


@pytest.mark.parametrize("sql,why", [
    ("DELETE FROM data", "only SELECT"),
    ("SELECT 1; DROP TABLE data", "exactly one statement"),
    ("SELECT * FROM data -- x", "comments"),
    ("WITH x AS (DELETE FROM data RETURNING 1) SELECT * FROM x", "DELETE"),
    ("SELECT * INTO newtable FROM data", "INTO"),
    ("SELECT pg_sleep(30)", "PG_SLEEP"),
])
def test_writes_and_tricks_are_refused(mcp, sql, why):
    client, home = mcp
    out, is_error = client.call("datasource_query", {"name": "streams-csv", "sql": sql})
    assert is_error and out["error"] == "query_refused", out
    assert why.lower() in out["message"].lower(), out
    assert any(e.get("event_type") == "datasource.query_refused" for e in _chain(home))


def test_string_literals_do_not_trip_the_keyword_guard(mcp):
    client, _home = mcp
    out, is_error = client.call("datasource_query", {
        "name": "streams-csv",
        "sql": "SELECT COUNT(*) AS n FROM data WHERE track_name <> 'update; delete -- x'",
    })
    assert not is_error, out
    assert out["rows"] == [[3]]


def test_unknown_connection_and_path_traversal(mcp):
    client, _home = mcp
    for name in ("nope", "../../etc/passwd", ""):
        out, is_error = client.call("datasource_query", {"name": name, "sql": "SELECT 1"})
        assert is_error and out["error"] == "unknown_connection", (name, out)


def test_local_residency_never_feeds_the_chat_engine(mcp):
    client, _home = mcp
    out, is_error = client.call("datasource_query", {"name": "streams-local", "sql": "SELECT 1 FROM data"})
    assert is_error and out["error"] == "residency_local", out


def test_adapter_outside_the_licence_is_refused(mcp):
    client, _home = mcp
    out, is_error = client.call("datasource_query", {"name": "streams-pg", "sql": "SELECT 1"})
    assert is_error and out["error"] == "license_limit", out  # free tier: local_file only


def test_row_cap_and_truncation(mcp):
    client, _home = mcp
    out, is_error = client.call("datasource_query",
                                {"name": "streams-csv", "sql": "SELECT * FROM data", "max_rows": 2})
    assert not is_error
    assert out["row_count"] == 2 and out["truncated"] is True


# --------------------------------------------------------------------------------------
# rag_query: the registered knowledge providers, over HTTP
# --------------------------------------------------------------------------------------

@pytest.fixture()
def bm25(tmp_path):
    import uvicorn

    data = tmp_path / "bm25data"
    (data / "tables").mkdir(parents=True)
    (data / "tables" / "songs.csv").write_text(
        "track_uri,track_name,artist_names,label,release_date\n"
        "syn:track:1,Glasswind Cobbleway,Moravine Zigurd,Parallaxo Records,2016-01-17\n"
        "syn:track:2,Hollowed Flintway,Fendris Bodkin,Malachyte Records,2021-11-01\n")
    (data / "tables" / "artists.csv").write_text("artist_uri,artist_name\nsyn:artist:1,Moravine Zigurd\n")
    (data / "tables" / "albums.csv").write_text(
        "album_uri,album_name,artist_names,label,release_date\n"
        "syn:album:1,Waystation Ledger,Moravine Zigurd,Parallaxo Records,2016-01-17\n")
    sys.path.insert(0, str(SIDECAR_DIR))
    try:
        import bm25_sidecar
    finally:
        sys.path.remove(str(SIDECAR_DIR))
    app = bm25_sidecar.make_app(bm25_sidecar.Index(bm25_sidecar.load_documents(data)))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/search"
    server.should_exit = True
    thread.join(timeout=5)


def _manifest(endpoint: str, classification: str) -> str:
    return (
        "apiVersion: rag.corvin.io/v1alpha1\nkind: RAGProvider\n"
        "metadata:\n  name: bm25-demo\n  version: '1.0'\n"
        "spec:\n  retrieval:\n    endpoint: " + endpoint + "\n    method: POST\n    timeout_ms: 5000\n"
        "    auth:\n      type: none\n"
        f"  dataClassification: {classification}\n  complianceZone: EU\n  capabilities: [keyword-search]\n"
    )


def test_rag_query_returns_the_grounded_passage(mcp, bm25):
    client, home = mcp
    reg = home / "tenants" / "_default" / "global" / "rag"
    (reg / "manifests").mkdir(parents=True)
    (reg / "manifests" / "bm25-demo.yaml").write_text(_manifest(bm25, "INTERNAL"))
    out, is_error = client.call("rag_query", {"query": "Glasswind Cobbleway label", "limit": 3})
    assert not is_error, out
    assert out["status"] == "ok" and out["providers_answered"] == ["bm25-demo"]
    assert out["passages"] and "Parallaxo Records" in out["passages"][0]["content"]
    assert out["classification"] == "INTERNAL"
    # counted and audited (metadata only)
    stats = json.loads((reg / "registry.json").read_text())["providers"][0]["query_stats"]
    assert stats["total_queries"] == 1
    events = {e.get("event_type"): e for e in _chain(home)}
    assert "rag.query_executed" in events and "rag.tool_query" in events
    assert "Glasswind" not in json.dumps(list(events.values()))


def test_rag_query_is_gated_by_the_classification_of_the_provider(mcp, bm25):
    client, home = mcp
    reg = home / "tenants" / "_default" / "global" / "rag"
    (reg / "manifests").mkdir(parents=True)
    (reg / "manifests" / "bm25-demo.yaml").write_text(_manifest(bm25, "CONFIDENTIAL"))
    out, is_error = client.call("rag_query", {"query": "Glasswind"})
    assert is_error and out["blocked_by"] == "L34_DATA_CLASSIFICATION" and out["passages"] == []


def test_rag_query_without_a_provider_says_so(mcp):
    client, _home = mcp
    out, is_error = client.call("rag_query", {"query": "anything"})
    assert not is_error and out["status"] == "no_providers_configured" and out["passages"] == []


@pytest.mark.skipif(not os.environ.get("CORVIN_TEST_PG"), reason="CORVIN_TEST_PG not set")
def test_postgres_over_the_mcp_boundary(tmp_path):
    host, port, db, user, password = os.environ["CORVIN_TEST_PG"].split(":", 4)
    env = _env(tmp_path)
    if os.environ.get("CORVIN_LICENSE_KEY"):
        env["CORVIN_LICENSE_KEY"] = os.environ["CORVIN_LICENSE_KEY"]
    home = Path(env["CORVIN_HOME"])
    vault = Path(env["CORVIN_SECRET_VAULT"])
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(json.dumps({"PGUSER": user, "PGPASSWORD": password}))
    vault.chmod(0o600)
    _register(home, "charts-pg", "postgresql", {"host": host, "port": int(port), "database": db},
              secrets=["PGUSER", "PGPASSWORD"])
    client = Mcp(env, tmp_path / "ws")
    try:
        out, is_error = client.call("datasource_query", {
            "name": "charts-pg",
            "sql": "SELECT track_uri, SUM(streams) AS v FROM charts_songs_daily WHERE country = 'DE' "
                   "GROUP BY track_uri ORDER BY v DESC, track_uri LIMIT 1",
        })
        assert not is_error, out
        assert out["rows"][0][0] == "syn:track:00001"
        # a write is refused by the session even if it slipped past the parser
        out, is_error = client.call("datasource_query", {"name": "charts-pg", "sql": "SELECT 1 INTO x"})
        assert is_error
    finally:
        client.close()
