"""E2E — ADR-0114 M2.1: artifacts of a delegated web-chat run are registered.

Drives the REAL chat WebSocket (``/v1/console/chat/sessions/{sid}/stream``) into
the REAL ``chat_runtime.stream_turn`` and the REAL ``ACSRuntime``. Only the model
boundaries are faked (manager/worker calls, spawn gates, licence quota), exactly
like the other delegation E2E suites. The worker writes a deliverable into
``<run_dir>/output`` the way a real worker's tool call would.

Asserted at the two ends a client sees over HTTP/WS:
  * the WebSocket event stream carries an ``artifact`` event for the file, before ``done``;
  * ``GET /chat/sessions/{sid}/turns`` returns the file as an ``artifact`` part.

Covered: a successful run, and a run whose worker fails after writing its file
(the post-run scan must still register what is on disk).

Sandbox: CORVIN_HOME / XDG_CONFIG_HOME / HOME in a fresh temp dir, a dummy
ANTHROPIC_API_KEY for the sign-in guard, FORGE_ROOT unset.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / _p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PROMPT = (
    "Recherchiere aus mehreren Quellen die Marktlage für E-Bikes und "
    "vergleiche die Ergebnisse"
)


class _Rec:
    tenant_id = "_default"
    sid_fingerprint = "test-fp"
    csrf_token = "t"


class M21ArtifactRegistrationE2E(unittest.TestCase):
    """Real WS turn + real GET /turns; only the model boundaries are faked."""

    worker_fails = False
    worker_sleep_s = 3.0
    # When set, the deliverable is written only after ACSRuntime.run has returned,
    # i.e. after the live poll's final sighting: only the post-run scan can see it.
    late_write = False
    # When set, the deliverable keeps growing inside the post-run stability window
    # (between the scan's size read and its re-read): it must not be registered.
    grow_during_window = False

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._saved = {k: os.environ.get(k) for k in (
            "CORVIN_HOME", "XDG_CONFIG_HOME", "HOME", "CORVIN_TENANT_ID",
            "CORVIN_AUDIT_ANCHOR_KEY", "ANTHROPIC_API_KEY", "FORGE_ROOT",
            "CORVIN_WDAT_KEY")}
        for k in self._saved:
            self.addCleanup(self._restore_env, k)
        os.environ["CORVIN_HOME"] = str(root / "corvin")
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg")
        os.environ["HOME"] = str(root / "home")
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(root / "anchor.key")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-sandbox-dummy"
        os.environ.pop("FORGE_ROOT", None)
        os.environ.pop("CORVIN_WDAT_KEY", None)

        from corvin_console import chat_runtime
        from corvin_console.routes import chat as chat_routes
        self.cr = chat_runtime
        self.routes = chat_routes
        self.sess = chat_runtime.create_session("_default")

    def _restore_env(self, key: str) -> None:
        prev = self._saved[key]
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev

    # ── fakes at the model-call boundary ────────────────────────────────────

    def _fake_manager(self, prompt, model, tenant_id="_default", proc_holder=None):
        self._manager_calls = getattr(self, "_manager_calls", 0) + 1
        if self._manager_calls == 1:
            decision = {
                "decision": "DELEGATE",
                "reasoning": "one worker writes the report",
                "subtasks": [{"id": "s1", "instructions": "write the report",
                              "expected_output": {}}],
            }
        else:
            if self.worker_fails:
                # The run ends in status "failed" after its worker wrote the file.
                raise RuntimeError("manager engine failed after the worker run")
            decision = {"decision": "COMPLETE",
                        "complete_artifacts": {"summary": "report written"}}
        return json.dumps(decision), 100

    def _fake_worker(self, prompt, system, model, budget, extra_env=None,
                     tenant_id="_default", proc_holder=None):
        if not self.late_write:
            runs = sorted((self.sess.workdir / "acs" / "runs").iterdir())
            out = runs[-1] / "output"
            out.mkdir(parents=True, exist_ok=True)
            (out / "marktbericht.md").write_text("# Marktbericht\n\nE-Bikes wachsen.\n",
                                                 encoding="utf-8")
        # Long enough for the live poll to see the file as stable (two sightings).
        time.sleep(self.worker_sleep_s)
        return (
            json.dumps({"status": "success", "result": {"ok": True}, "confidence": 0.9}),
            50,
            {"engine_id": "claude_code", "model_id": model, "attested": True,
             "locality": "us_cloud"},
        )

    def _patches(self):
        import house_rules as _hr  # type: ignore
        import spawn_gates as _sg  # type: ignore
        import acs_runtime as _rt  # type: ignore

        orig = getattr(_hr, "_house_rules_classifier", None)
        self.addCleanup(setattr, _hr, "_house_rules_classifier", orig)
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear"))

        limits_mod = types.ModuleType("license.limits")

        class _E(Exception):
            pass

        limits_mod.LicenseLimitError = _E  # type: ignore[attr-defined]
        quota_mod = types.ModuleType("license.compute_quota")
        quota_mod.increment_and_check = (  # type: ignore[attr-defined]
            lambda home, channel=None, chat_key=None: None)
        saved_mods = {m: sys.modules.get(m) for m in
                      ("license", "license.compute_quota", "license.limits")}
        sys.modules["license"] = types.ModuleType("license")
        sys.modules["license.compute_quota"] = quota_mod
        sys.modules["license.limits"] = limits_mod

        def _restore_mods():
            for m, prev in saved_mods.items():
                if prev is None:
                    sys.modules.pop(m, None)
                else:
                    sys.modules[m] = prev
        self.addCleanup(_restore_mods)

        async def _no_spawn(*args, **kwargs):
            proc = AsyncMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=0)
            proc.stdout = AsyncMock()
            proc.stdout.readline = AsyncMock(return_value=b"")
            proc.stdout.read = AsyncMock(return_value=b"")
            proc.stderr = AsyncMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            proc.stdin = MagicMock()
            proc.stdin.drain = AsyncMock()
            return proc

        orig_render = self.cr._render_acs_graph
        orig_sleep = asyncio.sleep
        grown: list[bool] = []

        def _render_then_late_write(scan_root, *args, **kwargs):
            # Runs in the thread that the turn awaits between the final live poll and
            # the post-run scan: the only window in which a file is invisible to the
            # live poll (it sees a file only after two equal sightings) yet still
            # on disk when the post-run scan runs.
            if self.late_write:
                self._scan_root = Path(scan_root)
                out = Path(scan_root) / "output"
                out.mkdir(parents=True, exist_ok=True)
                (out / "marktbericht.md").write_text(
                    "# Marktbericht\n\nE-Bikes wachsen.\n", encoding="utf-8")
                # Positive control: a file that stays unchanged must still register.
                (out / "stabil.md").write_text("# Stabil\n", encoding="utf-8")
            return orig_render(scan_root, *args, **kwargs)

        async def _sleep_then_grow(delay, *args, **kwargs):
            # The post-run scan sizes each candidate, then waits
            # _POST_RUN_STABLE_WINDOW_S and re-reads it. Growing the file exactly
            # in that wait is the only way a size-stability check can be tested.
            if (self.grow_during_window and not grown
                    and delay == self.cr._POST_RUN_STABLE_WINDOW_S):
                grown.append(True)
                with open(self._scan_root / "output" / "marktbericht.md", "a",
                          encoding="utf-8") as fh:
                    fh.write("weitere Zeilen, noch nicht fertig\n")
            return await orig_sleep(delay, *args, **kwargs)

        rec = _Rec()
        return [
            patch.object(asyncio, "sleep", new=_sleep_then_grow),
            patch.object(self.cr, "_render_acs_graph", new=_render_then_late_write),
            patch.object(asyncio, "create_subprocess_exec", side_effect=_no_spawn),
            patch.object(self.cr, "_delegation_enabled", return_value=True),
            patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"),
            patch.object(self.cr, "_engine_unavailable_message", return_value=None),
            patch.object(_rt, "_call_manager_sync", side_effect=self._fake_manager),
            patch.object(_rt, "_call_worker_sync", side_effect=self._fake_worker),
            patch.object(_rt, "_resolve_worker_engine", return_value=("claude_code", "test-model")),
            patch.object(_sg, "check_l44", return_value=None),
            patch.object(_sg, "check_l34", return_value=None),
            patch.object(_sg, "check_l35", return_value=None),
            patch.object(self.routes.session_auth, "load_session", return_value=rec),
        ]

    def _run_over_http(self) -> tuple[list[dict], dict]:
        """Send one prompt over the real WS, then read the turn history over HTTP."""
        app = FastAPI()
        app.include_router(self.routes.router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", "valid-sid")
        patches = self._patches()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        events: list[dict] = []
        with client.websocket_connect(
                f"/v1/console/chat/sessions/{self.sess.sid}/stream") as ws:
            self.assertEqual(ws.receive_json()["type"], "ready")
            ws.send_json({"type": "user", "text": _PROMPT})
            while True:
                ev = ws.receive_json()
                events.append(ev)
                if ev.get("type") == "done":
                    break
        resp = client.get(f"/v1/console/chat/sessions/{self.sess.sid}/turns")
        self.assertEqual(resp.status_code, 200, resp.text)
        return events, resp.json()

    @staticmethod
    def _artifact_names(events: list[dict]) -> list[str]:
        return [e.get("name") for e in events if e.get("type") == "artifact"]

    @staticmethod
    def _persisted_artifact_names(payload: dict) -> list[str]:
        return [p.get("name") for t in payload["turns"] for p in (t.get("parts") or [])
                if p.get("kind") == "artifact"]

    # ── tests ───────────────────────────────────────────────────────────────

    def test_successful_delegated_run_registers_the_artifact_end_to_end(self) -> None:
        events, payload = self._run_over_http()
        self.assertIn("Delegated to ACS workers",
                      "".join(e.get("text") or "" for e in events if e.get("type") == "delta"),
                      "prompt must take the ACS fan-out")
        self.assertIn("marktbericht.md", self._artifact_names(events),
                      f"artifact not streamed over WS: {events}")
        types_in_order = [e.get("type") for e in events]
        self.assertLess(types_in_order.index("artifact"), types_in_order.index("done"))
        self.assertIn("marktbericht.md", self._persisted_artifact_names(payload),
                      f"artifact not visible in GET /turns: {payload}")
        # Live poll and post-run scan both see the file; the dedup must keep one chip.
        self.assertEqual(self._artifact_names(events).count("marktbericht.md"), 1, events)
        self.assertEqual(self._persisted_artifact_names(payload).count("marktbericht.md"), 1,
                         payload)

    def test_acs_bookkeeping_is_not_registered_as_an_artifact(self) -> None:
        # ACS writes output/FINAL/manifest.json on every accepted run; it is not a
        # deliverable and must not become a chat chip on the streamed or stored turn.
        events, payload = self._run_over_http()
        self.assertTrue((self.sess.workdir / "acs" / "runs").is_dir())
        self.assertNotIn("manifest.json", self._artifact_names(events), events)
        self.assertNotIn("manifest.json", self._persisted_artifact_names(payload), payload)


class M21FailedRunArtifactE2E(M21ArtifactRegistrationE2E):
    """A run that ends in status "failed" still registers the file it left on disk.

    The post-run scan used to run only for ``ok or bounded_stop``. The file is
    written after ``ACSRuntime.run`` has returned, so the live poll's final
    sighting is its first one (it needs two equal sightings) and the file reaches
    the chat only through the post-run scan. Reinstating the status gate turns
    this test red (mutation probe, 2026-10-15).
    """

    worker_fails = True
    worker_sleep_s = 0.0
    late_write = True

    def test_file_written_before_a_failed_run_is_registered(self) -> None:
        events, payload = self._run_over_http()
        self.assertIn("marktbericht.md", self._artifact_names(events),
                      f"failed run dropped its written deliverable: {events}")
        self.assertIn("marktbericht.md", self._persisted_artifact_names(payload),
                      f"failed run's artifact missing from GET /turns: {payload}")
        self.assertEqual(self._artifact_names(events).count("marktbericht.md"), 1, events)
        self.assertEqual(self._persisted_artifact_names(payload).count("marktbericht.md"), 1,
                         payload)


class M21GrowingFileNotRegisteredE2E(M21ArtifactRegistrationE2E):
    """A file still being written when the post-run scan runs is not registered.

    The post-run scan sizes each candidate, waits ``_POST_RUN_STABLE_WINDOW_S``
    and re-reads it (RT-02). This run's failed worker leaves ``marktbericht.md``
    growing inside that window: it must not become a chip, on the WS or in
    GET /turns, while the stable ``stabil.md`` from the same run still does.
    """

    worker_fails = True
    worker_sleep_s = 0.0
    late_write = True
    grow_during_window = True
    # Inherited tests assert the successful-run deliverable, which this class's
    # failing, growing run does not produce. None is not callable, so unittest skips them.
    test_successful_delegated_run_registers_the_artifact_end_to_end = None
    test_acs_bookkeeping_is_not_registered_as_an_artifact = None

    def test_file_still_growing_after_the_run_is_not_registered(self) -> None:
        events, payload = self._run_over_http()
        self.assertNotIn("marktbericht.md", self._artifact_names(events),
                         f"half-written deliverable registered over WS: {events}")
        self.assertNotIn("marktbericht.md", self._persisted_artifact_names(payload),
                         f"half-written deliverable persisted: {payload}")
        # Positive control: the scan ran and registered the unchanged file.
        self.assertIn("stabil.md", self._artifact_names(events), events)
        self.assertIn("stabil.md", self._persisted_artifact_names(payload), payload)


if __name__ == "__main__":
    unittest.main()
