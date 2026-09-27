"""
Voice Summary Phase 1 Tests — through the real console router with a real session.

What must hold:

* every voice-summary route answers 401 without a console session, and every
  mutation 403 without the CSRF token (they used to be fully unauthenticated);
* voice state is scoped to the SESSION's tenant — a body ``tenant_id`` is
  ignored, and tenant B cannot read tenant A's transcript by guessing the sid;
* no fabricated summary: no summariser is wired, so ``summary`` is None rather
  than the strategy prompt glued to the transcript;
* start/stop reach the tenant's audit chain as metadata only — the transcript
  never lands in the chain.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

BASE = "/v1/console/chat"


def _stt_on() -> None:
    import corvin_console.routes.voice_summary as vs
    vs._get_stt_available = lambda: True
    vs._voice_sessions.clear()


class VoiceSummaryAuthTest(unittest.TestCase):
    def test_routes_require_a_session(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            client.cookies.clear()
            self.assertEqual(client.get(f"{BASE}/voice/health").status_code, 401)
            self.assertEqual(client.get(f"{BASE}/s1/voice/summary").status_code, 401)
            self.assertEqual(client.get(f"{BASE}/s1/task/status").status_code, 401)
            for path in ("/s1/voice/start", "/s1/voice/stop", "/s1/voice/feedback", "/s1/task/register"):
                self.assertEqual(client.post(BASE + path, json={}).status_code, 401, path)

    def test_mutation_requires_csrf(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            _stt_on()
            r = client.post(f"{BASE}/s1/voice/start", json={})
            self.assertEqual(r.status_code, 403, r.text)


class VoiceSummaryFlowTest(unittest.TestCase):
    def test_start_stop_summary_is_not_fabricated(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            _stt_on()
            h = {"X-CSRF-Token": csrf}
            self.assertEqual(client.get(f"{BASE}/never/voice/summary").json()["status"], "not_started")

            r = client.post(f"{BASE}/s2/voice/start", headers=h, json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(client.get(f"{BASE}/s2/voice/summary").json()["status"], "recording")

            transcript = "User asked: how do I route requests? Agent answered: use os-router."
            r = client.post(f"{BASE}/s2/voice/stop", headers=h, json={"transcript": transcript})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIsNone(r.json()["summary"])

            data = client.get(f"{BASE}/s2/voice/summary").json()
            self.assertEqual(data["status"], "stopped")
            self.assertEqual(data["transcript"], transcript)
            self.assertIsNone(data["summary"])

            blob = repr(_audit_events(home))
            self.assertIn("voice.recording_started", blob)
            self.assertIn("voice.recording_stopped", blob)
            self.assertNotIn("route requests", blob)

    def test_stop_nonexistent_session(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            _stt_on()
            r = client.post(f"{BASE}/nope/voice/stop", headers={"X-CSRF-Token": csrf},
                            json={"transcript": ""})
            self.assertEqual(r.status_code, 404)

    def test_tenant_isolation(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("_default", "tenant-b")) as (
            client, csrf, _home, clients,
        ):
            _stt_on()
            h = {"X-CSRF-Token": csrf}
            client.post(f"{BASE}/shared/voice/start", headers=h, json={})
            client.post(f"{BASE}/shared/voice/stop", headers=h,
                        json={"transcript": "tenant A secret", "tenant_id": "tenant-b"})
            client_b, csrf_b = clients["tenant-b"]
            seen = client_b.get(f"{BASE}/shared/voice/summary").json()
            self.assertEqual(seen["status"], "not_started")
            self.assertNotIn("secret", repr(seen))
            # B cannot stop / annotate A's recording either.
            r = client_b.post(f"{BASE}/shared/voice/feedback", headers={"X-CSRF-Token": csrf_b},
                              json={"score": 1.0})
            self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
