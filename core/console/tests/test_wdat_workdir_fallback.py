"""Regression: the Worker-Engine (WDAT) graph must ALWAYS render for a session,
even when the session's persisted absolute ``workdir`` has gone stale after a
CORVIN_HOME change. The /wdat list + graph gate must fall back to the
canonically-resolved session workdir (where ACS actually writes).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))


def _reset():
    for n in list(sys.modules):
        if n.startswith("corvin_console") or n in ("forge.paths",):
            sys.modules.pop(n, None)


@contextmanager
def _sandbox(tenant_id: str = "_default"):
    with tempfile.TemporaryDirectory(prefix="wdat-fallback-") as td:
        home = Path(td) / "corvin"
        xdg = Path(td) / "xdg"
        (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)
        (xdg / "corvin-voice").mkdir(parents=True)
        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "XDG_CONFIG_HOME")}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        try:
            _reset()
            from corvin_console import auth as A
            from corvin_console import chat_runtime
            from corvin_console.app import router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            rec = A.create_session(tenant_id=tenant_id)
            app = FastAPI()
            app.include_router(router, prefix="/v1/console")
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", rec.sid)
            yield {"client": client, "rec": rec, "chat_runtime": chat_runtime,
                   "home": home, "tenant_id": tenant_id}
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _reset()


def _write_run(workdir: Path, run_id: str) -> None:
    rd = workdir / "acs" / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "manifest.json").write_text(json.dumps(
        {"workflow_id": "wf-1", "started_at": 1000.0}), encoding="utf-8")
    (rd / "result.json").write_text(json.dumps(
        {"status": "completed", "workers_spawned": 3, "workflow_id": "wf-1"}), encoding="utf-8")


def test_wdat_list_finds_run_via_canonical_when_workdir_stale():
    with _sandbox() as ctx:
        cr = ctx["chat_runtime"]
        sess = cr.create_session(ctx["tenant_id"], title="t")
        run_id = "run_20260628_abc123"
        # ACS wrote the run under the CANONICAL session workdir...
        _write_run(cr._workdir(ctx["tenant_id"], sess.sid), run_id)
        # ...but the persisted workdir went STALE (simulated home change).
        sess.workdir = Path("/nonexistent/stale/home/sessions/web:" + sess.sid)
        cr._persist(sess) if hasattr(cr, "_persist") else cr._save_session(sess) \
            if hasattr(cr, "_save_session") else None
        # Persist via the meta writer the runtime actually uses:
        meta = cr._meta_path(ctx["tenant_id"], sess.sid)
        d = cr._read_meta(meta) or {}
        d["workdir"] = str(sess.workdir)
        cr._write_meta(meta, d)

        r = ctx["client"].get(f"/v1/console/chat/sessions/{sess.sid}/wdat")
        assert r.status_code == 200, r.text
        body = r.json()
        run_ids = {x["run_id"] for x in body["runs"]}
        assert run_id in run_ids, f"stale workdir hid the run; got {run_ids}"
        assert body["count"] >= 1


def test_wdat_list_empty_is_clean_not_500():
    with _sandbox() as ctx:
        cr = ctx["chat_runtime"]
        sess = cr.create_session(ctx["tenant_id"], title="t")
        r = ctx["client"].get(f"/v1/console/chat/sessions/{sess.sid}/wdat")
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 0  # fresh session: clean empty, never an error


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
