"""The Data Hub learning host is started by the shipped console host's lifespan and stopped (state saved)
on shutdown - the production entry point, not a direct call (e2e-wiring-proof)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


class LearningHostBootTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-lh-boot-")
        self.addCleanup(self._tmp.cleanup)
        self._prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_LEARNING_HOST_IN_TESTS")}
        os.environ["CORVIN_HOME"] = self._tmp.name
        os.environ["CORVIN_LEARNING_HOST_IN_TESTS"] = "1"
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_standalone_lifespan_starts_and_stops_the_host(self):
        from fastapi.testclient import TestClient

        from core.background import learning_host
        from corvin_console.standalone import create_app

        tenant = os.environ.get("CORVIN_TENANT_ID") or "_default"
        with TestClient(create_app()):
            st = learning_host.status(tenant)
            self.assertIsNotNone(st, "the console lifespan did not start the learning host")
            self.assertTrue(st["alive"])
        self.assertIsNone(learning_host.status(tenant), "shutdown did not stop the host")
        self.assertTrue(learning_host.state_path(tenant).is_file(), "shutdown did not persist state")


if __name__ == "__main__":
    unittest.main()
