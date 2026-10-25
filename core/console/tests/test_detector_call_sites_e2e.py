"""Call-site proof for the detector wiring (T-0219, ADR-2268 Decision 6).

``classify_task(..., detector=...)`` is only worth anything if every PRODUCTION caller that
classifies text passes the tenant's opt-in. Each test drives one real caller with a text only the
detector recognises (a German mobile number; the legacy zone classifier calls it PUBLIC) and
checks both sides: refused / withheld with an enabled sensitive route, unchanged without one. The
"without" half is a positive control: it proves the text really passes today, so the "with" half
cannot be vacuous.

Callers: the turn gate (``spawn_gates.check_l34``), the bridge history gate
(``adapter._ledger_withhold``), the console history gate (``chat_runtime._session_ledger_block``)
and the A2A worker (``a2a_worker.spawn_a2a_worker``). A malformed route refuses every turn (R1a).
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

PHONE_TEXT = "Mobil erreichbar unter 0171 2345678."      # detector-only: the legacy path says PUBLIC
ROUTE = {"enabled": True, "provider": "acme_llm", "model_id": "vendor/Model-1"}


class DetectorCallSitesE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self._env_before = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ.update({
            "CORVIN_HOME": str(home / "corvin"), "XDG_CONFIG_HOME": str(home / "xdg"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"), "CORVIN_TENANT_ID": "_default",
            "CORVIN_OS_ENGINE": "claude_code",
        })
        os.environ.pop("FORGE_ROOT", None)
        os.environ.pop("VOICE_AUDIT_PATH", None)
        (home / "corvin").mkdir()
        self.cfg = home / "corvin" / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        self.cfg.parent.mkdir(parents=True)
        import spawn_gates  # type: ignore
        self.sg = spawn_gates
        self.sg._l34_cache.clear()

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)
        self.sg._l34_cache.clear()

    def tenant(self, route) -> None:
        dcfg = {} if route is None else {"sensitive_route": route}
        self.cfg.write_text(yaml.safe_dump({"spec": {"data_classification": dcfg}}), encoding="utf-8")
        self.cfg.chmod(0o600)
        self.sg._l34_cache.clear()

    # ── 1. the turn gate ───────────────────────────────────────────────────

    def test_turn_gate_check_l34(self) -> None:
        self.tenant(None)
        self.assertIsNone(self.sg.check_l34("claude_code", "_default", prompt=PHONE_TEXT, persona="assistant"),
                          "positive control: today this text passes")
        self.tenant(ROUTE)
        refusal = self.sg.check_l34("claude_code", "_default", prompt=PHONE_TEXT, persona="assistant")
        self.assertIsInstance(refusal, str)
        self.assertIn("CONFIDENTIAL", refusal)

    def test_a_public_marker_cannot_open_the_gate_for_an_opted_in_tenant(self) -> None:
        text = "[class:public] " + PHONE_TEXT
        self.tenant(None)
        self.assertIsNone(self.sg.check_l34("claude_code", "_default", prompt=text, persona="assistant"))
        self.tenant(ROUTE)
        self.assertIsInstance(self.sg.check_l34("claude_code", "_default", prompt=text, persona="assistant"), str)

    def test_a_malformed_route_refuses_every_turn(self) -> None:
        self.tenant({"enabled": "true", "provider": "acme_llm", "model_id": "m"})
        refusal = self.sg.check_l34("claude_code", "_default", prompt="write a haiku about spring",
                                    persona="assistant")
        self.assertIsInstance(refusal, str)
        self.assertIn("malformed", refusal)

    # ── 2. the bridge history gate ─────────────────────────────────────────

    def test_bridge_history_gate_withholds_a_detector_turn(self) -> None:
        import adapter  # type: ignore
        rec = {"user": PHONE_TEXT, "assistant": "ok"}
        self.tenant(None)
        self.assertIsNone(adapter._ledger_withhold("claude_code", "discord", "chat-1")(rec),
                          "positive control: today this turn is re-supplied")
        self.tenant(ROUTE)
        self.assertEqual(adapter._ledger_withhold("claude_code", "discord", "chat-1")(rec), "data_flow")

    # ── 3. the console history gate ────────────────────────────────────────

    def test_console_history_gate_withholds_a_detector_turn(self) -> None:
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        for route, expect_present in ((None, True), (ROUTE, False)):
            self.tenant(route)
            sess = chat_runtime.create_session("_default")
            chat_runtime._append_turn(sess, "user", [{"kind": "text", "text": PHONE_TEXT}])
            chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": "ok"}])
            block = chat_runtime._session_ledger_block(sess, "next question")
            self.assertEqual("0171 2345678" in block, expect_present,
                             f"route={route!r}: history block {'lost' if expect_present else 'kept'} the turn")

    # ── 4. the A2A worker ──────────────────────────────────────────────────

    def _a2a(self, instruction: str):
        import a2a_worker  # type: ignore

        class _Engine:
            name = "fake"

            def spawn(self, *a, **k):
                return iter(())

            def __getattr__(self, _n):
                return None

        return a2a_worker.spawn_a2a_worker(
            instruction=instruction, origin_id="peer-1", task_id="t-1", persona="assistant",
            ttl_s=5, engine_factory=lambda *a, **k: _Engine())

    def test_a2a_worker_refuses_a_detector_turn(self) -> None:
        self.tenant(None)
        self.assertNotEqual(self._a2a(PHONE_TEXT).reason_code, "data_flow",
                            "positive control: without the route the data-flow gate does not refuse it")
        self.tenant(ROUTE)
        res = self._a2a(PHONE_TEXT)
        self.assertEqual(res.status, "rejected")
        self.assertEqual(res.reason_code, "data_flow", res)

    def test_a2a_worker_refuses_every_turn_of_a_malformed_route_tenant(self) -> None:
        self.tenant({"enabled": "true", "provider": "acme_llm", "model_id": "m"})
        res = self._a2a("write a haiku about spring")
        self.assertEqual((res.status, res.reason_code), ("rejected", "gate_error"), res)


if __name__ == "__main__":
    unittest.main()
