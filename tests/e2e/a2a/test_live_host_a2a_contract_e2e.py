"""A RUNNING host speaks the current A2A contract — checked over its real HTTP boundary.

Unit and in-process E2E suites prove the code; they cannot prove that the PROCESS an operator
actually runs has loaded it. A service that was started before a commit keeps serving the old
behaviour until it is restarted — the console bundle can be new while the A2A receiver behind it is
not (this is how "Item 6", the restart after the quota fast-fail / task-capacity change, is proven).

Point it at any host:

    CORVIN_LIVE_URL=http://127.0.0.1:8765 \
    python3 -m pytest tests/e2e/a2a/test_live_host_a2a_contract_e2e.py -q

It never touches a real pairing: it drops a THROWAWAY origin (random id and keys, mode 0600) into
the origins directory the host reads, talks to ``POST /v1/a2a/ping`` as that origin, and removes the
file again. Skipped unless ``CORVIN_LIVE_URL`` is set.

Contract asserted (ADR-2242 §2/§8):
  * the console feed carries ``stages`` and every peer carries ``task_capacity``
  * a signed pong verifies with the pairing's recv key and says ``task_capacity``
  * a signed task-status query for an unknown task answers ``{"stage": "unknown"}``
  * a re-aimed task signature is ignored (plain pong, no ``task_stage``)
  * an ordinary ping without any of that still works (an older sender)
"""
from __future__ import annotations

import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "corvin_operator" / "bridges" / "shared"))

BASE = (os.environ.get("CORVIN_LIVE_URL") or "").rstrip("/")
pytestmark = pytest.mark.skipif(not BASE, reason="set CORVIN_LIVE_URL to a running host")


def _canon(d: dict) -> bytes:
    return json.dumps(d, separators=(",", ":"), sort_keys=True).encode()


def _sign(key_hex: str, d: dict) -> str:
    return hmac.new(bytes.fromhex(key_hex), _canon(d), hashlib.sha256).hexdigest()


def _post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture(scope="module")
def probe_origin():
    """A throwaway pairing: random id + keys, in the directory the host reads."""
    from remote_trigger_receiver import OriginRegistry  # noqa: PLC0415 — path set above

    origins_dir = Path(os.environ.get("CORVIN_LIVE_ORIGINS_DIR") or OriginRegistry(None)._dir)
    origins_dir.mkdir(parents=True, exist_ok=True)
    oid = f"live-probe-{uuid.uuid4().hex[:12]}"
    cfg = {"origin_id": oid, "hmac_key": secrets.token_hex(32), "recv_key": secrets.token_hex(32),
           "enabled": True, "max_ttl_s": 60, "allowed_personas": ["assistant"], "spawn_worker": False}
    path = origins_dir / f"{oid}.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)
    try:
        yield cfg
    finally:
        path.unlink(missing_ok=True)


def _ping(o: dict, *, task_id: str | None = None, task_sig_for: str | None = None) -> tuple[int, dict]:
    base = {"ping_id": uuid.uuid4().hex, "issued_at": int(time.time()), "origin_id": o["origin_id"]}
    req = dict(base, signature=_sign(o["hmac_key"], base))
    if task_id is not None:
        req["task_id"] = task_id
        req["task_sig"] = _sign(o["hmac_key"], dict(base, task_id=task_sig_for or task_id))
    return _post("/v1/a2a/ping", req)


def _verified(o: dict, resp: dict) -> dict:
    body = dict(resp)
    sig = body.pop("signature", "")
    assert sig and hmac.compare_digest(sig, _sign(o["recv_key"], body)), "pong signature does not verify"
    return body


def test_ordinary_ping_still_works_for_an_older_sender(probe_origin):
    code, resp = _ping(probe_origin)
    assert code == 200, resp
    pong = _verified(probe_origin, resp)
    assert pong["ok"] is True and pong.get("instance_id")


def test_the_pong_advertises_task_capacity(probe_origin):
    code, resp = _ping(probe_origin)
    pong = _verified(probe_origin, resp)
    assert pong.get("task_capacity") in ("available", "limit_reached"), (
        "the host answers a plain pong with no task_capacity — it is still running code from before "
        f"the quota fast-fail / capacity change: {sorted(pong)}")


def test_a_task_status_query_for_an_unknown_task_says_unknown(probe_origin):
    tid = f"no-such-task-{uuid.uuid4().hex[:8]}"
    code, resp = _ping(probe_origin, task_id=tid)
    assert code == 200, resp
    pong = _verified(probe_origin, resp)
    assert pong.get("task_stage") == {"stage": "unknown"}, pong


def test_a_reaimed_task_signature_is_ignored(probe_origin):
    code, resp = _ping(probe_origin, task_id="task-a", task_sig_for="task-b")
    assert code == 200, resp
    assert "task_stage" not in _verified(probe_origin, resp)


def test_a_forged_ping_is_refused_without_a_hint(probe_origin):
    bad = dict(probe_origin, hmac_key=secrets.token_hex(32))
    code, resp = _ping(bad)
    assert code == 403 and resp == {"reason": "ping_rejected"}, (code, resp)


def test_console_feed_carries_stages_and_every_peer_task_capacity():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with op.open(f"{BASE}/v1/console/auth/local-login", timeout=30):
        pass
    with op.open(f"{BASE}/v1/console/a2a/feed?limit=5", timeout=30) as r:
        feed = json.loads(r.read())
    assert isinstance(feed.get("stages"), dict), (
        f"the feed has no `stages` map — the console host predates the message-lifecycle change: {sorted(feed)}")
    for p in feed["peers"]:
        assert "task_capacity" in p, f"peer {p.get('peer_id')} has no task_capacity key: {sorted(p)}"
        assert p["task_capacity"] in (None, "available", "limit_reached")
