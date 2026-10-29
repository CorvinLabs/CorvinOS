"""ADR-2092 E2E — the L5 routing record and outcome through the REAL console ``stream_turn``.

Reuses the ADR-0203 harness (fake claude subprocess, fake ACS runtime, pinned
house-rules classifier, fake license quota); the routing code under test — the
triage, ``delegation_policy.route_and_record`` and the outcome report inside
``_os_emit_completed`` — runs unpatched. Every assertion reads the tenant routing
ledger the turn wrote.
"""
from __future__ import annotations

from unittest.mock import patch

from test_delegation_routing_e2e import _drain, _StreamTurnE2EBase

FANOUT = ("Erstelle einen ausführlichen Reiseplan für zwei Wochen Japan "
          "und dann eine Packliste mit Empfehlungen für jede Jahreszeit "
          "sowie einen Überblick über die wichtigsten Etikette-Regeln")


class ConsoleRoutingLedgerE2E(_StreamTurnE2EBase):
    def _rows(self):
        from core.skills.os_skills.monitoring import routing_ledger

        return routing_ledger.join(routing_ledger.read_records("_default"))

    def test_smalltalk_turn_records_one_native_decision_and_its_outcome(self) -> None:
        events, spawn, _ = self._run_turn("wie spät ist es eigentlich?")
        self.assertTrue(spawn["hit"])
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["surface"], row["used"], row["phase"]), ("console", "native", "shadow"))
        self.assertIsNotNone(row["outcome"])
        self.assertEqual(row["outcome"]["used"], "native")
        self.assertEqual(events[-1].get("type"), "done")

    def test_fanout_turn_records_acs_as_the_served_engine(self) -> None:
        events, _spawn, _ = self._run_turn(FANOUT)
        self.assertTrue(self._took_acs(events))
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["used"], "acs")
        self.assertIsNotNone(rows[0]["outcome"])
        self.assertEqual(rows[0]["outcome"]["used"], "acs")

    def test_delegation_disabled_still_records_exactly_one_native_decision(self) -> None:
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        self._no_spawn_guard()
        with patch.object(self.cr, "_delegation_enabled", return_value=False):
            _drain(self.cr.stream_turn(self.sess, FANOUT))
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["bundled"], rows[0]["used"]), ("native", "native"))
        self.assertIsNotNone(rows[0]["outcome"])

    def test_three_turns_three_rows(self) -> None:
        for prompt in ("hallo", "wie spät ist es eigentlich?", FANOUT):
            self._run_turn(prompt)
        rows = self._rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["turn_id"] for r in rows}), 3)
        self.assertTrue(all(r["outcome"] is not None for r in rows))

    def test_ledger_holds_no_prompt_text(self) -> None:
        self._run_turn(FANOUT)
        from core.skills.os_skills.monitoring import routing_ledger

        raw = routing_ledger.ledger_path("_default").read_text(encoding="utf-8")
        self.assertNotIn("Japan", raw)
        self.assertNotIn("Reiseplan", raw)


# ── NOT-JUDGED outcomes (ADR-2092 G1 as amended 2026-10-27; review s3_routing-3/-4/P1) ──

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402

from test_delegation_routing_e2e import _FakeACSResult  # noqa: E402

TENANT = "_default"
SIMPLE_FEATS = {"complexity": "simple", "len_bucket": "s", "has_table": False, "has_code": False}


def _seed_console_ready():
    """A console history the readiness gate clears (510 judged turns, 40 disagreements)."""
    from core.skills.os_skills.monitoring import routing_ledger

    path = routing_ledger.ledger_path(TENANT)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        for i in range(510):
            tid = routing_ledger.new_turn_id()
            b = "acs" if i < 40 else "native"
            fh.write(json.dumps({"v": 1, "kind": "decision", "ts": time.time(), "turn_id": tid,
                                 "surface": "console", "phase": "shadow", "bundled": b,
                                 "skill": "native", "skill_conf": 0.8, "used": b,
                                 "source": "bundled", "clamped": False,
                                 "features": {"complexity": "simple"}}) + "\n")
            fh.write(json.dumps({"v": 1, "kind": "outcome", "ts": time.time(), "turn_id": tid,
                                 "surface": "console", "used": b, "ok": True,
                                 "latency_ms": 5}) + "\n")


class ConsoleNotJudgedOutcomesE2E(_StreamTurnE2EBase):
    def setUp(self) -> None:
        super().setUp()
        from core.skills.os_skills.monitoring import dual_write

        dual_write.reset_for_tests()
        self.addCleanup(dual_write.reset_for_tests)
        self.addCleanup(os.environ.pop, "CORVIN_ACP_PHASE", None)

    def _rows(self):
        from core.skills.os_skills.monitoring import routing_ledger

        return routing_ledger.join(routing_ledger.read_records(TENANT))

    def _chain(self):
        from forge.paths import tenant_audit_chain

        p = tenant_audit_chain(TENANT)
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []

    def _slow_acs(self):
        acs_mod = types.ModuleType("acs_runtime")

        class SlowACS:
            def __init__(self, **kw):
                pass

            async def run(self, spec, run_id=None, **kw):
                await asyncio.sleep(30)
                return _FakeACSResult()

        acs_mod.ACSRuntime = SlowACS
        acs_mod.ACSResult = _FakeACSResult
        sys.modules["acs_runtime"] = acs_mod

    def _summary(self):
        from core.skills.os_skills.monitoring import routing_ledger

        return routing_ledger.summarize(routing_ledger.read_records(TENANT), surface="console")

    def test_a_cancelled_turn_is_recorded_not_judged(self) -> None:
        """s3_routing-3: a client disconnect used to be an ACS failure (ok:false)."""
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._slow_acs()
        self._no_spawn_guard()

        async def drive():
            agen = self.cr.stream_turn(self.sess, FANOUT)

            async def collect():
                async for _ in agen:
                    pass
            t = asyncio.ensure_future(collect())
            await asyncio.sleep(4.0)
            t.cancel()
            try:
                await t
            except BaseException:  # noqa: BLE001
                pass
        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"):
            asyncio.run(drive())
        (row,) = self._rows()
        self.assertEqual(row["used"], "acs")
        self.assertEqual((row["outcome"]["ok"], row["outcome"].get("excluded")), (None, "cancelled"))
        s = self._summary()
        self.assertEqual((s["decisions"], s["not_judged"], s["join_rate"]), (0, 1, None), s)

    def test_a_turn_killed_by_a_restart_is_recorded_orphaned_by_the_boot_reaper(self) -> None:
        """NOTE-0011 cause A: the boot reaper now records the routing turn as not judged."""
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._slow_acs()
        self._no_spawn_guard()
        loop = asyncio.new_event_loop()
        agen = self.cr.stream_turn(self.sess, FANOUT)

        async def collect():
            async for _ in agen:
                pass
        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"):
            task = loop.create_task(collect())
            loop.run_until_complete(asyncio.sleep(3.0))
        # The "process" dies here: the turn never runs again and never reports.
        (row,) = self._rows()
        self.assertIsNone(row["outcome"])
        from corvin_core.task_manager import TaskManager
        from corvin_plugins import bootstrap

        # The liveness probes are the boundary that says "the owning process is gone".
        with patch.object(TaskManager, "_owner_alive", return_value=False), \
             patch.object(TaskManager, "_task_pid_alive", return_value=False):
            reaped = bootstrap._reap_stale_tasks()
        self.assertGreaterEqual(reaped, 1)
        (row,) = self._rows()
        self.assertEqual((row["outcome"]["ok"], row["outcome"].get("excluded")), (None, "orphaned"))
        self.assertEqual(self._summary()["not_judged_by_reason"], {"orphaned": 1})
        self._leaked = (loop, task, agen)   # never resumed: a killed process does not finish

    def test_an_empty_tde_directive_is_recorded_no_engine(self) -> None:
        """s3_routing-4: a turn in which no engine ran used to stay in the join denominator."""
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        self._no_spawn_guard()
        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="tde"):
            _drain(self.cr.stream_turn(self.sess, "/use-engine tiered_delegation"))
        (row,) = self._rows()
        self.assertEqual((row["outcome"]["ok"], row["outcome"].get("excluded")), (None, "no_engine"))
        self.assertEqual(self._summary()["decisions"], 0)

    def test_the_engine_guard_query_never_executes_the_skill(self) -> None:
        """s3_routing-5: the WebSocket engine guard (chat_runtime.will_delegate) executed the
        Skill in dual_write (a skill.executed record per guard call); it must mirror the turn."""
        from audit import audit_event
        from core.skills.boot import boot_skills

        boot_skills(tenant_id=TENANT, wire_learning=False,
                    audit_emit=lambda e, d: audit_event(e, details=d, tenant_id=TENANT))
        _seed_console_ready()
        from core.skills.os_skills.monitoring import readiness

        self.assertTrue(readiness.cached_evaluate(TENANT, "console", background=False).ready)
        os.environ["CORVIN_ACP_PHASE"] = "phase2_dual_write"
        import delegation_policy as dp

        def executed():
            return [e for e in self._chain() if e["event_type"] == "skill.executed"]
        before = len(executed())
        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"), \
             patch.object(dp, "routing_features",
                          lambda p, tenant_id="_default": dict(SIMPLE_FEATS)):
            guard = self.cr.will_delegate(self.sess, FANOUT)
        # The Skill de-escalates this short simple turn to native in a cleared Phase 2,
        # so the guard must answer "not delegated" — without running the Skill.
        self.assertFalse(guard)
        self.assertEqual(len(executed()), before)
        self.assertEqual([e for e in self._chain() if e["event_type"] == "routing.phase2_decision"], [])

    def test_an_explicit_tde_command_never_reaches_the_router(self) -> None:
        """s3_routing-P1: a cleared Phase 2 audited a de-escalation the TDE branch never served."""
        from audit import audit_event
        from core.skills.boot import boot_skills

        booted = boot_skills(tenant_id=TENANT, wire_learning=False,
                             audit_emit=lambda e, d: audit_event(e, details=d, tenant_id=TENANT))
        self.assertIn("os.delegation_router", booted)
        _seed_console_ready()
        from core.skills.os_skills.monitoring import readiness

        self.assertTrue(readiness.cached_evaluate(TENANT, "console", background=False).ready)
        os.environ["CORVIN_ACP_PHASE"] = "phase2_dual_write"
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        self._no_spawn_guard()
        ran = []

        async def fake_tde(sess, task_text, tm, task_id, *, emit_completed, **_kw):
            ran.append(task_text)
            emit_completed(0)
            yield {"type": "result", "text": "TDE done", "usage": None}
            yield {"type": "done"}
        import delegation_policy as dp

        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="tde"), \
             patch.object(self.cr, "_should_delegate", return_value=True), \
             patch.object(self.cr, "_tde_available", return_value=True), \
             patch.object(self.cr, "_tde_quota_peek_ok", return_value=True), \
             patch.object(self.cr, "_stream_tde_turn", fake_tde), \
             patch.object(dp, "routing_features",
                          lambda p, tenant_id="_default": dict(SIMPLE_FEATS)):
            _drain(self.cr.stream_turn(self.sess, "/use-engine tiered_delegation fasse kurz zusammen"))
        self.assertEqual(ran, ["fasse kurz zusammen"])
        live = [r for r in self._rows() if r["phase"] != "shadow" or r["bundled"] == "tde"
                or r["source"] == "skill"]
        self.assertEqual(len(live), 1, live)
        row = live[0]
        self.assertEqual((row["bundled"], row["used"], row["source"]), ("tde", "tde", "bundled"))
        self.assertTrue(row["features"].get("force_delegate"))
        self.assertEqual(row["outcome"]["used"], "tde")
        self.assertEqual([e for e in self._chain() if e["event_type"] == "routing.phase2_decision"], [])


# ── review r2_s2 (2026-10-28) ───────────────────────────────────────────────

class ConsoleRoundTwoRoutingE2E(_StreamTurnE2EBase):
    def setUp(self) -> None:
        super().setUp()
        from core.skills.os_skills.monitoring import dual_write

        dual_write.reset_for_tests()
        self.addCleanup(dual_write.reset_for_tests)
        self.addCleanup(os.environ.pop, "CORVIN_ACP_PHASE", None)

    _rows = ConsoleNotJudgedOutcomesE2E._rows
    _chain = ConsoleNotJudgedOutcomesE2E._chain
    _slow_acs = ConsoleNotJudgedOutcomesE2E._slow_acs

    def _ws_turn(self, prompt: str) -> list[dict]:
        """One turn through the REAL chat WebSocket route (routes/chat.py)."""
        from unittest.mock import MagicMock

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from corvin_console.routes import chat as chat_routes

        rec = MagicMock()
        rec.tenant_id = TENANT
        rec.sid_fingerprint = "fp-r2-s2"
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        wire: list[dict] = []
        with patch.object(chat_routes.session_auth, "load_session", return_value=rec), \
             patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
                   lambda *a, **k: None):
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", "valid-sid")
            with client.websocket_connect(
                    f"/v1/console/chat/sessions/{self.sess.sid}/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": prompt})
                while True:
                    ev = ws.receive_json()
                    wire.append(ev)
                    if ev.get("type") == "done":
                        break
        return wire

    def test_guard_and_turn_route_on_one_phase_decision_over_the_websocket(self) -> None:
        """r2_s2-2: on a cold/expired readiness cache the engine guard read 'evaluating'
        (shadow: 'will delegate' -> the engine-unavailable 503 skipped) and the turn, after
        the refresh landed, read 'ready' and de-escalated to the undrivable native engine."""
        from audit import audit_event
        from core.skills.boot import boot_skills
        from core.skills.os_skills.monitoring import readiness

        boot_skills(tenant_id=TENANT, wire_learning=False,
                    audit_emit=lambda e, d: audit_event(e, details=d, tenant_id=TENANT))
        _seed_console_ready()
        os.environ["CORVIN_ACP_PHASE"] = "phase2_dual_write"
        readiness.clear_cache()                   # cold: no verdict yet
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        spawn = self._no_spawn_guard()
        real_guard = self.cr.will_delegate

        def guard_then_refresh_lands(sess, prompt, **kw):
            out = real_guard(sess, prompt, **kw)
            # The refresh the guard's cold read started lands before the turn starts.
            deadline = time.time() + 5
            while time.time() < deadline:
                hit = readiness._cache.get((TENANT, "console"))
                if hit is not None and hit[1].ready:
                    break
                time.sleep(0.02)
            return out
        import delegation_policy as dp

        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"), \
             patch.object(self.cr, "get_engine_unavailable_message",
                          return_value="The OS engine is not set up."), \
             patch.object(self.cr, "will_delegate", guard_then_refresh_lands), \
             patch.object(dp, "routing_features",
                          lambda p, tenant_id="_default": dict(SIMPLE_FEATS)):
            wire = self._ws_turn(FANOUT)
        self.assertFalse(any(e.get("type") == "error" and e.get("code") == 503 for e in wire), wire)
        (row,) = [r for r in self._rows() if r.get("turn_id") and r["features"] == SIMPLE_FEATS
                  | {"force_delegate": False, "is_big_data": False, "mode": "acs",
                     "tde_available": False, "quota_ok": False}]
        # The guard said "delegates" on a shadow decision; the turn served the same.
        self.assertEqual((row["phase"], row.get("gate"), row["used"], row["source"]),
                         ("shadow", "pending", "acs", "bundled"), row)
        self.assertFalse(spawn["hit"], "the undrivable native engine must not be spawned")
        self.assertEqual([e for e in self._chain() if e["event_type"] == "routing.phase2_decision"], [])

    def test_the_detached_turn_watchdog_is_a_judged_failure(self) -> None:
        """r2_s2-5: the server's own watchdog (detached_timeout) cut a hung engine and the
        outcome was recorded NOT-JUDGED 'cancelled'. Driven through routes/chat.py
        ``_detach_turn`` with its bound shortened."""
        from corvin_console.routes import chat as chat_routes

        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._slow_acs()
        self._no_spawn_guard()

        async def drive():
            agen = self.cr.stream_turn(self.sess, FANOUT)

            async def collect():
                async for _ in agen:
                    pass
            t = asyncio.ensure_future(collect())
            await asyncio.sleep(1.0)
            with patch.object(chat_routes, "_detached_turn_max_s", return_value=2.0):
                chat_routes._detach_turn(t, tenant_id=TENANT, sid_fingerprint="fp",
                                         sid=self.sess.sid)
            try:
                await asyncio.wait_for(t, timeout=20)
            except BaseException:  # noqa: BLE001
                pass
        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"):
            asyncio.run(drive())
        (row,) = self._rows()
        self.assertEqual(row["used"], "acs")
        self.assertEqual((row["outcome"]["ok"], row["outcome"].get("excluded")), (False, None))

    def test_an_operator_stop_stays_not_judged(self) -> None:
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._slow_acs()
        self._no_spawn_guard()

        async def drive():
            agen = self.cr.stream_turn(self.sess, FANOUT)

            async def collect():
                async for _ in agen:
                    pass
            t = asyncio.ensure_future(collect())
            await asyncio.sleep(1.0)
            self.cr.note_cancel_reason(TENANT, self.sess.sid, "operator")
            t.cancel()
            try:
                await t
            except BaseException:  # noqa: BLE001
                pass
        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"):
            asyncio.run(drive())
        (row,) = self._rows()
        self.assertEqual((row["outcome"]["ok"], row["outcome"].get("excluded")), (None, "cancelled"))


    # ── review r3_s2-1: a cleared Phase 2 Skill route onto an unavailable OS engine. The
    # console guard refused it with 503 and wrote no ledger row (the bridge records it
    # since 36a450b75); driven through the REAL chat WebSocket route.

    def _unavailable_engine_turn(self, phase: str, prompt: str = FANOUT):
        from audit import audit_event
        from core.skills.boot import boot_skills
        from core.skills.os_skills.monitoring import readiness, routing_ledger

        boot_skills(tenant_id=TENANT, wire_learning=False,
                    audit_emit=lambda e, d: audit_event(e, details=d, tenant_id=TENANT))
        _seed_console_ready()
        os.environ["CORVIN_ACP_PHASE"] = phase
        if phase == "phase2_dual_write":
            self.assertTrue(readiness.cached_evaluate(TENANT, "console", background=False).ready)
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        spawn = self._no_spawn_guard()
        before = len(routing_ledger.read_records(TENANT))
        import delegation_policy as dp

        with patch.object(self.cr, "_delegation_enabled", return_value=True), \
             patch.object(self.cr._feature_flags, "worker_engine_mode", return_value="acs"), \
             patch.object(self.cr, "get_engine_unavailable_message",
                          return_value="The OS engine is not set up."), \
             patch.object(dp, "routing_features",
                          lambda p, tenant_id="_default": dict(SIMPLE_FEATS)):
            wire = self._ws_turn(prompt)
        return wire, routing_ledger.read_records(TENANT)[before:], spawn

    def test_a_skill_route_onto_an_unavailable_engine_is_a_judged_failure(self) -> None:
        wire, rows, spawn = self._unavailable_engine_turn("phase2_dual_write")
        self.assertTrue(any(e.get("type") == "error" and e.get("code") == 503 for e in wire), wire)
        self.assertFalse(spawn["hit"])
        decs = [r for r in rows if r["kind"] == "decision"]
        outs = [r for r in rows if r["kind"] == "outcome"]
        self.assertEqual(len(decs), 1, rows)
        self.assertEqual(len(outs), 1, rows)
        dec, out = decs[0], outs[0]
        self.assertEqual((dec["phase"], dec["bundled"], dec["used"], dec["source"], dec["gate"]),
                         ("dual_write", "acs", "native", "skill", "verdict"))
        self.assertEqual((out["turn_id"], out["used"], out["ok"], out.get("excluded")),
                         (dec["turn_id"], "native", False, None))
        self.assertEqual([e["details"]["turn_id"] for e in self._chain()
                          if e["event_type"] == "routing.phase2_decision"], [dec["turn_id"]])

    def test_control_the_same_turn_in_shadow_delegates_and_succeeds(self) -> None:
        wire, rows, _ = self._unavailable_engine_turn("phase1_shadow")
        self.assertFalse(any(e.get("type") == "error" for e in wire), wire)
        self.assertEqual([(r["kind"], r["used"], r.get("ok")) for r in rows],
                         [("decision", "acs", None), ("outcome", "acs", True)])

    def test_a_refusal_the_bundled_rule_made_still_records_nothing(self) -> None:
        """Not the Skill's doing: smalltalk stays native under the bundled rule."""
        wire, rows, _ = self._unavailable_engine_turn("phase2_dual_write", prompt="hallo")
        self.assertTrue(any(e.get("code") == 503 for e in wire), wire)
        self.assertEqual(rows, [])
