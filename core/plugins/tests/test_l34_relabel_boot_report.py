"""Boot report for rejected L34 engine relabels (T-0226, ADR-2268).

A tenant whose ``engine_compliance`` relabels a bundled US-cloud engine as a safer place is
refused by L34 on every spawn. ``boot_platform`` now names that cause once, at boot, in the
log and in the hash chain (``data_flow.relabel_rejected``). The report is read-only and never
blocks a boot. These tests read a real ``tenant.corvin.yaml`` under a sandboxed ``CORVIN_HOME``
and check that ``boot_platform`` really calls the report, by AST, not by substring.
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in ("core/plugins", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    if str(_REPO / _p) not in sys.path:
        sys.path.insert(0, str(_REPO / _p))

from corvin_plugins import bootstrap  # noqa: E402

BAD = (
    "spec:\n  data_classification:\n    engine_compliance:\n"
    "      - engine_id: acs_worker\n        locality: local\n        network_egress: none\n"
    "      - engine_id: copilot\n        locality: eu_cloud\n"
)
CLEAN = (
    "spec:\n  data_classification:\n    engine_compliance:\n"
    "      - engine_id: copilot\n        locality: eu_cloud\n"
)


class RelabelBootReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        self.addCleanup(self._restore)
        self.cfg = Path(self._tmp.name) / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
        self.cfg.parent.mkdir(parents=True)
        self.events: list[tuple[str, str, dict]] = []
        self._orig = bootstrap._audit_degradation
        bootstrap._audit_degradation = lambda tid, et, details: self.events.append((tid, et, dict(details)))
        self.addCleanup(setattr, bootstrap, "_audit_degradation", self._orig)

    def _restore(self) -> None:
        if self._prev is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev

    def test_a_rejected_relabel_is_named_with_ids_and_fields_only(self) -> None:
        self.cfg.write_text(BAD, encoding="utf-8")
        found = bootstrap._report_l34_relabels("acme")
        self.assertEqual(sorted((v["engine_id"], v["field"], v["value"]) for v in found),
                         [("acs_worker", "locality", "local"), ("acs_worker", "network_egress", "none")])
        self.assertEqual([e[1] for e in self.events], ["data_flow.relabel_rejected"] * 2)
        for _tid, _et, details in self.events:
            self.assertEqual(set(details), {"tenant_id", "engine_id", "field", "value"})
            self.assertEqual(details["tenant_id"], "acme")

    def test_a_clean_config_and_a_missing_config_report_nothing(self) -> None:
        self.cfg.write_text(CLEAN, encoding="utf-8")
        self.assertEqual(bootstrap._report_l34_relabels("acme"), [])
        self.cfg.unlink()
        self.assertEqual(bootstrap._report_l34_relabels("acme"), [])
        self.assertEqual(self.events, [])

    def test_an_unparseable_config_reports_nothing_and_does_not_raise(self) -> None:
        self.cfg.write_text("spec: [unclosed\n  : : :\n", encoding="utf-8")
        self.assertEqual(bootstrap._report_l34_relabels("acme"), [])

    def test_a_path_traversing_tenant_id_reads_nothing(self) -> None:
        self.assertEqual(bootstrap._report_l34_relabels("../../etc"), [])

    def test_boot_platform_calls_the_report_after_the_tripwire(self) -> None:
        tree = ast.parse(Path(bootstrap.__file__).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "boot_platform")
        calls = sorted(
            (n.lineno, n.func.id) for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in {"assert_compliance", "_report_l34_relabels"}
        )
        names = [name for _line, name in calls]
        self.assertIn("_report_l34_relabels", names, "boot_platform never calls the relabel report")
        self.assertLess(names.index("assert_compliance"), names.index("_report_l34_relabels"),
                        "the report must run after the fail-closed tripwire")


if __name__ == "__main__":
    unittest.main()
