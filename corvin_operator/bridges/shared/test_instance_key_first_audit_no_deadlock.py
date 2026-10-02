"""Regression: the FIRST audit write of a fresh install must not deadlock.

``security_events.write_event`` signs each record via
``instance_identity.sign_payload`` while holding the chain lock; on an install
with no instance key, ``ensure_instance_key()`` creates one and audits
``instance.key_rotated``. That audit re-entered ``write_event`` on the held lock
and the process hung forever (found 2026-10-02: every test suite with a fresh
CORVIN_HOME hung). Runs in a subprocess with a hard timeout, so a regression
fails instead of hanging the suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_first_audit_write_on_fresh_install_completes():
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, CORVIN_HOME=str(Path(d) / "home"),
                   FORGE_ROOT=str(Path(d) / "forge"), XDG_CONFIG_HOME=str(Path(d) / "xdg"),
                   CORVIN_AUDIT_ANCHOR_KEY=str(Path(d) / "anchor.key"))
        env.pop("VOICE_AUDIT_PATH", None)  # FORGE_ROOT above is the sandbox chain
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import audit;"
                "ok = audit.audit_event('session_ledger.boundary', channel='t', chat_key='c',"
                " details={'boundary': 'reset', 'reason': 'timeout'});"
                "print(audit.audit_path()); print(ok)")
        r = subprocess.run([sys.executable, "-c", code, str(HERE)], env=env,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        out = r.stdout.strip().splitlines()
        assert out[-1] == "True", r.stdout + r.stderr
        chain = Path(out[-2])
        assert str(chain).startswith(d), f"chain escaped the sandbox: {chain}"
        kinds = [json.loads(l)["event_type"] for l in chain.read_text().splitlines() if l.strip()]
        assert "session_ledger.boundary" in kinds
        assert (Path(d) / "home").rglob("instance_key.pem"), "the key was generated"


if __name__ == "__main__":
    test_first_audit_write_on_fresh_install_completes()
    print("PASS")
