"""HTTP-level regression tests for GET /v1/console/capabilities/manifest (ADR-0561).

2026-09-04: the endpoint answered 500 on every live host — one builtin panel
(``vibe-engineering``, commit 4fdd32a5) had lost its ``requiredFlag`` key and
the gating loop indexes ``p["requiredFlag"]`` strictly. The shell tolerated the
failure (registry fallback), which is exactly why nothing noticed: the manifest
was dead and the sidebar silently ran on the static list. These tests go
through the real HTTP boundary (TestClient), so a schema slip in ANY panel
source (builtin, plugin, skill) surfaces here instead of in the browser console.

Run: core/console/.venv/bin/python -m pytest core/console/tests/test_console_manifest_route.py -v
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

_REPO = Path(__file__).resolve().parents[3]
for _p in (
    _REPO / "core" / "console",
    _REPO / "operator" / "bridges" / "shared",
    _REPO / "core" / "plugins",
    _REPO / "operator" / "forge",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Every panel the manifest emits must carry BOTH gate keys — the frontend's
# gatePanels() and the backend gating loop read them unconditionally.
_GATE_KEYS = ("requiredFlag", "requiredCapability")


def _reset_modules() -> None:
    for key in list(sys.modules):
        if key.startswith("corvin_console"):
            del sys.modules[key]


@contextmanager
def _client(tmp_path: Path):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / "_default" / "global" / "console" / "sessions").mkdir(parents=True)
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = "_default"
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console.app import router as console_router
        from corvin_console.deps import require_csrf, require_session

        app = FastAPI()
        app.include_router(console_router, prefix="/v1/console")
        rec = MagicMock()
        rec.username = "operator"
        rec.tenant_id = "_default"
        rec.role = "admin"
        rec.sid_fingerprint = "test_fp_0123456789ab"
        app.dependency_overrides[require_session] = lambda: rec
        app.dependency_overrides[require_csrf] = lambda: "csrf-ok"
        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def test_manifest_answers_200_with_panels_and_nav(tmp_path):
    with _client(tmp_path) as tc:
        r = tc.get("/v1/console/capabilities/manifest")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == "2.0"
        assert body["contract_version"]
        assert isinstance(body["panels"], list) and body["panels"]
        assert isinstance(body["nav_groups"], list) and body["nav_groups"]
        assert len(body["hash"]) == 64


def test_manifest_lists_vibe_engineering_ungated(tmp_path):
    """The Vibe Dashboard route is ungated (its sidebar entry gates on the flag);
    it must survive gating even when every flag reads False (sandbox has no
    Skill registry, so _read_flags() resolves all flags to False)."""
    with _client(tmp_path) as tc:
        body = tc.get("/v1/console/capabilities/manifest").json()
        ids = {p["id"] for p in body["panels"]}
        assert "vibe-engineering" in ids
        # A flag-gated sibling is correctly hidden in the all-flags-off sandbox.
        assert "brain-monitor" not in ids


def test_every_panel_source_carries_both_gate_keys(tmp_path):
    """Guard for the 4fdd32a5 failure class: a panel dict missing a gate key
    must fail HERE, at the source, not as a KeyError inside the request."""
    with _client(tmp_path):
        from corvin_console.routes import capabilities as cap

        sources = {
            "builtin": cap._get_builtin_panels(),
            "plugin": cap._get_plugin_panels(),
            "skill": cap._get_skill_panels({}),
        }
        for source, panels in sources.items():
            for panel in panels:
                missing = [k for k in _GATE_KEYS if k not in panel]
                assert not missing, f"{source} panel {panel.get('id')!r} lacks {missing}"
