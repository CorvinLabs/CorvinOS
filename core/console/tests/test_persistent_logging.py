"""Persistent console logging regression test (2026-07-30).

Root cause: `corvin-serve` launches the console via `uvicorn corvin_console.
standalone:create_app --factory` (ops/launcher/corvin/serve_backend.py::
start()) — a path that never reaches standalone.py's `if __name__ ==
"__main__":` block, so its `logging.basicConfig()` call never actually runs
in production. On the Windows autostart path (install.ps1's hidden
Scheduled Task supervisor), the process also has no attached console at
all. Result: every log.warning()/log.error() anywhere in the console —
including bridges.py's Discord/Telegram token-validation failure logging —
went to a stderr nothing was reading, so a real reported bug (a 500 on
Discord bot-token validation) had NO visible cause short of stopping the
background service and re-running in a foreground terminal.

Run: python3 -m pytest core/console/tests/test_persistent_logging.py
"""
from __future__ import annotations

import logging
import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CONSOLE_PARENT = _HERE.parent  # core/console
if str(_CONSOLE_PARENT) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PARENT))


class PersistentLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-log-test-")
        self._orig_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        # Each test gets a clean root-logger handler set: the module-level
        # idempotency marker would otherwise skip re-configuration across
        # tests sharing the same process.
        self._orig_handlers = list(logging.getLogger().handlers)
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_corvin_console_log", False):
                logging.getLogger().removeHandler(h)

    def tearDown(self) -> None:
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_corvin_console_log", False):
                h.close()
                logging.getLogger().removeHandler(h)
        for h in self._orig_handlers:
            if h not in logging.getLogger().handlers:
                logging.getLogger().addHandler(h)
        if self._orig_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._orig_home
        self._tmp.cleanup()

    def test_log_file_created_and_captures_error_from_any_module(self) -> None:
        """The exact scenario that motivated this fix: an error logged from
        a route module (bridges.py's logger name pattern) must reach the
        file, not just standalone.py's own logger."""
        from corvin_console.standalone import _configure_persistent_logging

        _configure_persistent_logging()

        log_path = Path(self._tmp.name) / "logs" / "console.log"
        self.assertTrue(log_path.exists(), "console.log was not created")

        route_logger = logging.getLogger("corvin_console.routes.bridges")
        route_logger.error("Token validation error: test-marker-xyz")

        content = log_path.read_text(encoding="utf-8")
        self.assertIn("test-marker-xyz", content)
        self.assertIn("ERROR", content)
        self.assertIn("corvin_console.routes.bridges", content)

    def test_idempotent_does_not_add_duplicate_handlers(self) -> None:
        from corvin_console.standalone import _configure_persistent_logging

        _configure_persistent_logging()
        _configure_persistent_logging()
        _configure_persistent_logging()

        markers = [h for h in logging.getLogger().handlers
                   if getattr(h, "_corvin_console_log", False)]
        self.assertEqual(len(markers), 1)

    def test_never_raises_when_corvin_home_unwritable(self) -> None:
        """Logging setup is best-effort: a failure here must never block
        console startup."""
        from corvin_console.standalone import _configure_persistent_logging

        os.environ["CORVIN_HOME"] = "/nonexistent-nope/deeply/nested/path/xyz"
        try:
            _configure_persistent_logging()  # must not raise
        except Exception as exc:  # noqa: BLE001
            self.fail(f"_configure_persistent_logging raised: {exc!r}")

    def test_create_app_wires_logging_before_anything_else(self) -> None:
        """End-to-end: calling create_app() (the real uvicorn --factory
        entry point) alone -- no manual _configure_persistent_logging()
        call -- must produce a working log file."""
        from corvin_console.standalone import create_app

        create_app()

        log_path = Path(self._tmp.name) / "logs" / "console.log"
        self.assertTrue(log_path.exists())
        self.assertIn("persistent console log", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
