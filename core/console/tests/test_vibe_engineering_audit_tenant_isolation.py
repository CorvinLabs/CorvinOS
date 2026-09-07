"""Regression — GET /vibe-engineering/audit is tenant-scoped and CORVIN_HOME-aware.

Round-4 adversarial review, F2. The handler used to read a hard-wired
``Path.home()/".corvin"/"audit.jsonl"`` — a SHARED file holding every tenant's
events — and stamp ``"tenant_id": rec.tenant_id`` onto every record it returned.
So tenant A received tenant B's audit events, mislabelled as A's own, and
``CORVIN_HOME`` was ignored entirely. CLAUDE.md § Audit Chain as Ground Truth:
"audit reads MUST filter by tenant_id; no fallback to 'any tenant'".

It also emitted ``lom_hash = sha256(<the LoM label>)[:16]``, computed at read
time — under the field name CLAUDE.md cites as the ADR-0537 anti-spoofing SOURCE
binding, and not the value the writer stamped.

Driven through the REAL FastAPI router with TestClient; only the session
dependency is overridden.

Run: .venv/bin/python -m pytest -q core/console/tests/test_vibe_engineering_audit_tenant_isolation.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import vibe_engineering as V  # noqa: E402


def _fake_record(tenant_id: str) -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


def _event(event_id, tenant_id, *, ts="2026-09-07T12:00:00", details=None, etype="skill_executed"):
    body = {
        "event_id": event_id,
        "event_type": etype,
        "timestamp": ts,
        "hash": "a" * 64,
        "prev_hash": "b" * 64,
        "details": details if details is not None else {},
        "severity": "INFO",
    }
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    return json.dumps(body)


class AuditTenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.home = Path(self.td.name) / "corvin_home"
        # No patching of the resolver: CORVIN_HOME is the real knob, and this
        # test fails if the handler reads Path.home() instead.
        self.env = patch.dict(os.environ, {"CORVIN_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _client(self, tenant_id):
        app = FastAPI()
        app.include_router(V.router)
        rec = _fake_record(tenant_id)
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        return TestClient(app)

    def _write_chain(self, tenant, *lines):
        d = self.home / "tenants" / tenant / "global" / "forge"
        d.mkdir(parents=True, exist_ok=True)
        (d / "audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── the leak ──────────────────────────────────────────────────────────────

    def test_other_tenants_events_are_not_returned(self):
        """A record naming another tenant is DROPPED, never relabelled."""
        # Both tenants' events land in tenant A's own chain file — the exact
        # shape of the shared ~/.corvin/audit.jsonl the handler used to read.
        self._write_chain(
            "tenant_a",
            _event("mine", "tenant_a"),
            _event("theirs", "tenant_b", details={"secret": "tenant-b-only"}),
        )
        body = self._client("tenant_a").get("/vibe-engineering/audit").json()
        ids = [e["id"] for e in body["events"]]
        self.assertEqual(ids, ["mine"], "tenant A must not see tenant B's events")
        self.assertNotIn("tenant-b-only", json.dumps(body))
        # And the surviving record keeps its OWN tenant_id.
        self.assertEqual(body["events"][0]["tenant_id"], "tenant_a")

    def test_tenant_id_is_never_overwritten(self):
        """The handler must not stamp the caller's tenant onto a foreign record."""
        self._write_chain("tenant_a", _event("theirs", "tenant_b"))
        body = self._client("tenant_a").get("/vibe-engineering/audit").json()
        self.assertEqual(body["events"], [])
        self.assertEqual(body["graph"]["metadata"]["chainHeight"], 0)

    def test_reads_the_callers_chain_not_another_tenants_file(self):
        """Tenant B's separate chain file is never opened by a tenant-A caller."""
        self._write_chain("tenant_b", _event("b-only", "tenant_b"))
        body = self._client("tenant_a").get("/vibe-engineering/audit").json()
        self.assertEqual(body["events"], [])

    def test_path_honours_corvin_home(self):
        """Proof the resolver is used: the chain under CORVIN_HOME is found."""
        self.assertEqual(
            V._audit_path("tenant_a"),
            self.home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl",
        )
        self._write_chain("tenant_a", _event("here", "tenant_a"))
        body = self._client("tenant_a").get("/vibe-engineering/audit").json()
        self.assertEqual([e["id"] for e in body["events"]], ["here"])

    def test_untenanted_record_in_own_chain_is_attributed_to_that_tenant(self):
        self._write_chain("tenant_a", _event("sys", None))
        body = self._client("tenant_a").get("/vibe-engineering/audit").json()
        self.assertEqual(body["events"][0]["tenant_id"], "tenant_a")

    # ── the fabricated lom_hash ───────────────────────────────────────────────

    def test_lom_hash_is_the_recorded_one_not_a_read_time_hash(self):
        self._write_chain(
            "tenant_a",
            _event("with-hash", "tenant_a",
                   details={"lom": "core/skills/boot.py:boot_skills",
                            "lom_hash": "deadbeef" * 8}),
        )
        evt = self._client("tenant_a").get("/vibe-engineering/audit").json()["events"][0]
        self.assertEqual(evt["lom_hash"], "deadbeef" * 8)
        self.assertEqual(evt["lom"], "core/skills/boot.py:boot_skills")

    def test_missing_lom_hash_reports_empty_not_a_fabricated_one(self):
        """A record the writer never bound must not grow a binding at read time."""
        label = "/some/path.py:some_function:1"
        self._write_chain("tenant_a",
                          _event("no-hash", "tenant_a", details={"lom_audit_write": label}))
        evt = self._client("tenant_a").get("/vibe-engineering/audit").json()["events"][0]
        self.assertEqual(evt["lom_hash"], "", "read-time sha256(label) is not a source binding")
        self.assertEqual(evt["lom"], label, "the raw label is still reported, as a label")

    # ── the silently-ignored query params ─────────────────────────────────────

    def test_since_until_filter_is_applied(self):
        self._write_chain(
            "tenant_a",
            _event("old", "tenant_a", ts="2026-01-01T00:00:00"),
            _event("new", "tenant_a", ts="2026-09-07T00:00:00"),
        )
        c = self._client("tenant_a")
        got = c.get("/vibe-engineering/audit?since=2026-06-01T00:00:00").json()["events"]
        self.assertEqual([e["id"] for e in got], ["new"])
        got = c.get("/vibe-engineering/audit?until=2026-06-01T00:00:00").json()["events"]
        self.assertEqual([e["id"] for e in got], ["old"])

    def test_skill_filter_accepts_the_camelcase_name_the_spa_sends(self):
        """useAuditQuery.ts appends `skillIds`, not `skill_ids`."""
        self._write_chain(
            "tenant_a",
            _event("router", "tenant_a", details={"skill_id": "os.delegation_router"}),
            _event("other", "tenant_a", details={"skill_id": "os.capabilities"}),
        )
        c = self._client("tenant_a")
        for param in ("skillIds", "skill_ids"):
            got = c.get(f"/vibe-engineering/audit?{param}=os.delegation_router").json()["events"]
            self.assertEqual([e["id"] for e in got], ["router"], param)


if __name__ == "__main__":
    unittest.main()
