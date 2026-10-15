"""Provider quota windows: stream -> persisted -> status endpoint.

``chat_runtime.stream_turn`` runs against a REAL subprocess (``CORVIN_CLAUDE_BIN``) that
replays the stream captured from the real claude CLI (``fixtures/chat_diff/real_file_tools.jsonl``);
its ``rate_limit_event`` carries five_hour / seven_day windows. Asserted at the two ends a
caller sees: the file the stream loop wrote, and the JSON the Models page reads.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

FIXTURE = _THIS.parent / "fixtures" / "chat_diff" / "real_file_tools.jsonl"


def _drain(agen):
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


class ParseTests(unittest.TestCase):
    def setUp(self):
        from corvin_console import rate_limit_windows as rlw
        self.rlw = rlw

    def test_keeps_numbers_and_drops_everything_else(self):
        evt = {"type": "rate_limit_event", "uuid": "UUID-MARKER", "session_id": "SESSION-MARKER", "rate_limit_info": {
            "status": "allowed", "overageDisabledReason": "org_level_disabled",
            "unifiedWindows": {"five_hour": {"utilization": 0.5, "resetsAt": 1791567000},
                               "seven_day": {"utilization": 0.1, "resetsAt": 1792036800}}}}
        rec = self.rlw.parse(evt)
        self.assertEqual(rec["windows"]["seven_day"], {"utilization": 0.1, "resets_at": 1792036800.0})
        self.assertEqual(rec["status"], "allowed")
        self.assertNotIn("MARKER", json.dumps(rec))
        self.assertNotIn("org_level", json.dumps(rec))

    def test_rejects_junk_instead_of_inventing_a_window(self):
        bad = [{}, {"rate_limit_info": "x"}, {"rate_limit_info": {"unifiedWindows": {
            "Bad Name": {"resetsAt": 5}, "five_hour": {"resetsAt": "soon"}, "seven_day": {"resetsAt": True},
            "x": {"resetsAt": float("nan")}}}}]
        for evt in bad:
            self.assertIsNone(self.rlw.parse(evt), evt)

    def test_older_event_shape_with_a_single_window(self):
        rec = self.rlw.parse({"rate_limit_info": {"rateLimitType": "five_hour", "resetsAt": 1791567000}})
        self.assertEqual(rec["windows"], {"five_hour": {"utilization": None, "resets_at": 1791567000.0}})


@unittest.skipIf(sys.platform.startswith("win"), "POSIX process semantics in the harness")
class StreamToStatusE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        cls._env_before = dict(os.environ)
        os.environ.update({
            "CORVIN_HOME": str(home / "corvin"),
            "XDG_CONFIG_HOME": str(home / "xdg"),
            "FORGE_ROOT": str(home / "forge"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default",
            "CORVIN_OS_ENGINE": "claude_code",
            # Hermetic auth: the sandboxed HOME has no ~/.claude/.credentials.json, and
            # chat_runtime._claude_authenticated() would refuse the turn before the spawn.
            # A dummy key, set only in this test process, satisfies the real probe.
            "ANTHROPIC_API_KEY": "sk-ant-test-dummy-not-a-credential",
        })
        os.environ.pop("VOICE_AUDIT_PATH", None)
        (home / "corvin").mkdir()
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        cr = chat_runtime
        import house_rules as _hr  # type: ignore
        cls._hr, cls._hr_orig = _hr, _hr._house_rules_classifier
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
        cls.sess = cr.create_session("_default")
        os.environ.update(kit.fake_env(home, FIXTURE, speedup=20.0))
        cls.before = None
        from corvin_console import rate_limit_windows as rlw
        cls.rlw = rlw
        cls.before = rlw.read("_default")
        _drain(cr.stream_turn(cls.sess, "edit the files"))
        cls.after = rlw.read("_default")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._hr._house_rules_classifier = cls._hr_orig
        cls.tmp.cleanup()
        os.environ.clear()
        os.environ.update(cls._env_before)

    def test_a_chat_turn_persists_the_windows_from_the_stream(self):
        self.assertEqual(self.before, {})
        self.assertEqual(self.after["windows"]["five_hour"]["resets_at"], 1791567000.0)
        self.assertEqual(self.after["windows"]["seven_day"],
                         {"utilization": 0.16, "resets_at": 1792036800.0})
        self.assertGreater(self.after["observed_at"], 0)

    def test_the_models_status_endpoint_serves_them(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from corvin_console import deps as _deps
        from corvin_console.routes import model_cost_optimizer_api as route

        class _Rec:
            tenant_id = "_default"
            sid_fingerprint = "test-fp"
            csrf_token = "t"

        app = FastAPI()
        app.include_router(route.router, prefix="/v1/console")
        app.dependency_overrides[_deps.require_session] = lambda: _Rec()
        app.dependency_overrides[_deps.require_csrf] = lambda: _Rec()
        resp = TestClient(app, raise_server_exceptions=False).get(
            "/v1/console/learning/model-cost-optimizer/status")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["rate_limit_windows"]["windows"]["seven_day"]["resets_at"], 1792036800.0)


if __name__ == "__main__":
    unittest.main()
