"""Unit tests for audit_rotate.py (M3.7 — L37 daily rotation timer).

Run with::

    python3 operator/voice/scripts/test_audit_rotate.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "voice" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))

import audit_rotate  # noqa: E402


def _entry(prev: str, idx: int) -> dict:
    rec = {
        "ts": 1_700_000_000.0 + idx,
        "event_type": f"test.event_{idx}",
        "severity": "INFO",
        "run_id": "",
        "tool": "test",
        "details": {},
        "prev_hash": prev,
    }
    canonical = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(prev.encode("utf-8"))
    h.update(b"\n")
    h.update(canonical.encode("utf-8"))
    rec["hash"] = h.hexdigest()[:16]
    return rec


def _make_tenant(root: Path, tenant_id: str, *,
                 chain_entries: int = 3,
                 yaml_content: str | None = None) -> Path:
    """Build a tenant tree with a fake audit chain and optional yaml."""
    tenant = root / "tenants" / tenant_id / "global"
    tenant.mkdir(parents=True)
    audit_dir = tenant / "forge"
    audit_dir.mkdir()
    audit = audit_dir / "audit.jsonl"
    prev = ""
    with audit.open("w") as fh:
        for i in range(chain_entries):
            rec = _entry(prev, i)
            fh.write(json.dumps(rec) + "\n")
            prev = rec["hash"]
    if yaml_content is not None:
        (tenant / "tenant.corvin.yaml").write_text(yaml_content)
    return tenant


class TestListTenants(unittest.TestCase):

    def test_lists_multiple_tenants(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tenant(root, "_default")
            _make_tenant(root, "acme")
            tenants = audit_rotate._list_tenants(root)
            self.assertEqual(tenants, ["_default", "acme"])

    def test_empty_when_no_tenants_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(audit_rotate._list_tenants(root), [])

    def test_legacy_single_tenant_layout(self):
        """If only `global/` exists (no tenants/), treat as _default."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "global").mkdir()
            self.assertEqual(audit_rotate._list_tenants(root), ["_default"])


class TestTenantPaths(unittest.TestCase):

    def test_multi_tenant_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tenants" / "acme" / "global").mkdir(parents=True)
            audit_p, yaml_p = audit_rotate._tenant_paths(root, "acme")
            self.assertEqual(audit_p, root / "tenants" / "acme" / "global" / "forge" / "audit.jsonl")
            self.assertEqual(yaml_p, root / "tenants" / "acme" / "global" / "tenant.corvin.yaml")

    def test_legacy_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "global").mkdir()
            audit_p, _ = audit_rotate._tenant_paths(root, "_default")
            # legacy layout falls through to root/global/...
            self.assertEqual(audit_p, root / "global" / "forge" / "audit.jsonl")


class TestProcessTenant(unittest.TestCase):

    def test_under_thresholds_no_rotate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tenant(root, "_default")
            with redirect_stdout(io.StringIO()) as out:
                rc = audit_rotate.process_tenant("_default", root,
                                                 dry_run=False)
            self.assertEqual(rc, 0)
            self.assertIn("under thresholds", out.getvalue())

    def test_size_trigger_rotates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Tiny max_size_mb makes the existing chain trigger rotation.
            _make_tenant(root, "_default",
                         yaml_content="""
spec:
  audit:
    rotation:
      max_size_mb: 0.0000001
      max_age_days: 365
""")
            with redirect_stdout(io.StringIO()) as out:
                rc = audit_rotate.process_tenant("_default", root,
                                                 dry_run=False)
            self.assertEqual(rc, 0)
            self.assertIn("rotated", out.getvalue())
            # New audit.jsonl starts with audit.rotation_link; ADR-0135 M2
            # may also append audit.chain_anchor_written immediately after.
            audit_p, _ = audit_rotate._tenant_paths(root, "_default")
            lines = audit_p.read_text().splitlines()
            self.assertGreaterEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["event_type"],
                             "audit.rotation_link")

    def test_dry_run_does_not_rotate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tenant(root, "_default", yaml_content="""
spec:
  audit:
    rotation:
      max_size_mb: 0.0000001
""")
            audit_p, _ = audit_rotate._tenant_paths(root, "_default")
            content_before = audit_p.read_text()
            with redirect_stdout(io.StringIO()) as out:
                rc = audit_rotate.process_tenant("_default", root,
                                                 dry_run=True)
            self.assertEqual(rc, 0)
            self.assertIn("DRY-RUN", out.getvalue())
            self.assertEqual(audit_p.read_text(), content_before,
                             "dry-run modified the audit file")

    def test_missing_audit_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tenant = root / "tenants" / "empty" / "global"
            tenant.mkdir(parents=True)
            with redirect_stdout(io.StringIO()) as out:
                rc = audit_rotate.process_tenant("empty", root,
                                                 dry_run=False)
            self.assertEqual(rc, 0)
            self.assertIn("skip", out.getvalue())

    def test_malformed_config_returns_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tenant(root, "_default", yaml_content="""
spec:
  audit:
    encryption_at_rest:
      enabled: true
      recipient: ""  # invalid: requires non-empty recipient
""")
            with redirect_stdout(io.StringIO()), \
                 redirect_stderr(io.StringIO()) as err:
                rc = audit_rotate.process_tenant("_default", root,
                                                 dry_run=False)
            self.assertEqual(rc, 1)
            self.assertIn("config error", err.getvalue())


class TestMainCLI(unittest.TestCase):

    def test_processes_all_tenants(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tenant(root, "_default")
            _make_tenant(root, "acme")
            with redirect_stdout(io.StringIO()) as out:
                rc = audit_rotate.main(["--home", str(root), "--dry-run"])
            self.assertEqual(rc, 0)
            output = out.getvalue()
            self.assertIn("[_default]", output)
            self.assertIn("[acme]", output)
            self.assertIn("tenants=2", output)

    def test_single_tenant_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tenant(root, "_default")
            _make_tenant(root, "acme")
            with redirect_stdout(io.StringIO()) as out:
                rc = audit_rotate.main(["--home", str(root),
                                        "--tenant", "acme", "--dry-run"])
            self.assertEqual(rc, 0)
            output = out.getvalue()
            self.assertIn("[acme]", output)
            self.assertNotIn("[_default]", output)

    def test_missing_home_returns_2(self):
        with redirect_stderr(io.StringIO()) as err:
            rc = audit_rotate.main(["--home", "/tmp/totally-nonexistent-xyz",
                                    "--dry-run"])
        self.assertEqual(rc, 2)
        self.assertIn("not found", err.getvalue())

    def test_no_tenants_returns_0(self):
        with tempfile.TemporaryDirectory() as td:
            with redirect_stdout(io.StringIO()) as out:
                rc = audit_rotate.main(["--home", td, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertIn("no tenants", out.getvalue())


if __name__ == "__main__":
    unittest.main()
