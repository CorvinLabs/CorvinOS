"""E2E — Agent Hub live feed: A2A messages with media, over the real wire.

Drives the RUNNING console (corvin-webui on 127.0.0.1:8765) through its HTTP
boundary only — nothing is imported and called directly:

1. ``/v1/console/a2a/feed*`` is mounted and session-guarded (401, not 404).
2. The peer directory lists the configured A2A connection.
3. ``POST /a2a/feed/send`` with text + a PNG goes out as a REAL signed task
   envelope to a REAL paired peer; the outbound message and the attachment
   appear in the feed, then the peer's response (any terminal status).
4. The stored attachment is served byte-identical with ``nosniff``; a
   script-capable declared MIME is downgraded to an octet-stream download.
5. The exchange is linked to the audit chain by ``task_id``
   (``A2A.envelope_sent`` / ``A2A.response_received``), which carries no
   message content.

Step 3 sends to a live peer (it may spawn a worker there), so the send tests
are opt-in: ``CORVIN_E2E_A2A_LIVE=1``. Steps 1, 2 and the CSRF check always run.
"""
from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path

import pytest

HOST, PORT = "127.0.0.1", 8765
CONSOLE = f"http://{HOST}:{PORT}/v1/console"
LIVE = os.environ.get("CORVIN_E2E_A2A_LIVE") == "1"
REPO = Path(__file__).resolve().parents[2]


def _console_up() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _console_up(), reason=f"console not listening on {HOST}:{PORT}")


def _png(w: int = 24, h: int = 24) -> bytes:
    """A real, decodable PNG (amber square) — no fixture file needed."""
    raw = b"".join(b"\x00" + bytes((201, 155, 73)) * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture(scope="module")
def session():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with op.open(f"{CONSOLE}/auth/local-login", timeout=15) as resp:
        assert resp.status == 200
    with op.open(f"{CONSOLE}/auth/whoami", timeout=15) as resp:
        who = json.loads(resp.read())
    csrf = who.get("csrf_token") or ""
    assert csrf, "whoami returned no csrf_token"
    return op, csrf


def _json(op, path: str, *, method: str = "GET", body: dict | None = None, csrf: str | None = None):
    req = urllib.request.Request(f"{CONSOLE}{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    with op.open(req, data=data, timeout=60) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


# ── always-on ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/a2a/feed", "/a2a/feed/blob/" + "0" * 64])
def test_routes_are_mounted_and_session_guarded(path):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{CONSOLE}{path}", timeout=15)
    assert exc.value.code == 401, f"{path} answered {exc.value.code}; 404 = not mounted"


def test_feed_lists_configured_peers(session):
    op, _ = session
    status, data = _json(op, "/a2a/feed?limit=5")
    assert status == 200
    assert {"messages", "peers", "retention_days"} <= data.keys()
    endpoints = list((REPO / "corvin_operator" / "cowork" / "remote_endpoints").glob("*.json"))
    ids = {p["peer_id"] for p in data["peers"]}
    for f in endpoints:
        assert f.stem in ids, f"configured endpoint {f.stem} missing from the feed peer list"


def test_mutations_require_csrf(session):
    op, _ = session
    for method, path in (("DELETE", "/a2a/feed"), ("POST", "/a2a/feed/send")):
        req = urllib.request.Request(f"{CONSOLE}{path}", method=method, data=b"{}")
        req.add_header("Content-Type", "application/json")
        with pytest.raises(urllib.error.HTTPError) as exc:
            op.open(req, timeout=15)
        assert exc.value.code == 403, f"{method} {path} without CSRF answered {exc.value.code}"


def test_blob_rejects_malformed_digest(session):
    op, _ = session
    with pytest.raises(urllib.error.HTTPError) as exc:
        op.open(f"{CONSOLE}/a2a/feed/blob/..%2F..%2Fetc%2Fpasswd", timeout=15)
    assert exc.value.code == 404


# ── live peer (opt-in) ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def live_exchange(session):
    if not LIVE:
        pytest.skip("set CORVIN_E2E_A2A_LIVE=1 to send to the real paired peer")
    op, csrf = session
    _, data = _json(op, "/a2a/feed?limit=1")
    peer = next((p for p in data["peers"] if p["can_send"] and p["state"] == "ACTIVE"), None)
    if peer is None:
        pytest.skip("no ACTIVE A2A peer with an enabled endpoint")

    marker = f"agent-hub-e2e-{uuid.uuid4().hex[:10]}"
    png = _png()
    since = time.time() - 1
    status, resp = _json(op, "/a2a/feed/send", method="POST", csrf=csrf, body={
        "peer_id": peer["peer_id"],
        "text": f"[{marker}] Corvin Agent Hub E2E: please reply with one short sentence describing the attached image.",
        "attachments": [{"name": "e2e-square.png", "mime": "image/png",
                         "content_b64": base64.b64encode(png).decode()}],
        "timeout_s": 120,
    })
    assert status == 202 and resp["accepted"] is True

    task = response = None
    deadline = time.time() + 180
    while time.time() < deadline and response is None:
        _, feed = _json(op, f"/a2a/feed?since={since}&limit=200")
        msgs = feed["messages"]
        task = task or next((m for m in msgs if m["kind"] == "task" and marker in m["text"]), None)
        if task:
            response = next((m for m in msgs if m["kind"] == "response" and m["task_id"] == task["task_id"]), None)
        if response is None:
            time.sleep(2)
    return {"op": op, "peer": peer, "png": png, "task": task, "response": response}


def test_outbound_message_with_image_is_in_feed(live_exchange):
    task, png = live_exchange["task"], live_exchange["png"]
    assert task is not None, "outbound task never appeared in the feed"
    assert task["direction"] == "out" and task["peer_id"] == live_exchange["peer"]["peer_id"]
    [att] = task["attachments"]
    assert att["mime"] == "image/png" and att["size"] == len(png)
    assert att["sha256"] == hashlib.sha256(png).hexdigest()


def test_peer_response_arrives(live_exchange):
    resp = live_exchange["response"]
    assert resp is not None, "no response recorded for the task within 180 s"
    assert resp["direction"] == "in"
    assert resp["status"] in {"ok", "filtered", "rejected", "timeout", "error"}
    print(f"\npeer response: status={resp['status']} duration_ms={resp['duration_ms']} "
          f"data_keys={sorted(resp['data'])} attachments={len(resp['attachments'])} error={resp['error']}")


def test_attachment_is_served_safely(live_exchange):
    op, png = live_exchange["op"], live_exchange["png"]
    att = live_exchange["task"]["attachments"][0]
    with op.open(f"{CONSOLE}/a2a/feed/blob/{att['sha256']}?name=e2e-square.png&mime=image/png", timeout=15) as r:
        assert r.read() == png
        assert r.headers["Content-Type"].startswith("image/png")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in r.headers["Content-Security-Policy"]
    # The same bytes declared as SVG (script-capable) must never render inline.
    with op.open(f"{CONSOLE}/a2a/feed/blob/{att['sha256']}?name=x.svg&mime=image/svg%2Bxml", timeout=15) as r:
        assert r.headers["Content-Type"].startswith("application/octet-stream")
        assert r.headers["Content-Disposition"].startswith("attachment")


def test_exchange_is_linked_to_audit_chain_without_content(live_exchange):
    task = live_exchange["task"]
    chain = REPO / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        pytest.skip("live chain not at the repo-local CORVIN_HOME")
    events = {}
    with chain.open(encoding="utf-8") as fh:
        for line in fh:
            if task["task_id"] in line:
                r = json.loads(line)
                events[r["event_type"]] = r
                assert "agent-hub-e2e-" not in line, "message content leaked into the audit chain"
    assert "A2A.envelope_sent" in events, f"no envelope_sent for {task['task_id']}: {sorted(events)}"
    assert "A2A.response_received" in events or "A2A.response_rejected" in events, sorted(events)
