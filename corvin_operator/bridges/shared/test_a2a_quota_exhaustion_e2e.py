"""A free-tier peer's daily compute pool, end to end: what the 11th task of the day looks like.

Root cause of "the peer refused the task without naming a reason" against PF65XQC9 (2026-10-09):
the free tier allows ``compute_units_per_day = 10`` agent runs, shared by everything on that
instance, counted per UTC day. Intensive testing used the peer's 10 units (9 completed runs + 1
that timed out), and from the 11th task on every task was refused — after ~22-28 s, because the
quota gate sat BEHIND the L44 classification (a model call) and the refusal threw its cause away.

This runs the REAL path in a subprocess with its own ``CORVIN_HOME`` (the worker snapshots it at
import): ``RemoteTriggerSender.send`` -> ``RemoteTriggerReceiver.receive`` -> ``spawn_a2a_worker``
-> ``license.compute_quota``, plus the signed ping that advertises the peer's task capacity. Only
the model (a scripted engine), the L44 classifier (a counter) and the network hop are replaced.

Asserted:
  * tasks 1-10 complete; the 11th and 12th are refused with the closed reason ``quota``
  * the refusal costs no L44 classification (fast-fail) and consumes no unit (counter stays 10)
  * the sender shows fixed text naming the daily limit — never a bare ``rejected``
  * the pong says ``available`` before the pool is spent and ``limit_reached`` after
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SHARED = Path(__file__).resolve().parent
REPO = SHARED.parents[2]

_SCRIPT = r'''
import hashlib, hmac, json, os, secrets, sys, tempfile, time, uuid
from dataclasses import dataclass
from pathlib import Path
R = Path(os.environ["REPO"])
for p in ("corvin_operator/bridges/shared", "corvin_operator/forge", "core/console", "core/orchestration", "core/workflows"):
    sys.path.insert(0, str(R / p))
import unittest.mock as mock
_se = mock.MagicMock(); _se.write_event = mock.MagicMock(return_value={"hash": "x"})
import remote_trigger_receiver as rtr
rtr._forge_se = _se
import remote_trigger_sender as rts
import a2a_http_server as srv
import spawn_gates
L44 = []
spawn_gates.check_l44 = lambda *a, **k: (L44.append(1), None)[1]

HMAC, RECV, ORIGIN = "f" * 64, "e" * 64, "quota-origin"
origins = Path(tempfile.mkdtemp()); eps = Path(tempfile.mkdtemp())
(origins / f"{ORIGIN}.json").write_text(json.dumps({"origin_id": ORIGIN, "hmac_key": HMAC, "recv_key": RECV,
    "enabled": True, "max_ttl_s": 300, "allowed_personas": ["assistant"], "spawn_worker": True}))
(origins / f"{ORIGIN}.json").chmod(0o600)
(eps / "ep-1.json").write_text(json.dumps({"endpoint_id": "ep-1", "url": "http://peer.invalid/v1/a2a/receive",
    "hmac_key": HMAC, "recv_key": RECV, "instance_id": "", "enabled": True, "default_ttl_s": 60,
    "origin_id_for_send": ORIGIN}))
(eps / "ep-1.json").chmod(0o600)

@dataclass
class Ev:
    type: str; text: str | None = None; usage: dict | None = None; error: str | None = None
class Engine:
    name = "fake"; capabilities = {}
    def spawn(self, prompt, **kw):
        return iter([Ev("text_delta", '{"output":"pong"}'), Ev("turn_completed", '{"output":"pong"}')])
    def cancel(self): pass

recv = rtr.RemoteTriggerReceiver(origins_dir=origins, engine_factory=lambda: Engine())

def hop(url, envelope, timeout_s):  # the network, replaced by direct calls into the real receiver
    if url.endswith("/v1/a2a/ping"):
        return srv.process_ping_request(envelope, recv)[1]
    return recv.receive(envelope).to_dict()
rts.RemoteTriggerSender._http_post = staticmethod(hop)
sender = rts.RemoteTriggerSender(eps, rts.RemoteEndpointRegistry(eps), instance_id="sender-iid")

facts = {"cap_before": sender.ping("ep-1").task_capacity}
results = []
for i in range(12):
    before = len(L44)
    t = time.time()
    r = sender.send("ep-1", "say pong", timeout_s=20)
    results.append({"status": r.status, "data": r.data, "detail": r.error_detail, "l44": len(L44) - before})
facts["results"] = results
facts["cap_after"] = sender.ping("ep-1").task_capacity
facts["counter"] = json.loads(next(Path(os.environ["CORVIN_HOME"]).rglob("compute_quota.json")).read_text())
print("FACTS=" + json.dumps(facts))
'''


def _run() -> dict:
    home = tempfile.mkdtemp(prefix="quota-e2e-")
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": home, "REPO": str(REPO),
        "CORVIN_HOME": home, "XDG_CONFIG_HOME": os.path.join(home, "xdg"),
        "FORGE_ROOT": os.path.join(home, "forge"), "CORVIN_AUDIT_ANCHOR_KEY": os.path.join(home, "anchor.key"),
    }
    out = subprocess.run([sys.executable, "-c", _SCRIPT], env=env, capture_output=True, text=True, timeout=240)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("FACTS=")), None)
    assert line, f"no result\nSTDOUT:\n{out.stdout[-1500:]}\nSTDERR:\n{out.stderr[-2500:]}"
    return json.loads(line[len("FACTS="):])


def test_the_eleventh_task_of_a_free_tier_peer_is_refused_cheaply_and_with_its_cause():
    f = _run()
    res = f["results"]
    assert [r["status"] for r in res[:10]] == ["ok"] * 10, res[:10]
    for r in res[10:]:
        assert r["status"] == "rejected"
        assert r["data"] == {"reason": "quota"}, "the cause must travel, not a bare rejected"
        assert "daily compute limit" in r["detail"] and "00:00 UTC" in r["detail"], r["detail"]
        assert r["l44"] == 0, "a refused task must not pay for the L44 classification"
    assert all(r["l44"] == 1 for r in res[:10]), "accepted tasks still pass L44"
    (units,) = [v for v in f["counter"].values()]
    assert units == 10, f"refused tasks must burn no unit: {f['counter']}"


def test_the_pong_advertises_the_capacity_before_anyone_waits_for_a_refusal():
    f = _run()
    assert f["cap_before"] == "available"
    assert f["cap_after"] == "limit_reached"
