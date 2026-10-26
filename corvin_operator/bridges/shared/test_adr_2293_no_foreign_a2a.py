"""ADR-2293: A2A in CorvinOS is the paired, signed protocol only - there is no Google A2A path.

Pins four things against the real code:

* the adapter and the sender are gone, and no production file mentions them again;
* ``build_server`` has no flag that could switch a foreign inbound protocol on;
* over a REAL HTTP server, ``POST /a2a`` and ``GET /.well-known/agent.json`` answer 404 (the two routes the adapter used),
  with ``/healthz`` as the positive control that the server answered at all;
* the receiver keeps exactly its own inbound routes (a signed ping is still routed to its handler, not 404).
"""
from __future__ import annotations

import inspect
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

RETIRED_FILES = (
    "corvin_operator/bridges/shared/a2a_google_adapter.py",
    "corvin_operator/bridges/shared/a2a_google_sender.py",
    "corvin_operator/bridges/shared/test_a2a_google_adapter.py",
    "corvin_operator/bridges/shared/test_a2a_google_e2e.py",
)
NEEDLES = ("a2a_google", "GoogleA2A", "google_a2a_enabled", "google_adapter", "agent_card_overrides")
SCAN_ROOTS = ("corvin_operator", "core", "scripts")


def test_the_retired_files_are_gone():
    present = [p for p in RETIRED_FILES if (REPO / p).exists()]
    assert not present, present


def test_no_production_file_mentions_the_retired_adapter():
    me = Path(__file__).resolve()
    hits: list[str] = []
    scanned = 0
    for root in SCAN_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if path.resolve() == me or "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if any(n in text for n in NEEDLES):
                hits.append(str(path.relative_to(REPO)))
    assert scanned > 500, f"positive control: the scan reached only {scanned} files"
    assert not hits, hits


def test_build_server_has_no_foreign_protocol_switch():
    import a2a_http_server as srv  # type: ignore
    params = set(inspect.signature(srv.build_server).parameters)
    assert not {"google_a2a_enabled", "agent_card_overrides"} & params, params
    assert not hasattr(srv, "GoogleA2AAdapter")
    assert not hasattr(srv._A2AHandler, "google_adapter")
    assert not hasattr(srv, "_sanitize_host"), "the Host-header filter only served the agent card"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import a2a_http_server as srv  # type: ignore
    httpd = srv.build_server(
        port=0, origins_dir=tmp_path / "origins", endpoints_dir=tmp_path / "endpoints",
        pending_dir=tmp_path / "pending", force_m1_only=True, instance_id="adr2293-test",
    )
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(url: str, *, data: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_positive_control_the_server_answers(server):
    code, body = _call(server + "/healthz")
    assert code == 200, (code, body)


def test_the_jsonrpc_route_and_the_agent_card_are_404(server):
    rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {}}).encode()
    code, _ = _call(server + "/a2a", data=rpc)
    assert code == 404
    code, _ = _call(server + "/.well-known/agent.json")
    assert code == 404


def test_the_paired_protocol_is_still_routed(server):
    """An unsigned ping is refused by the signature check, not by routing: anything but 404."""
    code, _ = _call(server + "/v1/a2a/ping", data=b"{}")
    assert code != 404, code
