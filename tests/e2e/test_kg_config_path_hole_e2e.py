"""T-0345 / ADR-2316 Decision 7 — the Knowledge Graph config is per tenant and never takes an
arbitrary path from the client.

Before the fix (measured 2026-11-08 on 5d0cd6471):
* ``effective_config()`` started from ONE file under the OS user's home,
  ``~/.claude/plugins/corvin-knowledge.json``, for EVERY tenant, so a tenant without its own
  registry entry inherited the operator's ``repo_path`` and ``remote_url`` (sandbox finding S-1b);
* ``POST /config`` stored any client-supplied ``repo_path`` (no allowlist) and wrote it into
  that shared file, and ``/init`` / ``/cleanup`` reset or deleted it from any tenant.

Driven through the real console router with real sessions for two tenants (``_sandbox``).
The shared file is redirected into the sandbox (``CONFIG_FILE`` is bound at import), and the
operator's live file is compared byte for byte before and after.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS = Path(__file__).resolve().parents[2] / "core" / "console" / "tests"
sys.path.insert(0, str(_TESTS))
from test_admin_route import _sandbox  # noqa: E402

_URL = "/v1/console/plugins/corvin-knowledge"
_MARKER = "kgholemarker7d1"
_LIVE = Path.home() / ".claude" / "plugins" / "corvin-knowledge.json"


def _make_kb(root: Path) -> Path:
    """A directory that looks like a knowledge-base checkout (``kb/_meta/sources.yaml``)."""
    (root / "kb" / "_meta").mkdir(parents=True, exist_ok=True)
    (root / "kb" / "_meta" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (root / "kb" / "graph").mkdir(parents=True, exist_ok=True)
    return root


class KgConfigPathHoleE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.live_before = _LIVE.read_bytes() if _LIVE.exists() else None
        self.cfg = self.tmp / "oshome" / ".claude" / "plugins" / "corvin-knowledge.json"
        self.foreign_kb = _make_kb(self.tmp / "foreign-kb")

    def tearDown(self):
        self.assertEqual(_LIVE.read_bytes() if _LIVE.exists() else None, self.live_before,
                         "the operator's live corvin-knowledge.json changed")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _plant_operator_file(self):
        self.cfg.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.write_text(json.dumps({"repo_path": str(self.foreign_kb),
                                        "remote_url": f"https://example.invalid/{_MARKER}/kb",
                                        "auto_sync_on_query": True, "consistency_level": "warn"}),
                            encoding="utf-8")

    def _run(self, fn):
        with _sandbox(self.tmp / "s", tenants=("_default", "acme")) as (_c, _t, home, clients):
            from corvin_console.routes import plugins_corvin_knowledge_api as kg
            with mock.patch.object(kg, "CONFIG_FILE", self.cfg):
                return fn(clients, home, kg)

    # ── a foreign tenant ──────────────────────────────────────────────────────

    def test_foreign_tenant_does_not_inherit_the_operator_file(self):
        self._plant_operator_file()

        def go(clients, home, kg):
            client, _ = clients["acme"]
            r = client.get(f"{_URL}/config")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn(_MARKER, r.text)
            self.assertNotIn(str(self.foreign_kb), r.text)
            self.assertEqual(r.json()["repo_path"], str(home / "tenants" / "acme" / "knowledge"))
            # the graph and doc routes read the same (tenant's own) path
            self.assertEqual(client.get(f"{_URL}/graph").status_code, 200)
        self._run(go)

    def test_foreign_tenant_cannot_set_an_arbitrary_path(self):
        def go(clients, home, kg):
            client, csrf = clients["acme"]
            for bad in (str(self.foreign_kb), "/etc", str(self.tmp / "missing"),
                        str(home / "tenants" / "_default" / "knowledge")):
                r = client.post(f"{_URL}/config", json={"repo_path": bad}, headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.status_code, 422, f"{bad}: {r.status_code} {r.text}")
            self.assertFalse(self.cfg.exists(), "a refused POST wrote the shared file")
            self.assertEqual(client.get(f"{_URL}/config").json()["repo_path"],
                             str(home / "tenants" / "acme" / "knowledge"))
        self._run(go)

    def test_foreign_tenant_never_writes_or_deletes_the_operator_file(self):
        self._plant_operator_file()
        before = self.cfg.read_bytes()

        def go(clients, home, kg):
            client, csrf = clients["acme"]
            r = client.post(f"{_URL}/config", json={"consistency_level": "strict"}, headers={"X-CSRF-Token": csrf})
            self.assertIn(r.status_code, (200, 409), r.text)   # 409: no registry record to store it in
            self.assertEqual(client.post(f"{_URL}/init", headers={"X-CSRF-Token": csrf}).status_code, 200)
            self.assertEqual(client.post(f"{_URL}/cleanup", headers={"X-CSRF-Token": csrf}).status_code, 200)
        self._run(go)
        self.assertEqual(self.cfg.read_bytes(), before, "a foreign tenant changed the operator's file")

    # ── the operator's own tenant ─────────────────────────────────────────────

    def test_operator_tenant_keeps_its_explicit_kb(self):
        self._plant_operator_file()

        def go(clients, home, kg):
            client, _ = clients["_default"]
            r = client.get(f"{_URL}/config")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["repo_path"], str(self.foreign_kb))   # live workflow preserved
        self._run(go)

    def test_operator_tenant_path_must_be_a_kb_outside_other_tenants(self):
        def go(clients, home, kg):
            client, csrf = clients["_default"]
            other = _make_kb(home / "tenants" / "acme" / "knowledge")
            not_kb = self.tmp / "plain-dir"
            not_kb.mkdir()
            for bad in (str(not_kb), str(other), str(self.tmp / "missing")):
                r = client.post(f"{_URL}/config", json={"repo_path": bad}, headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.status_code, 422, f"{bad}: {r.status_code} {r.text}")
            self.assertFalse(self.cfg.exists())
            # a symlink into another tenant's home is the same refusal (resolved first)
            link = self.tmp / "sneaky"
            link.symlink_to(other, target_is_directory=True)
            r = client.post(f"{_URL}/config", json={"repo_path": str(link)}, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 422, r.text)
            # positive control: a real KB checkout outside every tenant home is accepted
            r = client.post(f"{_URL}/config", json={"repo_path": str(self.foreign_kb)}, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["repo_path"], str(self.foreign_kb))
        self._run(go)

    def test_a_stored_path_that_is_no_longer_allowed_falls_back_to_the_bound_kb(self):
        def go(clients, home, kg):
            other = _make_kb(home / "tenants" / "acme" / "knowledge")
            self.cfg.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.write_text(json.dumps({"repo_path": str(other)}), encoding="utf-8")
            client, _ = clients["_default"]
            got = client.get(f"{_URL}/config").json()["repo_path"]
            self.assertNotEqual(got, str(other))
        self._run(go)


if __name__ == "__main__":
    unittest.main()
