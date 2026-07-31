"""adapter.py must be ensured running on every corvin-serve boot (2026-07-31).

Root cause, live-reported: "Discord receives messages but never replies."
adapter.py is the ONLY process that reads the shared inbox and writes
replies to the shared outbox -- the Node bridge daemons (discord/whatsapp/
telegram/...) only relay messages in and out of those directories and need
no Python at all. Nothing in corvin-serve's boot path (ops/launcher/corvin/
serve_backend.py -> uvicorn corvin_console.standalone:create_app) ever
started adapter.py -- the only trigger was a console button click
(routes/setup.py's "Start WhatsApp bridge") or a bridge-settings save
(routes/bridges.py::_apply_runtime_change's win32 branch, 0.10.82). So once
the adapter process died for ANY reason (crash, the self-updater's own
relaunch replacing corvin-serve's process tree, a manual kill, machine
sleep/wake) nothing ever brought it back: a stale adapter.pid pointed at a
PID that no longer existed, inbound messages kept arriving, and every reply
silently never got written to the outbox -- with zero error in any log,
because nothing was there to log one.

Fixed by calling bridge_manager.ensure_adapter_detached() (idempotent,
cmdline-verified) once during standalone.py's lifespan startup, so every
boot -- including a post-update relaunch -- restores the adapter if it is
not already alive.

Run: python3 -m pytest core/console/tests/test_adapter_ensured_at_boot.py
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


class AdapterEnsuredAtBootTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-adapter-boot-test-")
        self.addCleanup(self._tmp.cleanup)
        import os
        self._orig_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        self.addCleanup(self._restore_home)

        # Pre-install a fake bridge_manager module in sys.modules so the
        # lifespan's dynamic `import bridge_manager` picks up our mock
        # instead of doing real subprocess/filesystem work.
        self._orig_module = sys.modules.get("bridge_manager")
        self.fake_bm = types.ModuleType("bridge_manager")
        self.fake_bm.ensure_adapter_detached = MagicMock(
            return_value={"ok": True, "pid": 4242},
        )
        sys.modules["bridge_manager"] = self.fake_bm
        self.addCleanup(self._restore_module)

    def _restore_home(self) -> None:
        import os
        if self._orig_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._orig_home

    def _restore_module(self) -> None:
        if self._orig_module is not None:
            sys.modules["bridge_manager"] = self._orig_module
        else:
            sys.modules.pop("bridge_manager", None)

    def test_ensure_adapter_detached_is_called_on_boot(self) -> None:
        from fastapi.testclient import TestClient
        from corvin_console.standalone import create_app

        app = create_app()
        with TestClient(app):
            pass  # __enter__/__exit__ drive the real lifespan startup/shutdown

        self.fake_bm.ensure_adapter_detached.assert_called_once()

    def test_adapter_ensure_failure_does_not_block_boot(self) -> None:
        """A failing/missing bridge_manager must never prevent the console
        itself from booting -- best-effort, same as every other startup
        hook in this lifespan (heartbeat, voice migration, A2A relay)."""
        self.fake_bm.ensure_adapter_detached = MagicMock(
            side_effect=RuntimeError("boom"),
        )
        from fastapi.testclient import TestClient
        from corvin_console.standalone import create_app

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/")
            # Not 500: the app booted and is serving requests. The exact
            # status (redirect to login, 403 without a session, ...) is
            # irrelevant here -- only "did startup crash" is under test.
            self.assertLess(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
