"""Wave 1c T09 — compute.worker_terminated + a2a.offline_pair_initiated, E2E.

Both events were registered (EVENT_SEVERITY + _EVENT_ALLOWLIST) with no live
emitter. Each is driven here through its REAL entry point as a subprocess:

* ``python -m corvin_compute.cli serve`` stopped with SIGTERM — what
  ``systemctl stop corvin-compute@<tid>`` sends. Before this change SIGTERM
  killed the process ahead of its ``finally: server.stop()``, so a service
  stop left no record at all.
* ``corvin_a2a.py pair <peer> <url> --offline-pair`` — the CLI that switches
  network attestation off for an origin. Audit-first: the record is committed
  before the origin file exists, and the pair is refused if it cannot be.

The chain is read back from the tenant chain file and hash-verified.

``a2a.genesis_block_created`` is deliberately NOT here: no production code
creates an NBAC genesis block (``nbac.sign_genesis_block`` has no caller), so
there is no subject to wire — see the fence test at the bottom.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import tempfile
import unittest

_REPO = Path(__file__).resolve().parents[2]
_FORGE = _REPO / "corvin_operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.append(str(_FORGE))

from forge.security_events import verify_chain  # noqa: E402


def _chain(home: Path, tenant: str = "_default") -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _records(chain: Path, event_type: str) -> list[dict]:
    if not chain.exists():
        return []
    rows = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return [r for r in rows if r.get("event_type") == event_type]


def _env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(tmp_path / "home")
    env.pop("CORVIN_TENANT_ID", None)
    env.pop("VOICE_AUDIT_PATH", None)
    env["REMOTE_ORIGINS_DIR"] = str(tmp_path / "origins")
    env["REMOTE_ENDPOINTS_DIR"] = str(tmp_path / "endpoints")
    env["PYTHONPATH"] = os.pathsep.join([
        str(_REPO / "core" / "compute"),
        str(_FORGE),
        str(_REPO / "corvin_operator" / "bridges" / "shared"),
        str(_REPO / "core" / "plugins"),
        str(_REPO),
    ])
    for d in ("home", "origins", "endpoints"):
        (tmp_path / d).mkdir(exist_ok=True)
    return env


# ── compute.worker_terminated ────────────────────────────────────────────────


def _t_sigterm(tmp_path):
    env = _env(tmp_path)
    sock = tmp_path / "w.sock"
    proc = subprocess.Popen(
        [sys.executable, "-m", "corvin_compute.cli", "serve",
         "--tenant", "_default", "--socket", str(sock)],
        env=env, cwd=str(tmp_path),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while not sock.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"worker exited early: {proc.stdout.read().decode()[-2000:]}")
            time.sleep(0.1)
        assert sock.exists(), "positive control: the worker must actually be serving"
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert rc == 0, "SIGTERM must go through the graceful stop path, not kill the process"
    assert not sock.exists(), "stop() ran: socket cleaned up"

    chain = _chain(tmp_path / "home")
    recs = _records(chain, "compute.worker_terminated")
    assert len(recs) == 1, recs
    d = recs[0]["details"]
    assert d["termination_reason"] == "shutdown"
    assert d["worker_id"] == f"compute:{proc.pid}"
    assert d["tenant_id"] == "_default"
    assert str(sock) not in json.dumps(recs[0]), "no socket path in the record"
    ok, problems = verify_chain(chain)
    assert ok, problems


# ── a2a.offline_pair_initiated ───────────────────────────────────────────────

_A2A_CLI = _REPO / "corvin_operator" / "voice" / "scripts" / "corvin_a2a.py"


def _pair(tmp_path: Path, env: dict, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_A2A_CLI), "pair", "peerx", "http://127.0.0.1:9", *extra],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
    )


def _t_offline_recorded(tmp_path):
    env = _env(tmp_path)
    res = _pair(tmp_path, env, "--offline-pair")
    assert res.returncode == 0, res.stderr[-2000:]
    origin = json.loads((tmp_path / "origins" / "peerx.json").read_text())
    assert origin["require_network_attestation"] is False

    chain = _chain(tmp_path / "home")
    recs = _records(chain, "a2a.offline_pair_initiated")
    assert len(recs) == 1
    d = recs[0]["details"]
    assert d["peer_id"] == "peerx"
    assert d["pairing_id"] == origin["pairing_id"]
    assert d["ttl_s"] == 300
    assert d["tenant_id"] == "_default"
    blob = json.dumps(recs[0])
    for secret in (origin["hmac_key"], origin["recv_key"], "127.0.0.1"):
        assert secret not in blob, "no key material / URL in the chain"
    ok, problems = verify_chain(chain)
    assert ok, problems


def _t_offline_refused(tmp_path):
    env = _env(tmp_path)
    # Make the tenant chain directory impossible to create: a FILE where the
    # forge directory must go.
    g = tmp_path / "home" / "tenants" / "_default" / "global"
    g.mkdir(parents=True)
    (g / "forge").write_text("not a directory")
    res = _pair(tmp_path, env, "--offline-pair")
    assert res.returncode == 1
    assert "offline pairing refused" in res.stderr
    assert not (tmp_path / "origins" / "peerx.json").exists(), "audit-first: no origin without a record"


# ── a2a.genesis_block_created: no subject (fence) ────────────────────────────


def _t_genesis_fence():
    """If this fails, something now CREATES an NBAC genesis block — wire
    ``a2a_audit.emit_genesis_block_created`` at that site in the same commit."""
    hits = []
    for root in ("core", "corvin_operator", "ops"):
        for p in (_REPO / root).rglob("*.py"):
            s = str(p)
            if "/tests/" in s or p.name.startswith("test_") or p.name == "nbac.py":
                continue
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            if "sign_genesis_block(" in text or "build_genesis_payload(" in text:
                hits.append(s)
    assert hits == [], hits


class TestWave1cAuditWiringE2E(unittest.TestCase):
    def _run(self, fn):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))

    def test_sigterm_on_compute_worker_records_termination(self):
        self._run(_t_sigterm)

    def test_offline_pair_is_recorded_before_the_origin_exists(self):
        self._run(_t_offline_recorded)

    def test_offline_pair_refused_when_audit_cannot_commit(self):
        self._run(_t_offline_refused)

    def test_genesis_block_has_no_production_creator(self):
        _t_genesis_fence()


if __name__ == "__main__":
    unittest.main()
