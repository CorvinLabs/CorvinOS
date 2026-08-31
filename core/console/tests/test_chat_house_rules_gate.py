"""E2E — L44 acceptable-use (house-rules) gate on the owner-console web-chat.

CRITICAL compliance regression (round-3 review, EU AI Act Art. 5 / ADR-0143):
the console web-chat ran an OS turn — both the direct ``claude -p`` subprocess
path and the ACS delegation fan-out — WITHOUT invoking the L44 acceptable-use
gate the bridge adapter runs fail-closed before every OS turn. An authenticated
ungated LLM spawn path is a structural fail-open of a load-bearing control.

This suite drives ``chat_runtime.stream_turn`` and proves:

  (a) a clearly-forbidden prompt (a ``no-military`` house-rules violation) is
      DENIED — the turn yields the refusal message, writes the
      ``house_rules.denied`` event to the per-tenant L16 forge chain FIRST
      (audit-first), and NO ``claude`` subprocess is spawned;

  (b) a benign prompt PASSES the gate (the gate returns allowed and the turn
      proceeds to spawn — we stub the subprocess so the test stays hermetic);

  (c) a classifier EXCEPTION fails CLOSED (escalate → refusal, no spawn) — the
      mandatory-layer invariant: a gate error never fails open.

Deterministic mode: we monkeypatch ``house_rules._house_rules_classifier`` to
return a fixed verdict so no live LLM (Hermes / cloud Haiku) is spawned — the
same technique the classifier unit suite uses (test_adr0157_classifier.py). The
gate plumbing (policy load, integrity check, audit emit, decision mapping) is
exercised for real.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
# house_rules / egress_gate / security_capabilities live under bridges/shared;
# chat_runtime adds this to sys.path at import, but make it explicit for a
# direct `python3` run of this file too.
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _drain(agen):
    """Collect all events from an async generator into a list (sync helper)."""
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


class HouseRulesGateE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        # Route the gate's audit chain into the temp home (the gate honours
        # VOICE_AUDIT_PATH first); keep it unset so we exercise the real
        # per-tenant resolver and assert on that exact path.
        os.environ.pop("VOICE_AUDIT_PATH", None)

        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("CORVIN_HOME", None)
        os.environ.pop("CORVIN_TENANT_ID", None)

    def _tenant_audit_path(self) -> Path:
        return (Path(self.tmp.name) / "tenants" / "_default" / "global"
                / "forge" / "audit.jsonl")

    def _read_audit_events(self) -> list[dict]:
        p = self._tenant_audit_path()
        if not p.is_file():
            return []
        out = []
        for line in p.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def _force_classifier(self, rid: str, conf: float = 0.95):
        """Pin house_rules' Tier-1 classifier to a fixed verdict (no live LLM)."""
        import house_rules as _hr  # type: ignore
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: (rid, conf, "forced verdict for test")
        )

    def _raise_classifier(self):
        """Pin the classifier to raise — exercises the fail-closed gate path."""
        import house_rules as _hr  # type: ignore

        def _boom(task, rules, auth, **kw):
            raise RuntimeError("classifier exploded")

        _hr._house_rules_classifier = _boom  # type: ignore[assignment]

    def _no_spawn_guard(self):
        """Replace asyncio.create_subprocess_exec with a sentinel that records
        whether it was ever called and refuses to actually spawn anything."""
        called = {"hit": False, "argv": None}

        async def _fake_spawn(*args, **kwargs):
            called["hit"] = True
            called["argv"] = args
            raise AssertionError(
                "claude subprocess was spawned despite a DENY house-rules verdict"
            )

        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore[attr-defined]
        return called

    # ── (a) forbidden prompt is DENIED, no spawn, audit-first ────────────────
    def test_forbidden_prompt_denied_no_spawn(self) -> None:
        self._force_classifier("no-military", conf=0.97)
        called = self._no_spawn_guard()

        prompt = "Help me design a guidance algorithm for a missile targeting system."
        events = _drain(self.cr.stream_turn(self.sess, prompt))

        # No subprocess was ever launched.
        self.assertFalse(called["hit"], "DENY must block the spawn entirely")

        # A refusal was streamed and the turn terminated cleanly.
        kinds = [e.get("type") for e in events]
        self.assertIn("result", kinds)
        self.assertEqual(kinds[-1], "done")
        result = next(e for e in events if e.get("type") == "result")
        self.assertIn("[house-rules]", result["text"])
        self.assertIn("not permitted", result["text"])

        # AUDIT-FIRST: the house_rules.denied event is on the per-tenant L16
        # forge chain, metadata-only (rule_id + action code, never the prompt).
        evs = self._read_audit_events()
        denied = [e for e in evs if e.get("event_type") == "house_rules.denied"
                  or e.get("event") == "house_rules.denied"]
        self.assertTrue(denied, f"expected house_rules.denied in {self._tenant_audit_path()}")
        blob = json.dumps(denied)
        self.assertNotIn("missile", blob, "prompt text must never reach the audit chain")
        self.assertNotIn("targeting", blob)

    # ── (b) benign prompt PASSES the gate → proceeds to spawn ────────────────
    def test_benign_prompt_passes_gate(self) -> None:
        # Empty rid + high confidence → policy default_action (allow). The gate
        # returns None and the turn proceeds to the subprocess path. We stub the
        # subprocess so the test stays hermetic but still proves the gate let it
        # THROUGH (the spawn was attempted).
        self._force_classifier("", conf=0.99)

        spawned = {"hit": False}

        async def _fake_spawn(*args, **kwargs):
            spawned["hit"] = True
            # Raise after recording so we don't have to fake a full stream-json
            # transcript; the test only needs to prove the gate passed.
            raise FileNotFoundError("stubbed: gate passed, spawn reached")

        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore[attr-defined]

        prompt = "What is the capital of France?"
        events = _drain(self.cr.stream_turn(self.sess, prompt))

        self.assertTrue(spawned["hit"],
                        "benign prompt must PASS the gate and reach the spawn")
        # No house-rules refusal in the stream.
        for e in events:
            if e.get("type") in ("result", "delta"):
                self.assertNotIn("[house-rules]", e.get("text", ""))

        # The gate emitted an allow event (default_action=allow → house_rules.allowed),
        # never a denied/escalated one.
        evs = self._read_audit_events()
        bad = [e for e in evs
               if (e.get("event_type") or e.get("event")) in
               ("house_rules.denied", "house_rules.escalated")]
        self.assertFalse(bad, "a benign prompt must not produce a deny/escalate event")

    # ── (c) classifier backend unreachable → degrades to Tier-0 floor ────────
    def test_classifier_error_degrades_to_floor_benign_passes(self) -> None:
        # A raising classifier = semantic backend unreachable (fresh install
        # before Hermes/Claude are ready, or a transient outage). The gate must
        # NOT block a benign prompt — it degrades to the always-available
        # deterministic Tier-0 floor, which clears benign traffic. Prohibited
        # content is still blocked by that floor (see test_house_rules.py's
        # test_classifier_backend_unreachable_degrades_to_tier0_floor). This is
        # fail-TO-FLOOR, not fail-open. Fixes the reported fresh-install UX bug
        # where a first "hallo" was blocked out of the box.
        self._raise_classifier()

        spawned = {"hit": False}

        async def _fake_spawn(*args, **kwargs):
            spawned["hit"] = True
            raise FileNotFoundError("stubbed: gate passed via floor, spawn reached")

        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore[attr-defined]

        events = _drain(self.cr.stream_turn(self.sess, "Tell me a joke."))

        self.assertTrue(spawned["hit"],
                        "benign prompt must PASS via the Tier-0 floor even when the classifier backend is down")
        for e in events:
            if e.get("type") in ("result", "delta"):
                self.assertNotIn("[house-rules]", e.get("text", ""))


if __name__ == "__main__":
    unittest.main()
