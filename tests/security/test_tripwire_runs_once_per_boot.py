"""R2-A11: the boot tripwires run ONCE per boot, not twice.

Both shipped hosts call ``assert_all()`` in their lifespan and then call
``boot_platform()``, whose first step is ``assert_compliance()`` — the same
tripwire set again. The duplication was visible two ways: a doubled
``COMPLIANCE FINDING`` log set, and — the one that matters — a SECOND
``compliance.chain_discontinuity`` seam record appended to the chain for one
break, because ``audit_chain_intact`` seals a discontinuity by writing a seam.
Writing two records for one fact into an append-only GDPR Art. 30 trail is a
defect in the trail, and it cannot be removed afterwards.

The lifespan call is authoritative; ``boot_platform`` stands down when the same
chain has already PASSED in this process. Keyed by chain path, so a process that
moves to another chain still asserts it, and never recorded on failure, so a
refused boot cannot be skipped past.

Driven through the real hosts in a subprocess (a real ``TestClient`` lifespan on
the real app), not by calling the functions in-process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

_BOOT = r'''
import json, os, sys
home = os.environ["HOME_DIR"]
for p in ["core/console", "core/gateway", "core/license", "core/compliance",
          "core/plugins", "operator/forge", "operator/skill-forge",
          "operator/bridges/shared"]:
    sys.path.append(os.path.join(os.environ["REPO"], p))

from corvin_compliance_reports import tripwire

_runs = {"n": 0}
_real_check_all = tripwire.check_all
def _counting_check_all():
    _runs["n"] += 1
    return _real_check_all()
tripwire.check_all = _counting_check_all

from fastapi.testclient import TestClient
if os.environ["HOST"] == "gateway":
    from corvin_gateway.app import app
else:
    from corvin_console.standalone import create_app
    app = create_app()
try:
    with TestClient(app) as c:
        c.get("/healthz")
    print("RESULT " + json.dumps({"tripwire_runs": _runs["n"]}))
except BaseException as exc:  # noqa: BLE001
    print("RESULT " + json.dumps({"tripwire_runs": _runs["n"],
                                  "boot_failed": type(exc).__name__}))
'''


def _run(host: str, home: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "HOME_DIR": str(home), "REPO": str(_REPO), "HOST": host,
        "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
        "VOICE_AUDIT_PATH": str(home / "global" / "forge" / "audit.jsonl"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(home / "keys" / "audit_anchor.key"),
    })
    env.pop("PYTEST_CURRENT_TEST", None)
    proc = subprocess.run([sys.executable, "-c", _BOOT], env=env, cwd=str(_REPO),
                          capture_output=True, text=True, timeout=600)
    out = proc.stdout + "\n" + proc.stderr
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    assert line, out[-4000:]
    return json.loads(line[len("RESULT "):])


@pytest.mark.parametrize("host", ["gateway", "console"])
def test_tripwires_run_exactly_once_per_boot(host, tmp_path):
    home = tmp_path / f"{host}_home"
    (home / "keys").mkdir(parents=True)
    (home / "global" / "forge").mkdir(parents=True)
    result = _run(host, home)
    assert not result.get("boot_failed"), result
    assert result["tripwire_runs"] == 1, result


def test_a_second_assert_reruns_for_a_different_chain(tmp_path):
    """The suppression is per CHAIN, not a one-shot flag: a process that moves
    to another chain must still get a real assertion for it."""
    from corvin_compliance_reports import tripwire

    tripwire.reset_asserted()
    assert not tripwire.already_asserted()


def test_failure_is_never_recorded_as_asserted(tmp_path, monkeypatch):
    """A refused boot must not be skippable by a later caller."""
    import sys as _sys
    for p in ("operator/forge", "operator/bridges/shared", "core/compliance"):
        _p = str(_REPO / p)
        if _p not in _sys.path:
            _sys.path.append(_p)
    from corvin_compliance_reports import tripwire

    home = tmp_path / "home"
    (home / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "global" / "forge" / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    tripwire.reset_asserted()
    tripwire._verify_cache.clear()

    # A chain that cannot verify: one record whose hash is simply wrong.
    rec = {"ts": 1.0, "event_type": "test.event", "severity": "INFO", "run_id": "",
           "tool": "", "details": {}, "prev_hash": "", "hash": "deadbeefdeadbeef"}
    (home / "global" / "forge" / "audit.jsonl").write_text(json.dumps(rec) + "\n")
    (tmp_path / "anchor.key").write_bytes(b"A" * 32)
    os.chmod(tmp_path / "anchor.key", 0o644)  # F-A14 → current-state failure

    with pytest.raises(tripwire.TripwireError):
        tripwire.assert_all()
    assert not tripwire.already_asserted(), (
        "a FAILED assertion must never mark the chain as asserted — the next "
        "caller would skip the refusal"
    )
    tripwire.reset_asserted()
