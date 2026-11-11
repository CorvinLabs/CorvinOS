"""REVIEW-0063 m11 - one tenant cannot fill the host-wide import staging budget for every other tenant (ADR-2323 Decision 13).

The budgets are scaled down (same ratios: host budget, per-tenant share = half of it) so the test writes kilobytes, not GiB.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "e2e"))
from _projects_fixtures import Sandbox  # noqa: E402

CHUNK = b"x" * 3000


class StagingShare(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        from corvin_console import projects_service as svc
        self.svc = svc
        self._p = mock.patch.object(svc, "IMPORT_HOST_BUDGET_BYTES", 10_000)
        self._p.start()
        self.handles = []

    def tearDown(self):
        for h in self.handles:
            self.svc.staging_abort(h)
        self._p.stop()
        self.sb.close()

    def stage(self, tenant: str, n_chunks: int = 1):
        h = self.svc.staging_open(tenant, declared=len(CHUNK) * n_chunks)
        self.handles.append(h)
        for _ in range(n_chunks):
            self.svc.staging_write(h, CHUNK)
        self.svc.staging_close(h)
        return h

    def test_one_tenant_is_capped_at_its_share_and_another_tenant_still_stages(self):
        self.stage("_default")
        refused = 0
        for _ in range(4):
            try:
                self.stage("_default")
            except self.svc.StagingFull:
                refused += 1
        self.assertGreaterEqual(refused, 3, "a single tenant took more than its share")
        self.assertLessEqual(self.svc._tenant_staging_bytes("_default"), self.svc._tenant_share())
        self.stage("acme")                                          # the other tenant is not starved
        self.assertLessEqual(self.svc._host_staging_bytes(), self.svc.IMPORT_HOST_BUDGET_BYTES)
        self.assertGreater(self.svc._tenant_staging_bytes("acme"), 0)

    def test_the_share_is_half_the_host_budget_and_fits_one_full_upload(self):
        with mock.patch.object(self.svc, "IMPORT_HOST_BUDGET_BYTES", 2 << 30):
            self.assertEqual(self.svc._tenant_share(), 1 << 30)
            self.assertGreaterEqual(self.svc._tenant_share(), self.svc.IMPORT_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
