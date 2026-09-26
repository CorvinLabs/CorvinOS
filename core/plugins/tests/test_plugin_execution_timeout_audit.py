"""plugin.execution_timeout reaches THE tenant chain (ADR-2043, Wave 1c T09).

The event was registered in EVENT_SEVERITY / _EVENT_ALLOWLIST and had an
emitter (``corvin_plugins.audit.emit_execution_timeout``) that nothing called.
The two real deadline sites are in ``registry.py``: ``on_load``
(LOAD_DEADLINE_S, via ``bootstrap._register_instance``) and ``health_check``
(HEALTH_CHECK_DEADLINE_S, via ``health_check_all`` — what every
``GET /api/admin/*`` runs). Both are driven here through the real registry
dispatch, and the record is read back from the chain file with the hash chain
verified.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_PKG = _HERE.parent
for _p in (
    str(_PKG),
    str(_REPO / "core" / "compliance"),
    str(_REPO / "corvin_operator" / "forge"),
    str(_REPO / "corvin_operator" / "bridges" / "shared"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.append(_p)

import audit as _audit  # type: ignore[import-not-found]  # noqa: E402
from corvin_plugins import bootstrap, registry  # noqa: E402
from corvin_plugins.protocol import HealthStatus  # noqa: E402

_EXPECTED_KEYS = {"plugin_id", "boot_layer", "timeout_ms", "tenant_id"}


class _Plugin:
    plugin_type = "notification_backend"
    version = "1.0"

    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id

    def on_load(self, ctx):
        pass

    def on_unload(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True)


class TestPluginExecutionTimeoutAudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        (self.home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        self._prev = {k: os.environ.get(k) for k in ("VOICE_AUDIT_PATH", "CORVIN_HOME", "CORVIN_TENANT_ID")}
        os.environ.pop("VOICE_AUDIT_PATH", None)
        os.environ["CORVIN_HOME"] = str(self.home)
        os.environ.pop("CORVIN_TENANT_ID", None)
        if _audit._se is None:
            self.skipTest("forge.security_events not importable in this layout")
        self._load_deadline = registry.LOAD_DEADLINE_S
        self._hc_deadline = registry.HEALTH_CHECK_DEADLINE_S
        registry.LOAD_DEADLINE_S = 0.5
        registry.HEALTH_CHECK_DEADLINE_S = 0.3
        self.reg = registry.get_registry()
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        registry.LOAD_DEADLINE_S = self._load_deadline
        registry.HEALTH_CHECK_DEADLINE_S = self._hc_deadline
        for pid in ("hang-load-t09", "hang-health-t09", "ok-load-t09"):
            try:
                self.reg.unregister(pid)
            except Exception:  # noqa: BLE001
                pass
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _records(self, event_type: str, plugin_id: str) -> list[dict]:
        p = Path(_audit.audit_path())
        # Positive control: we are reading the isolated tenant chain, not a
        # file that merely does not exist.
        self.assertTrue(str(p).startswith(str(self.home)), p)
        if not p.exists():
            return []
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return [
            r for r in rows
            if r.get("event_type") == event_type and r["details"].get("plugin_id") == plugin_id
        ]

    def test_on_load_deadline_overrun_is_recorded(self):
        release = self.release

        class Hang(_Plugin):
            def on_load(self, ctx):
                release.wait(30)

        ok = bootstrap._register_instance(
            Hang("hang-load-t09"), plugin_id="hang-load-t09", tenant_id="_default",
            corvin_home=self.home, origin="builtin",
        )
        self.assertFalse(ok)
        # The pre-existing load_failed record still lands (positive control).
        self.assertEqual(len(self._records("plugin.load_failed", "hang-load-t09")), 1)
        recs = self._records("plugin.execution_timeout", "hang-load-t09")
        self.assertEqual(len(recs), 1, "exactly one execution_timeout per overrun")
        d = recs[0]["details"]
        self.assertTrue(_EXPECTED_KEYS <= set(d), f"allow-listed fields dropped: {d}")
        self.assertEqual(d["timeout_ms"], 500)
        self.assertEqual(d["tenant_id"], "_default")
        self.assertIn(d["boot_layer"], {"bundled", "installed"})
        self.assertEqual(recs[0].get("severity"), "WARNING")
        self.assertEqual(_audit.verify_audit(), (True, []))

    def test_a_raising_on_load_is_not_a_timeout(self):
        class Boom(_Plugin):
            def on_load(self, ctx):
                raise ValueError("nope")

        bootstrap._register_instance(
            Boom("ok-load-t09"), plugin_id="ok-load-t09", tenant_id="_default",
            corvin_home=self.home, origin="builtin",
        )
        self.assertEqual(len(self._records("plugin.load_failed", "ok-load-t09")), 1)
        self.assertEqual(self._records("plugin.execution_timeout", "ok-load-t09"), [])

    def test_health_check_deadline_overrun_is_recorded(self):
        release = self.release

        class SlowHealth(_Plugin):
            def health_check(self):
                release.wait(30)
                return HealthStatus(ok=True)

        ok = bootstrap._register_instance(
            SlowHealth("hang-health-t09"), plugin_id="hang-health-t09",
            tenant_id="_default", corvin_home=self.home, origin="builtin",
        )
        self.assertTrue(ok)
        results = self.reg.health_check_all()
        self.assertFalse(results["hang-health-t09"].ok)
        self.assertEqual(len(self._records("plugin.health_check_failed", "hang-health-t09")), 1)
        recs = self._records("plugin.execution_timeout", "hang-health-t09")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["details"]["timeout_ms"], 300)
        self.assertTrue(_EXPECTED_KEYS <= set(recs[0]["details"]))
        self.assertEqual(_audit.verify_audit(), (True, []))


if __name__ == "__main__":
    unittest.main()
