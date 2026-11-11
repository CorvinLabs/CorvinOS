"""Driver run INSIDE a venv that has the CorvinOS wheel installed (tests/e2e/test_projects_wheel_e2e.py).

It imports only what the wheel ships (no repo path on ``sys.path``; the parent starts it with ``python -I``), mounts the projects
router (``corvin_console.routes.projects.router``, the one ``corvin_console.app`` includes) - not the whole console app: on
2026-11-11 ``corvin_console.app`` itself does not import from a wheel (``core.compliance.audit_chain_provider`` imports
``corvin_operator.bridges...`` and nothing puts ``corvin_core/_vendor`` on ``sys.path``; a separate defect, reported with
REVIEW-0064) - opens a real session and creates a project in the managed workspace over HTTP. It prints
one JSON line: the plan/create status codes and bodies, the doctor's ``hint_source`` check and where the hint test loaded from.

argv: <sandbox dir> <template catalogue dir>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    tmp, catalogue = Path(sys.argv[1]), Path(sys.argv[2])
    home = tmp / "corvin_home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / "_default" / "global" / sub).mkdir(parents=True, exist_ok=True)
    user = tmp                                                     # the data dir lies below HOME, as on a real host
    os.environ.update({"HOME": str(user), "USERPROFILE": str(user), "XDG_DATA_HOME": str(tmp / "data"),
                       "XDG_CONFIG_HOME": str(tmp / "xdg"), "LOCALAPPDATA": str(tmp / "data"), "APPDATA": str(tmp / "data"),
                       "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
                       "CORVIN_AUDIT_ANCHOR_KEY": str(home / "audit-anchor.key"),
                       "CORVIN_PLUGIN_SLOT_DIR": str(home / "plugin-slot"), "VOICE_AUDIT_PATH": str(home / "audit.jsonl"),
                       "CORVIN_TELEMETRY_OPTIN": "false", "CORVIN_CONSOLE_NO_AUTOBUILD": "1"})
    os.environ.pop("FORGE_ROOT", None)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.projects import hints
    from corvin_console import auth, projects_service as svc
    from corvin_console.routes.projects import router              # the router corvin_console.app mounts (app.py: projects_route)

    svc.template_source = lambda tenant: catalogue                 # the same seam every projects E2E uses
    svc.plugin_state = lambda tenant, pid: "enabled"
    app = FastAPI()
    app.include_router(router, prefix="/v1/console", tags=["console-projects"])
    rec = auth.create_session(tenant_id="_default", token_fingerprint="wheel-fp")
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("corvin_console_sid", rec.sid)
    h = {"X-CSRF-Token": auth.derive_csrf_token(rec.csrf_secret, rec.sid)}
    out: dict = {"hints_file": hints.__file__, "available": hints.available(),
                 "gate": str(hints.gate_path()) if hasattr(hints, "gate_path") else str(getattr(hints, "GATE_PATH", ""))}
    lst = c.get("/v1/console/projects")
    out["default_storage"] = lst.json().get("default_storage") if lst.status_code == 200 else lst.status_code
    plan = c.post("/v1/console/projects/plan", json={"template": "plain", "title": "Wheel one"}, headers=h)
    out["plan"] = [plan.status_code, plan.json()]
    if plan.status_code == 200:
        made = c.post("/v1/console/projects", json={"plan_id": plan.json()["plan_id"]}, headers=h)
        out["create"] = [made.status_code, made.json()]
        if made.status_code == 201:
            pid = made.json().get("id") or made.json().get("project_id") or "wheel-one"
            doc = c.get(f"/v1/console/projects/{pid}/health")
            out["health"] = [doc.status_code, doc.json()]
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
