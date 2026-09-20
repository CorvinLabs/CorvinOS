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

# Routes are counted through PUBLIC surfaces, deliberately.
#
# Reading ``capp.router.routes`` directly stopped working at FastAPI 0.141: an
# ``include_router`` call no longer copies the sub-router's routes, it appends one
# lazy ``_IncludedRouter`` placeholder whose ``.path`` is ``None``. The old probe
# therefore reported **3** distinct paths for a console exposing 626 of them —
# a false red that looks exactly like the real break this file exists to catch
# (and which masked nothing only because an ImportError happened to fail first).
#
# ``app.openapi()`` gives the count. It cannot see ``include_in_schema=False``
# routes — ``/auth/local-login`` is one — so presence is checked by REQUESTING
# each path through TestClient and asserting it is not a 404. Any other status is
# proof of registration: 401/403 means the route exists and the auth layer
# answered. ``_ABSENT`` is the positive control: if that returns anything but 404
# the discrimination is gone and every presence check below is vacuous.
PROBE = r"""
import json, sys
import corvin_console.app as capp
from fastapi.testclient import TestClient

MUST = ["/auth/local-login", "/capabilities/manifest",
        "/v1/engine/analytics", "/v1/engine/config"]
ABSENT = "/definitely-not-a-console-route-xyzzy"

paths = sorted(capp.app.openapi().get("paths", {}))
client = TestClient(capp.app)
status = {p: client.get(p).status_code for p in MUST + [ABSENT]}
print(json.dumps({"n_routes": len(paths), "paths": paths, "status": status,
                  "absent_path": ABSENT}))
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
    assert out["n_routes"] > 100, (
        f"the console exposes only {out['n_routes']} documented paths. Either a "
        "route module dropped out, or this probe's enumeration broke — check that "
        "app.openapi() still reports every router before assuming a regression."
    )
    absent = out["absent_path"]
    assert out["status"][absent] == 404, (
        f"POSITIVE CONTROL FAILED: {absent} answered "
        f"{out['status'][absent]}, not 404. A catch-all is swallowing unknown "
        "paths, so 'not 404' no longer proves a route is registered and every "
        "presence assertion below is vacuous."
    )
    # the routes that make this process a console, not merely "some router"
    for must, code in ((p, out["status"][p]) for p in out["status"] if p != absent):
        assert code != 404, (
            f"{must} is NOT registered (404). The console imported, so this is a "
            "route module that silently failed to mount — /console itself may "
            "still answer while the operator's panel is dead."
        )


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
