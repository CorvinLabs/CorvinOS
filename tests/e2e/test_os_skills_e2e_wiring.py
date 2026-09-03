"""E2E-Wiring-Proof for OS-Skills (per ADR-0215 / e2e-wiring-proof standard).

Two phases:

1. **Reachability** — the ONLY production caller of ``SkillManager`` is the
   console monitoring router (``corvin_console.routes.skills_monitoring``:
   ``GET /api/skills/status`` and ``GET /api/skills/{skill_id}/metrics``).
   The proof drives those routes over a real FastAPI ``TestClient`` (the same
   ``/v1/console`` prefix the SPA calls) against a sandboxed ``CORVIN_HOME``
   into which a skill was installed through ``SkillManager.install_skill``,
   and asserts the route reports THAT skill. The previous "reachability"
   test called a local closure and asserted it ran — a mock that could not
   fail.

2. **Functional** — ``SkillManager.execute_skill`` resolves the skill by
   trigger, runs the phases, persists run state, and chains a metadata-only
   ``skill.executed`` audit event.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.skills.skill_manager import SkillManager

_REPO = Path(__file__).resolve().parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
for _p in [
    str(_OPERATOR / "bridges" / "shared"),
    str(_OPERATOR / "bridges"),
    str(_OPERATOR / "forge"),
    str(_CONSOLE),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


MANIFEST = """
name: os.delegation_router
version: "1.0.0"
goal: "Route tasks"
triggers:
  - name: before_delegation_decision
    event_type: decision_point
input_schema:
  type: object
  required: [task_shape, context_size, tenant_id]
  properties:
    task_shape: {type: string}
    context_size: {type: integer}
    tenant_id: {type: string}
output_schema:
  type: object
  required: [decision, confidence, reasoning]
  properties:
    decision: {type: string}
    confidence: {type: number}
    reasoning: {type: string}
learning_signal:
  metrics: [latency]
  feedback_sources: []
  sanitization:
    disallow_fields: [prompt]
"""

SKILL_MD = """
---
name: os.delegation_router
version: "1.0.0"
---
# Skill
Route tasks.
"""


def _write_bundle(root: Path) -> Path:
    bundled_dir = root / "bundle" / "os_delegation_router_v1.0"
    bundled_dir.mkdir(parents=True)
    (bundled_dir / "manifest.yaml").write_text(MANIFEST)
    (bundled_dir / "SKILL.md").write_text(SKILL_MD)
    return bundled_dir


def _reset_console_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _console_sandbox(tmp_path: Path):
    """Sandboxed console app + live session (same shape as test_license_http_gates)."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    try:
        _reset_console_modules()
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
        _reset_console_modules()


class TestE2EWiringProof:

    @pytest.fixture
    def temp_corvin_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def skill_manager(self, temp_corvin_home):
        """SkillManager with the delegation_router installed through the real path."""
        mgr = SkillManager(temp_corvin_home, "_default")
        installed = mgr.install_skill(_write_bundle(temp_corvin_home))
        assert installed["success"], installed
        return mgr

    # ── Phase 1: reachability through the real HTTP boundary ─────────────────

    def test_reachability_console_status_route_reports_installed_skill(self, tmp_path):
        """GET /v1/console/api/skills/status — the production call site of
        SkillManager — lists the skill installed into the sandbox tenant."""
        with _console_sandbox(tmp_path) as (client, home, tenant_id):
            mgr = SkillManager(home, tenant_id)
            installed = mgr.install_skill(_write_bundle(tmp_path))
            assert installed["success"], installed

            r = client.get("/v1/console/api/skills/status")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["tenant_id"] == tenant_id
            ids = {s["id"] for s in body["skills"]}
            assert "os.delegation_router" in ids, body
            entry = next(s for s in body["skills"] if s["id"] == "os.delegation_router")
            assert entry["version"] == "1.0.0"
            assert entry["enabled"] is True
            assert entry["status"] == "healthy"

            r2 = client.get("/v1/console/api/skills/os.delegation_router/metrics")
            assert r2.status_code == 200, r2.text
            assert r2.json()["skill_id"] == "os.delegation_router"
            assert r2.json()["version"] == "1.0.0"

            r3 = client.get("/v1/console/api/skills/does.not.exist/metrics")
            assert r3.status_code == 404

    def test_reachability_route_refuses_unauthenticated(self, tmp_path):
        with _console_sandbox(tmp_path) as (client, home, tenant_id):
            client.cookies.clear()
            assert client.get("/v1/console/api/skills/status").status_code == 401

    # ── Negative: registry entries outside the tenant dir are refused ────────

    def test_registered_path_outside_tenant_dir_is_refused(self, temp_corvin_home):
        mgr = SkillManager(temp_corvin_home, "_default")
        mgr.registry.register_skill("evil", "1", "/etc")
        assert mgr.registry.get_skill_path("evil") is None
        assert "evil" not in mgr.list_active_skills()
        assert mgr.get_skill_status("evil") is None
        chain = temp_corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        assert chain.exists(), "refusal must be audited"
        events = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        assert any(e["event_type"] == "skill.path_refused" and e.get("tool") == "evil" for e in events)

    # ── Phase 2: functional ──────────────────────────────────────────────────

    def test_functional_skill_executes_e2e(self, skill_manager):
        result = skill_manager.execute_skill(
            trigger="before_delegation_decision",
            inputs={"task_shape": "big_data", "context_size": 50000, "tenant_id": "_default"},
            timeout_ms=5000,
        )
        assert result.success is True, f"Skill execution failed: {result.errors}"
        assert result.phase_completed >= 0
        assert result.output is not None
        for key in ("decision", "confidence", "reasoning"):
            assert key in result.output, f"Output missing {key!r}"
        output = result.output
        assert output["decision"] in ["native", "acs", "tde"]
        assert 0 <= output["confidence"] <= 1
        assert isinstance(output["reasoning"], str)
        assert 0 < len(output["reasoning"]) <= 500

    def test_functional_unknown_trigger_is_not_routed_to_a_default(self, skill_manager):
        result = skill_manager.execute_skill(trigger="no_such_trigger", inputs={})
        assert result.success is False
        assert result.errors and "no_such_trigger" in result.errors[0]

    def test_functional_native_routing(self, skill_manager):
        result = skill_manager.execute_skill(
            trigger="before_delegation_decision",
            inputs={"task_shape": "small_code", "context_size": 10000, "tenant_id": "_default"},
        )
        assert result.success is True
        assert result.output["decision"] == "native"

    def test_functional_acs_routing(self, skill_manager):
        result = skill_manager.execute_skill(
            trigger="before_delegation_decision",
            inputs={"task_shape": "big_data", "context_size": 500000, "tenant_id": "_default"},
        )
        assert result.success is True
        assert result.output["decision"] == "acs"

    def test_functional_state_persisted_and_audited(self, skill_manager, temp_corvin_home):
        result = skill_manager.execute_skill(
            trigger="before_delegation_decision",
            inputs={"task_shape": "big_data", "context_size": 50000,
                    "tenant_id": "_default", "prompt": "SECRET USER TEXT"},
        )
        run_id = result.run_id
        assert run_id is not None

        # Installed skills live in the TENANT skills dir (ADR-0007)
        run_dir = skill_manager.skills_dir / "os.delegation_router_v1.0.0" / "runs" / run_id
        assert run_dir.exists(), f"Run directory not found: {run_dir}"
        state_file = run_dir / "run_state.json"
        assert state_file.exists()
        state_data = json.loads(state_file.read_text())
        assert state_data["skill_id"] == "os.delegation_router"
        assert state_data["trigger"] == "before_delegation_decision"
        assert state_data["phase_completed"] >= 0
        # manifest sanitization.disallow_fields applied BEFORE persistence
        assert "prompt" not in state_data["inputs"]
        assert "SECRET USER TEXT" not in state_file.read_text()

        # metadata-only skill.executed event in the tenant CORE chain
        chain = temp_corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        events = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        executed = [e for e in events if e["event_type"] == "skill.executed"
                    and e.get("details", {}).get("run_id") == run_id]
        assert len(executed) == 1, events
        d = executed[0]["details"]
        assert d["skill_id"] == "os.delegation_router"
        assert d["status"] == "success"
        assert d["tenant_id"] == "_default"
        assert "latency_ms" in d
        assert "SECRET USER TEXT" not in json.dumps(executed[0])
        assert "big_data" not in json.dumps(executed[0])

        from forge.security_events import verify_chain  # type: ignore[import-not-found]
        ok, problems = verify_chain(chain)
        assert ok, problems

    def test_wiring_proof_summary(self, skill_manager):
        scenarios = [
            ("big_data", "acs"),
            ("small_code", "native"),
            ("prose", "native"),
            ("structured", "native"),
        ]
        for task_shape, expected_decision in scenarios:
            result = skill_manager.execute_skill(
                trigger="before_delegation_decision",
                inputs={"task_shape": task_shape, "context_size": 50000, "tenant_id": "_default"},
            )
            assert result.success is True, f"Failed for {task_shape}"
            assert result.output["decision"] == expected_decision, \
                f"Expected {expected_decision}, got {result.output['decision']} for {task_shape}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
