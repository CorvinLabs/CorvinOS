"""The console package must import in the same process shape the service boots.

corvin-webui.service runs ``corvin_gateway.app`` and mounts the console through
an opt-in ``try: from corvin_console import app`` (ADR-0015). Any ImportError
inside the console's ~120 route modules therefore does not crash the boot: it
removes ``/console`` and every ``/v1/console/*`` route from the live process,
which presents to the operator as a bare 404 on http://127.0.0.1:8765/console.

That happened on 2026-09-17: 9433de4b removed ``get_optimizer`` /
``ConfidenceOptimizer`` from ``core.learning.model_selection_optimizer`` while
``routes/model_selection_analytics.py`` still imported them. The running
console pre-dated the commit, so the break surfaced only at the next restart.

This test imports the console app the way the service does — a fresh
interpreter with the unit's PYTHONPATH — and asserts the routes that make it a
console are registered. A plain in-process import would be satisfied by
whatever earlier tests already loaded.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Mirrors Environment=PYTHONPATH in core/gateway/systemd/corvin-webui.service.
SERVICE_PYTHONPATH = [
    "core/console", "core/gateway", "core/license", "core/compliance",
    "corvin_operator/forge", "corvin_operator/skill-forge",
    "corvin_operator/bridges/shared", "core/plugins",
]
CONSOLE_VENV_PY = REPO / "core" / "console" / ".venv" / "bin" / "python"

PROBE = r"""
import json, sys
import corvin_console.app as capp
paths = sorted({getattr(r, "path", "") for r in capp.router.routes})
print(json.dumps({"n_routes": len(paths), "paths": paths}))
"""


def _service_python() -> str:
    return str(CONSOLE_VENV_PY) if CONSOLE_VENV_PY.exists() else sys.executable


def test_console_app_imports_in_a_fresh_interpreter(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(REPO / p) for p in SERVICE_PYTHONPATH)
    env["CORVIN_HOME"] = str(tmp_path / "home")  # never the operator's live home
    proc = subprocess.run(
        [_service_python(), "-c", PROBE],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=240,
    )
    assert proc.returncode == 0, (
        "corvin_console.app failed to import — the gateway would boot WITHOUT the "
        "console (/console 404):\n" + proc.stderr[-4000:]
    )
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["n_routes"] > 100, out["n_routes"]
    # the routes that make this process a console, not merely "some router"
    for must in ("/auth/local-login", "/capabilities/manifest", "/v1/engine/analytics", "/v1/engine/config"):
        assert any(p == must or p.startswith(must + "/") for p in out["paths"]), must


@pytest.mark.parametrize("name", [
    # persisted, audit-first learner — read by routes/model_selection_analytics.py,
    # routes/engine_api.py, fed by model_selection_learning_listener.py
    "ConfidenceOptimizer", "ModelStats", "get_optimizer", "initialize_optimizer",
    # k=4 in-memory learner (ADR-0845 Tier 2)
    "OSModelSelectorOptimizer", "TaskExecutionOutcome", "HaikuSuccessRateStats", "create_optimizer",
])
def test_model_selection_optimizer_keeps_both_public_apis(name: str) -> None:
    import core.learning.model_selection_optimizer as m
    assert hasattr(m, name), f"{name} removed — migrate every importer in the same commit"
