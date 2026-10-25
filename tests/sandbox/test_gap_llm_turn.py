"""One REAL chat turn (real ``claude`` engine, operator login) on a sandbox console, then the changed
Memory and Files paths are exercised on what that turn produced, and the audit chain is verified.

Opt-in: runs only with ``SBX_REAL_LLM=1`` (it costs one short model turn). The sandbox HOME gets a copy of
``~/.claude/.credentials.json`` WITHOUT the refresh token (the sandbox CLI can use the access token but can
never rotate the operator's refresh token) and a ``claude`` symlink in the sandbox bin dir; both are
removed at teardown. The console runs on its own port with the sandbox CORVIN_HOME; nothing touches :8765.

Flow (all over HTTP / WebSocket against the real console):
  1. POST /chat/sessions, WS /chat/sessions/{sid}/stream {"type": "user", ...}: the model writes
     ``sbx-llm-proof.txt`` (marker inside) into its session workdir and answers.
  2. Files: the model-written file is downloadable through /files/download (positive control on the
     changed grading code); global/connector_vault.json is refused (403) and the refusal is audited.
  3. Memory: GET /memory is the tenant's empty state; the planted developer-memory marker is not served.
  4. The process tenant's chain carries the turn's records and verifies.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .conftest import MARKER_A, stop_checked  # noqa: E402
from .harness import Server  # noqa: E402
from .helpers_gap import action_records, chain_events, networked_server, verify_tenant_chain  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("SBX_REAL_LLM") != "1",
                                reason="real LLM turn is opt-in (SBX_REAL_LLM=1)")

PROOF = "SBX-LLM-PROOF-5b17"
VAULT_SECRET = "SBX-VAULT-SECRET-77aa"


@pytest.fixture(scope="module")
def llm_console(sandbox, planted, live_tripwire):
    real = shutil.which("claude")
    assert real, "claude CLI not found on the operator PATH"
    link = sandbox.bin_dir / "claude"
    link.symlink_to(os.path.realpath(real))
    creds_src = Path.home() / ".claude" / ".credentials.json"
    data = json.loads(creds_src.read_text())
    data.get("claudeAiOauth", {}).pop("refreshToken", None)  # never rotate the operator's token
    creds = sandbox.home / ".claude" / ".credentials.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text(json.dumps(data))
    creds.chmod(0o600)
    vault = sandbox.tenant_home("_default") / "global" / "connector_vault.json"
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(json.dumps({"openai": VAULT_SECRET}))
    # networked: the sandbox dead-proxy variables block the claude CLI from api.anthropic.com
    srv = networked_server(sandbox, "console-llm").start()
    try:
        yield srv
    finally:
        stop_checked(srv)
        creds.unlink(missing_ok=True)
        link.unlink(missing_ok=True)


def _turn(srv, sess, sid, text, timeout=300.0):
    from websockets.sync.client import connect

    url = srv.base.replace("http://", "ws://") + f"/chat/sessions/{sid}/stream"
    deltas, events = [], []
    with connect(url, additional_headers={"Cookie": f"{harness.COOKIE}={sess.sid}", "Origin": srv.base},
                 open_timeout=30, close_timeout=10) as ws:
        ready = json.loads(ws.recv(timeout=30))
        assert ready.get("type") == "ready", ready
        ws.send(json.dumps({"type": "user", "text": text}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = json.loads(ws.recv(timeout=max(1.0, deadline - time.monotonic())))
            events.append(msg.get("type"))
            if msg.get("type") == "delta":
                deltas.append(msg.get("text") or "")
            if msg.get("type") in ("done", "error"):
                if msg.get("type") == "error":
                    raise AssertionError(f"turn error: {msg}")
                break
    return "".join(deltas), events


def test_G_llm_real_turn_then_files_and_memory_paths(sandbox, llm_console, session_for):
    srv = llm_console
    sess = session_for("_default")
    before = len(chain_events(sandbox, "_default"))

    r = harness.fetch(srv, sess, "/chat/sessions", method="POST", json_body={"title": "sbx llm proof"})
    assert r.status_code in (200, 201), r.text[:300]
    sid = (r.json().get("session") or r.json()).get("sid") or r.json().get("id")
    assert sid, r.text[:300]

    answer, events = _turn(srv, sess, sid,
                           f"Create a file named sbx-llm-proof.txt in the current working directory whose "
                           f"entire content is the single line {PROOF}. Then reply with the word DONE.")
    print("EVENTS", events[:40])
    print("ANSWER", answer[-400:])
    assert "DONE" in answer.upper(), answer[-400:]

    wd = Path(harness.fetch(srv, sess, f"/chat/sessions/{sid}/workdir-path").json()["path"])
    proof = wd / "sbx-llm-proof.txt"
    assert proof.is_file(), f"the model did not write {proof}"
    rel = proof.resolve().relative_to(sandbox.tenant_home("_default").resolve())
    dl = harness.fetch(srv, sess, f"/files/download?path={rel}")
    print("FILES download", rel, dl.status_code, dl.text[:80])
    assert dl.status_code == 200 and PROOF in dl.text, (dl.status_code, dl.text[:200])

    vault = harness.fetch(srv, sess, "/files/download?path=global/connector_vault.json")
    print("FILES vault", vault.status_code)
    assert vault.status_code == 403 and VAULT_SECRET not in vault.text

    mem = harness.fetch(srv, sess, "/memory")
    print("MEMORY", mem.status_code, mem.text[:200])
    assert mem.status_code == 200 and mem.json()["available"] is False
    assert MARKER_A not in mem.text and ".claude" not in mem.text

    after = chain_events(sandbox, "_default")
    new_types = sorted({e.get("event_type") for e in after[before:]})
    print("CHAIN new event types", new_types)
    assert len(after) > before
    denied = action_records(sandbox, "_default", "files.download")
    assert denied and denied[-1]["event_type"] == "console.action_denied"
    chain = verify_tenant_chain(sandbox, "_default")
    print("CHAIN verify", chain)
    assert chain["ok"], chain
    # evidence for the report: event types, times and detail KEYS of this turn's records (no values)
    ev_dir = os.environ.get("SBX_EVIDENCE")
    if ev_dir:
        out = [{"event_type": e.get("event_type"), "ts": e.get("ts"), "severity": e.get("severity"),
                "detail_keys": sorted((e.get("details") or {}).keys())} for e in after[before:]]
        Path(ev_dir, "llm_turn_chain_records.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        Path(ev_dir, "llm_turn_verify.json").write_text(json.dumps(chain), encoding="utf-8")
