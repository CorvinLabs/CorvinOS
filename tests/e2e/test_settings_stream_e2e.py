"""T-0380 - the console's settings stream lives 10 s and ends itself, over real HTTP (SSE).

Uvicorn keeps a graceful shutdown waiting for every open response and the app is not told about the shutdown, so a 30 minute stream made every
restart with an open console tab wait its full timeout and then cancel the stream ("Cancel 1 running task" in the journal). The stream now ends
with an `end` event after 10 s; EventSource reconnects at once and the client re-reads the settings queries on every reconnect.
Real: a session, the real route, real streaming, a real file in the tenant's global directory.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_projects_api_e2e as base  # noqa: E402
from test_projects_activity_e2e import _read_sse  # noqa: E402
from test_projects_ops_e2e import _Base  # noqa: E402

_URL = "/v1/console/settings/stream"


@unittest.skipUnless(base._MARKET.is_file(), "needs the Marketplace checkout")
class SettingsStream(_Base):
    def test_the_stream_ends_itself_within_ten_seconds_with_an_end_event(self):
        with self._ctx() as c:
            t0 = time.monotonic()
            ev = _read_sse(c.own, _URL, limit_s=20.0)
            took = time.monotonic() - t0
            self.assertEqual(ev[0], ("__status__", 200))
            self.assertEqual(ev[-1][0], "end", ev[-3:])
            self.assertLessEqual(took, 12.5, f"the stream lived {took:.1f} s; a restart would wait for it")
            self.assertGreaterEqual(took, 6.0, "ending far too early would make the client reconnect in a loop")

    def test_a_changed_settings_file_still_arrives_as_an_event_inside_the_lifetime(self):
        from core.console.corvin_console.routes import settings_stream as ss
        with self._ctx() as c:
            path = ss._watched_files("_default")["ldd"]
            path.parent.mkdir(parents=True, exist_ok=True)

            def touch():
                time.sleep(2.5)
                path.write_text('{"changed": true}\n')

            threading.Thread(target=touch, daemon=True).start()
            ev = _read_sse(c.own, _URL, until=lambda e, d: e == "settings.changed", limit_s=14.0)
            hits = [d for e, d in ev if e == "settings.changed"]
            self.assertTrue(hits, ev)
            self.assertIn("ldd", hits[0]["domains"])


if __name__ == "__main__":
    unittest.main()
