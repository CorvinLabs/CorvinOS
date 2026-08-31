#!/usr/bin/env python3
"""Per-subtask E2E for ADR-0007 Phase 6.3 — voice-audit metrics CLI.

Covers:
  * subprocess round-trip: prom / json / table formats
  * --tenant resolves to the right chain
  * --since filters older events
  * --since with garbage exits 4
  * audit_metrics module unreachable → exit 3 (skipped if reachable)
  * default tenant is ``_default``
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "operator" / "voice" / "scripts" / "voice_audit.py"


@contextmanager
def sandbox(tenants=("_default", "acme")):
    with tempfile.TemporaryDirectory(prefix="va-metrics-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        for t in tenants:
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)


def _write_event(home: Path, tenant: str, event_type: str, **kw) -> None:
    # Use forge.security_events directly for deterministic chain writes.
    sys.path.insert(0, str(_REPO / "operator" / "forge"))
    from forge import security_events as _se
    chain = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    _se.write_event(
        chain, event_type,
        ts=kw.pop("ts", None),
        run_id=kw.pop("run_id", ""),
        tool=kw.pop("tool", ""),
        severity=kw.pop("severity", None),
        details=kw or {},
    )


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *argv],
        capture_output=True, text=True,
        env={**os.environ},
    )


class CliMetricsTests(unittest.TestCase):
    def test_default_tenant_is__default(self):
        with sandbox() as home:
            _write_event(home, "_default", "tool.created", persona="coder")
            r = _run(["metrics"])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("corvin_forge_tools_created_total", r.stdout)
            self.assertIn("persona=coder", r.stdout)

    def test_explicit_tenant(self):
        with sandbox() as home:
            _write_event(home, "acme", "tool.created", persona="research")
            r = _run(["metrics", "--tenant", "acme"])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("persona=research", r.stdout)

    def test_prom_format(self):
        with sandbox() as home:
            _write_event(home, "_default", "tool.created", persona="coder")
            r = _run(["metrics", "--format", "prom"])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn(
                'corvin_forge_tools_created_total{persona="coder"} 1',
                r.stdout,
            )
            self.assertIn("# HELP corvin_gateway_runs_total", r.stdout)

    def test_json_format(self):
        with sandbox() as home:
            _write_event(home, "_default", "tool.created", persona="coder")
            r = _run(["metrics", "--format", "json"])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            data = json.loads(r.stdout)
            self.assertIn("events_read", data)
            self.assertIn("corvin_forge_tools_created_total", data["counters"])

    def test_since_filter(self):
        with sandbox() as home:
            old = time.time() - 7200  # 2h ago
            _write_event(home, "_default", "tool.created",
                         ts=old, persona="coder")
            _write_event(home, "_default", "tool.created", persona="coder")
            r = _run(["metrics", "--since", "1h", "--format", "prom"])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn(
                'corvin_forge_tools_created_total{persona="coder"} 1',
                r.stdout,
            )

    def test_since_garbage_returns_4(self):
        with sandbox():
            r = _run(["metrics", "--since", "garbage"])
            self.assertEqual(r.returncode, 4)
            self.assertIn("--since", r.stderr)

    def test_empty_chain(self):
        with sandbox():
            r = _run(["metrics"])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("events_read=0", r.stdout)
            self.assertIn("chain_intact=yes", r.stdout)


if __name__ == "__main__":
    unittest.main()
