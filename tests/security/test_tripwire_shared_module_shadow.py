"""Regression: the L18/L34 boot tripwires must probe the bridges/shared gates,
not a same-named module that happens to be earlier on sys.path.

``corvin-webui.service`` sets PYTHONPATH with ``core/compliance`` BEFORE
``corvin_operator/bridges/shared``. ``core/compliance/consent.py`` is the FastAPI
``@consent_required`` decorator module and has no ``is_granted``, so
``tripwire._shared_module("consent")`` — a bare ``__import__`` — returned it and
``consent_gate_denies_by_default`` failed: a FATAL tripwire, i.e. a refused boot.
Run in a fresh interpreter with the unit's exact PYTHONPATH order.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_UNIT_PYTHONPATH = [
    "core/console", "core/gateway", "core/license", "core/compliance",
    "corvin_operator/forge", "corvin_operator/skill-forge",
    "corvin_operator/bridges/shared", "core/plugins",
]


def _probe(tmp_path: Path, pre_import: str = "") -> dict:
    code = (
        "import json, sys\n"
        f"{pre_import}\n"
        "from corvin_compliance_reports import tripwire as t\n"
        "r = t.consent_gate_denies_by_default()\n"
        "print(json.dumps({'ok': r.ok, 'detail': r.detail,\n"
        "  'consent': getattr(sys.modules.get('consent'), '__file__', None)}))\n"
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "CORVIN_HOME": str(tmp_path / "corvin"),
        "PYTHONPATH": os.pathsep.join(str(REPO / p) for p in _UNIT_PYTHONPATH),
    }
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(tmp_path),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_consent_probe_reaches_the_bridges_gate_under_the_unit_path_order(tmp_path):
    res = _probe(tmp_path)
    assert res["ok"], res["detail"]


def test_a_shadow_already_imported_as_consent_is_left_alone(tmp_path):
    # Someone in the process imported the decorator module as ``consent`` first:
    # the probe must still check the real gate, without clobbering that import.
    res = _probe(tmp_path, pre_import="import consent  # the core/compliance shadow")
    assert res["ok"], res["detail"]
    assert res["consent"].endswith(os.path.join("core", "compliance", "consent.py"))
