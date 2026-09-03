"""ACP (Agentic Control Plane) — E2E through the REAL transport boundaries.

Proves, against a sandboxed CORVIN_HOME, that the Skills registry is reachable
from every production surface that consumes it — not that the Skills return the
right value when called directly (the unit suites already do that):

1. Boot:   ``corvin_plugins.bootstrap.boot_platform()`` — the ONE sequence both
           shipped hosts call — populates the global registry (7 builtin Skills)
           with the core audit writer attached.
2. Audit:  a Skill execution lands in the hash-chained ``audit.jsonl`` as a
           ``skill.executed`` record carrying skill_id/status/lom_hash, and the
           chain verifies afterwards (GDPR Art. 30/32, ADR-0537).
3. HTTP:   ``GET /v1/console/capabilities`` (an ``async def`` route) resolves the
           flag manifest THROUGH ``os.capabilities`` — the audit chain proves the
           Skill ran inside the request.
4. HTTP:   ``GET /v1/console/vibe-engineering/pipeline`` reports ``active_enabled``
           from ``os.vibe_engineering`` (again audited).
5. Slash:  ``/build`` reaches the plugin builder because ``os.plugin_builder``
           answers (the dispatcher is the entry point of both the console and the
           messenger bridges).
6. Learn:  the execution is mirrored into the ADR-0314 learning event store.

Until 2026-09-03 every one of these surfaces received "Skill not found": nothing
ever registered the builtin Skills, and ``asyncio.run()`` inside the async routes
raised on top of that. These tests are the wiring proof CLAUDE.md § E2E Wiring
Proof demands.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"), str(_CONSOLE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules() -> None:
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


def _skill_events(audit_file: Path) -> list[dict]:
    if not audit_file.exists():
        return []
    out = []
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("event_type") == "skill.executed":
            out.append(rec)
    return out


@contextmanager
def _sandbox(tmp_path: Path):
    """Sandboxed CORVIN_HOME + booted platform + authenticated console client."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    for sub in ("global/auth", "global/forge", "global/console/sessions"):
        (home / "tenants" / tenant_id / sub).mkdir(parents=True)

    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "CORVIN_TELEMETRY_OPTIN", "VOICE_AUDIT_PATH")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["CORVIN_TELEMETRY_OPTIN"] = "false"
    os.environ.pop("VOICE_AUDIT_PATH", None)

    try:
        _reset_modules()
        import corvin_console  # noqa: F401 — operator bootstrap puts `audit`/`forge` on sys.path
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from corvin_plugins.bootstrap import boot_platform
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        boot_platform()  # tripwires → plugins → ACP Skills registry → post-boot check

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="acp-e2e-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})

        from audit import audit_path

        yield client, home, tenant_id, audit_path()
    finally:
        try:
            from core.skills.skill_registry_phase1 import get_registry

            lb = get_registry().learning_backend
            if lb is not None and hasattr(lb, "emitter"):
                lb.emitter.stop()
        except Exception:  # noqa: BLE001
            pass
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestAcpBootWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_boot_platform_populates_registry_with_core_audit(self):
        """PROOF 1: boot_platform() registers all builtin Skills and attaches the core audit writer."""
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from core.skills.os_skills_phase1 import BUILTIN_SKILL_IDS
            from core.skills.skill_registry_phase1 import CoreAuditBackend, get_registry

            registry = get_registry()
            ids = sorted(m.id for m in registry.list_skills())
            self.assertEqual(ids, sorted(BUILTIN_SKILL_IDS))
            self.assertIsInstance(registry.audit_backend, CoreAuditBackend)
            self.assertEqual(registry.tenant_id, tid)
            self.assertIn(tid, registry._allowed_tenants)

    def test_skill_execution_is_hash_chained_and_chain_verifies(self):
        """PROOF 2: a Skill decision is a chained audit record with LoM hash; chain verifies."""
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from audit import verify_audit
            from core.skills.skill_registry_phase1 import get_registry

            registry = get_registry()
            before = len(_skill_events(audit_file))
            res = registry.execute(
                "os.delegation_router",
                {"complexity": 9, "task_type": "code"},
                lom="core/skills/boot.py:boot_skills:L60",
            )
            self.assertEqual(res.status, "success", res.error_message)
            self.assertEqual(res.output["engine"], "claude-opus-5")
            self.assertEqual(registry.audit_backend.write_failures, 0)

            events = _skill_events(audit_file)
            self.assertEqual(len(events), before + 1, "exactly one skill.executed record expected")
            details = events[-1]["details"]
            self.assertEqual(details["skill_id"], "os.delegation_router")
            self.assertEqual(details["status"], "success")
            self.assertEqual(details["tenant_id"], tid)
            self.assertEqual(details["lom_hash"], res.lom_hash)
            self.assertEqual(len(details["lom_hash"]), 64)
            # The chain writer keeps the record content-free: no Skill output in the chain.
            self.assertIn("output", details.get("_dropped_fields", []))
            self.assertTrue(events[-1].get("hash") and events[-1].get("prev_hash"))

            ok, problems = verify_audit(audit_file)
            self.assertTrue(ok, problems)

    def test_empty_tenant_is_refused_and_audited(self):
        """PROOF 2b: fail-closed tenant isolation at the boot-wired registry."""
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from core.skills.skill_registry_phase1 import get_registry

            res = get_registry().execute("os.delegation_router", {}, tenant_id="")
            self.assertEqual(res.status, "error")
            self.assertIn("isolation", res.error_message.lower())
            last = _skill_events(audit_file)[-1]["details"]
            self.assertEqual(last["status"], "error")


class TestAcpHttpSurfaces(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_capabilities_route_resolves_flags_through_skill(self):
        """PROOF 3: /capabilities (async route) gets its flags from os.capabilities."""
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from corvin_core.feature_flags import is_enabled
            from corvin_console.routes.capabilities import GATED_FLAGS

            before = [e for e in _skill_events(audit_file) if e["details"]["skill_id"] == "os.capabilities"]
            resp = client.get("/v1/console/capabilities")
            self.assertEqual(resp.status_code, 200, resp.text)
            flags = resp.json()["flags"]
            self.assertEqual(set(flags), set(GATED_FLAGS))
            # The Skill answers with the per-tenant flag registry, not a hardcoded False.
            for flag in GATED_FLAGS:
                self.assertEqual(flags[flag], bool(is_enabled(flag, tid)), flag)
            # Whether a given flag is on is the flag registry's policy (a fresh home
            # has no overlay); the wiring proof is the equality above + the audit record.

            after = [e for e in _skill_events(audit_file) if e["details"]["skill_id"] == "os.capabilities"]
            self.assertEqual(len(after), len(before) + 1, "os.capabilities must be audited per request")
            self.assertEqual(after[-1]["details"]["status"], "success")

    def test_capabilities_unauthenticated_is_401(self):
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from fastapi.testclient import TestClient

            anon = TestClient(client.app, raise_server_exceptions=False)
            self.assertEqual(anon.get("/v1/console/capabilities").status_code, 401)

    def test_vibe_pipeline_route_reports_active_from_skill(self):
        """PROOF 4: /vibe-engineering/pipeline active_enabled comes from os.vibe_engineering."""
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from corvin_core.feature_flags import is_enabled

            resp = client.get("/v1/console/vibe-engineering/pipeline")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            if not body.get("available"):
                self.skipTest("CEL stages module unavailable in this layout — route short-circuits before the Skill")
            self.assertEqual(body["active_enabled"], bool(is_enabled("vibe_engineering_active", tid)))
            vibe = [e for e in _skill_events(audit_file) if e["details"]["skill_id"] == "os.vibe_engineering"]
            self.assertGreaterEqual(len(vibe), 1, "os.vibe_engineering must have executed inside the request")
            self.assertEqual(vibe[-1]["details"]["status"], "success")

    def test_slash_build_reaches_plugin_builder(self):
        """PROOF 5: the slash dispatcher's /build gate is answered by os.plugin_builder."""
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from corvin_console import slash_commands
            from corvin_core.feature_flags import is_enabled

            enabled = slash_commands._plugin_builder_enabled(tid)
            self.assertEqual(enabled, bool(is_enabled("plugin_builder_enabled", tid)))
            pb = [e for e in _skill_events(audit_file) if e["details"]["skill_id"] == "os.plugin_builder"]
            self.assertGreaterEqual(len(pb), 1)
            self.assertEqual(pb[-1]["details"]["status"], "success")

            if enabled:
                out = slash_commands.handle(
                    "/build", tier=None, tenant_id=tid, fingerprint="acp-e2e-fp",
                    session_key="acp-e2e-session", configured_engine="claude",
                )
                self.assertIsNotNone(out, "/build must be dispatched, not passed through as unknown")

    def test_learning_event_mirrored_to_store(self):
        """PROOF 6: every execution is mirrored into the ADR-0314 learning store (no silent drop)."""
        with _sandbox(self._tmp) as (client, home, tid, audit_file):
            from core.skills.skill_registry_phase1 import get_registry

            registry = get_registry()
            self.assertIsNotNone(registry.learning_backend, "learning backend not wired at boot")
            registry.execute("os.delegation_router", {"complexity": 2, "task_type": "chat"}, lom="x:y:1")
            emitter = registry.learning_backend.emitter
            events_dir = emitter.store.events_dir
            deadline = time.time() + 5
            lines: list[dict] = []
            while time.time() < deadline:
                lines = [
                    json.loads(l)
                    for f in events_dir.glob("*.jsonl")
                    for l in f.read_text().splitlines()
                    if l.strip()
                ]
                if any(l.get("skill_id") == "os.delegation_router" for l in lines):
                    break
                time.sleep(0.05)
            hits = [l for l in lines if l.get("skill_id") == "os.delegation_router"]
            self.assertTrue(hits, f"no learning event persisted under {events_dir}")
            self.assertEqual(hits[-1]["event_type"], "skill_executed")
            self.assertEqual(hits[-1]["tenant_id"], tid)
            self.assertEqual(emitter.dropped, 0)
            self.assertEqual(emitter.write_failures, 0)
            self.assertEqual(registry.learning_emit_failures, 0)


if __name__ == "__main__":
    unittest.main()
