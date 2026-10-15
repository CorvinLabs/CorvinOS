"""E2E — ADR-0114 M2: ACS worker progress reaches the web-chat WebSocket.

M2ProgressE2E in test_adr0114_milestones_e2e.py proves the "✓ Worker … finished"
delta is produced by ``stream_turn`` — it calls the generator directly and never
crosses a transport. This file drives the same delegated turn over the REAL chat
WebSocket (``/v1/console/chat/sessions/{sid}/stream``) and asserts the progress
line in the frames a client receives, before ``done``.

The harness (sandbox, fakes at the model boundary, real stream_turn + ACSRuntime)
is inherited from the M2.1 artifact suite; the inherited artifact tests run again
here as part of the same class.
"""
from __future__ import annotations

from test_m2_1_artifact_registration_e2e import M21ArtifactRegistrationE2E


class M2ProgressWsE2E(M21ArtifactRegistrationE2E):
    """M2 over WS: the worker progress delta is streamed to the client."""

    def test_worker_progress_is_streamed_over_the_websocket(self) -> None:
        events, _payload = self._run_over_http()
        self.assertIn("Delegated to ACS workers",
                      "".join(e.get("text") or "" for e in events
                              if e.get("type") == "delta"),
                      "prompt must take the ACS fan-out")
        progress = [e.get("text") or "" for e in events
                    if e.get("type") == "delta" and "✓ Worker" in (e.get("text") or "")]
        self.assertEqual(len(progress), 1,
                         f"expected one worker progress frame over WS, got {progress}")
        types_in_order = [e.get("type") for e in events]
        self.assertLess(types_in_order.index("delta"), types_in_order.index("done"))
        self.assertEqual(events[-1].get("type"), "done")
