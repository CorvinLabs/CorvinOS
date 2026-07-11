"""HTTP route-level tests for the hardened workflow resume endpoint
(POST /workflows/{wid}/runs/{rid}/resume) — CON-F1/F2/F3 + WF-A3.

  CON-F2: a concurrent (already-claimed) resume returns 409, not a double-run.
  CON-F3: malformed rid → 404 (not 500); missing checkpoint → 404; a legit
          resume hash-chains node events into the owner's audit chain.

Modeled on test_license_http_gates.py's sandbox.

Run:  python3 -m pytest core/console/tests/test_workflow_resume_hardening.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_WORKFLOWS = _REPO / "core" / "workflows"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_WORKFLOWS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


_WF = """awp: "1.1.0"
workflow:
  name: resume_console_test
  description: single ask_human node
inputs: {}
orchestration:
  engine: dag
  graph:
    - id: confirm
      type: ask_human
      depends_on: []
      channel: discord
      chat_id: "555"
      prompt: "Confirm?"
      expect:
        field: confirmed
        type: boolean
"""


@contextmanager
def _sandbox(tmp_path: Path):
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, home, tenant_id
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _make_workflow(home: Path, tenant_id: str, wid: str) -> Path:
    """Write the wid meta.json + yaml so the route's checks pass. Returns the
    yaml path."""
    from forge import paths as _fp  # imported under the sandboxed CORVIN_HOME
    wf_dir = _fp.tenant_home(tenant_id) / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{wid}.meta.json").write_text(
        json.dumps({"id": wid, "name": wid, "phase": "ready"}), encoding="utf-8"
    )
    yaml_p = wf_dir / f"{wid}.awp.yaml"
    yaml_p.write_text(_WF, encoding="utf-8")
    return yaml_p


def _pause_a_run(yaml_p: Path, tenant_id: str) -> str:
    """Run the single-ask_human workflow to a pause and return its run_id. The
    checkpoint lands under the same CORVIN_HOME/tenant the console reads."""
    from corvin_workflows import DAGRunner, StubEngine
    from corvin_workflows.storage import load_workflow
    doc = load_workflow(str(yaml_p))
    runner = DAGRunner(doc, engine=StubEngine(), tenant_id=tenant_id)
    result = runner.run(inputs={})
    assert result.state == "paused", result.state
    return result.run_id


class ResumeHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_malformed_rid_returns_404_not_500(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            _make_workflow(home, tid, "wf1")
            resp = client.post(
                "/v1/console/workflows/wf1/runs/..%2F..%2Fetc/resume",
                json={"reply": "ja"},
            )
            self.assertIn(resp.status_code, (404, 422),
                          f"malformed rid must not 500: {resp.status_code} {resp.text}")

    def test_missing_checkpoint_returns_404(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            _make_workflow(home, tid, "wf1")
            resp = client.post(
                "/v1/console/workflows/wf1/runs/deadbeefdeadbeef/resume",
                json={"reply": "ja"},
            )
            self.assertEqual(resp.status_code, 404, resp.text)

    def test_already_claimed_returns_409(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            yaml_p = _make_workflow(home, tid, "wf1")
            rid = _pause_a_run(yaml_p, tid)
            # Simulate a concurrent resume that already holds the claim.
            from corvin_workflows import checkpoint as _ckpt
            _ckpt.claim(rid, tenant_id=tid)
            resp = client.post(
                f"/v1/console/workflows/wf1/runs/{rid}/resume",
                json={"reply": "ja"},
            )
            self.assertEqual(resp.status_code, 409, resp.text)

    def test_happy_resume_completes_and_audits_nodes(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid):
            yaml_p = _make_workflow(home, tid, "wf1")
            rid = _pause_a_run(yaml_p, tid)
            resp = client.post(
                f"/v1/console/workflows/wf1/runs/{rid}/resume",
                json={"reply": "ja"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["status"], "complete")
            self.assertTrue(body["confirmed"])
            # CON-F3: node events for the resumed run are hash-chained into the
            # owner's audit log (workflow.* actions carrying the node id).
            from forge import paths as _fp
            chain = _fp.tenant_home(tid) / "global" / "forge" / "audit.jsonl"
            self.assertTrue(chain.exists(), "audit chain must exist after resume")
            lines = [json.loads(x) for x in chain.read_text().splitlines() if x.strip()]
            node_events = [
                r for r in lines
                if str(r.get("details", {}).get("action", "")).startswith("workflow.")
                and r.get("details", {}).get("step_id")
            ]
            self.assertTrue(node_events, "resumed node events must be hash-chained")


if __name__ == "__main__":
    unittest.main(verbosity=2)
