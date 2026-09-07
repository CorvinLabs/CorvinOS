"""F-A2 (2026-09-07): both shipped hosts run the compliance tripwire
UNCONDITIONALLY and treat an absent ``corvin_plugins`` as a boot failure.

Before this, ``corvin_gateway.app`` and ``corvin_console.standalone`` imported
``boot_platform`` from the plugin package and, when the package was simply not
installed, tolerated it — booting and serving with ZERO tripwires. The
reviewer's repro (``gw_noplugins2.py`` / ``sa_noplugins.py``) is reused here as
a subprocess regression: a meta-path finder strips ``corvin_plugins`` and the
host is booted through the real ASGI lifespan (``TestClient``).

Two assertions per host:
  * the boot FAILS (ModuleNotFoundError for corvin_plugins propagates), and
  * the tripwire RAN before that — evidenced by the ``audit_writer_reachable``
    probe file dance / the tripwire log line, and by the fact that a tampered
    chain aborts the boot with a TripwireError even earlier.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

_BOOT = textwrap.dedent('''
    import importlib.abc, json, os, sys
    home = os.environ["HOME_DIR"]
    for p in ["core/console", "core/gateway", "core/license", "core/compliance",
              "operator/forge", "operator/skill-forge", "operator/bridges/shared"]:
        sys.path.append(os.path.join(os.environ["REPO"], p))
    class _Strip(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == "corvin_plugins" or name.startswith("corvin_plugins."):
                raise ModuleNotFoundError("No module named 'corvin_plugins'", name="corvin_plugins")
            return None
    if os.environ.get("STRIP_PLUGINS") == "1":
        sys.meta_path.insert(0, _Strip())
    from fastapi.testclient import TestClient
    if os.environ["HOST"] == "gateway":
        from corvin_gateway.app import app
    else:
        from corvin_console.standalone import create_app
        app = create_app()
    try:
        with TestClient(app) as c:
            r = c.get("/healthz")
            print("BOOTED", r.status_code)
    except BaseException as exc:  # noqa: BLE001
        print("BOOT_FAILED", type(exc).__name__, str(exc)[:200])
''')


def _boot(host: str, tmp_path: Path, *, strip: bool, tamper: bool = False) -> str:
    home = tmp_path / f"{host}_home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update({
        "HOME_DIR": str(home), "REPO": str(_REPO), "HOST": host,
        "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
        # R2-A3: the resolver's own path under this CORVIN_HOME — a redirect
        # to any OTHER in-root path is now a tripwire failure in its own right.
        "VOICE_AUDIT_PATH": str(home / "global" / "forge" / "audit.jsonl"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
        "STRIP_PLUGINS": "1" if strip else "0",
    })
    env.pop("PYTEST_CURRENT_TEST", None)
    if tamper:
        # A current-state chain failure that later writers cannot re-anchor
        # over: a group-readable anchor key (F-A14) → anchor_key_insecure_mode
        # → audit_chain_intact refuses. (A single tampered record would be
        # SEALED by a seam record, F-A13 — that is not a boot failure.)
        rec = {"ts": 1.0, "event_type": "test.event", "severity": "INFO", "run_id": "",
               "tool": "", "details": {}, "prev_hash": "", "hash": "deadbeefdeadbeef"}
        chain = home / "global" / "forge" / "audit.jsonl"
        chain.parent.mkdir(parents=True, exist_ok=True)
        chain.write_text(json.dumps(rec) + "\n")
        (home / "anchor.key").write_bytes(b"A" * 32)
        os.chmod(home / "anchor.key", 0o644)
    proc = subprocess.run([sys.executable, "-c", _BOOT], env=env, cwd=str(_REPO),
                          capture_output=True, text=True, timeout=240)
    return proc.stdout + "\n" + proc.stderr


@pytest.mark.parametrize("host", ["gateway", "console"])
def test_absent_plugin_package_is_a_boot_failure(host, tmp_path):
    out = _boot(host, tmp_path, strip=True)
    assert "BOOT_FAILED ModuleNotFoundError" in out, out[-2000:]
    assert "corvin_plugins" in out
    assert "BOOTED" not in out
    # The tripwire ran BEFORE the plugin import: its reporting-only L10 probe
    # logs on every boot in this environment (no path_gate hook registered).
    assert "corvin.compliance.tripwire" in out, out[-2000:]


@pytest.mark.parametrize("host", ["gateway", "console"])
def test_tampered_chain_aborts_boot_before_plugins(host, tmp_path):
    out = _boot(host, tmp_path, strip=True, tamper=True)
    assert "BOOT_FAILED TripwireError" in out, out[-2000:]
    assert "audit_chain_intact" in out
    assert "BOOTED" not in out
