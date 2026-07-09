"""Unit tests for ACO Layers 2, 3, 4 (ADR-0174)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

def _write_log(workdir: Path, events: list[dict]) -> None:
    log = workdir / "chat_debug.jsonl"
    with log.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


# ── Layer 2: Replay manifest ──────────────────────────────────────────────────

class TestReplayManifest:
    def test_from_dict_minimal(self):
        from corvin_console.aco.replay import ReplayManifest
        m = ReplayManifest.from_dict({
            "version": 1,
            "scenario": "test",
            "description": "minimal",
            "turns": [{"input": "Hello", "expect_events": ["turn.done"]}],
        })
        assert m.scenario == "test"
        assert len(m.turns) == 1
        assert m.turns[0].input == "Hello"
        assert m.turns[0].expect_events == ["turn.done"]

    def test_roundtrip(self):
        from corvin_console.aco.replay import ReplayManifest
        data = {
            "version": 1,
            "scenario": "roundtrip",
            "description": "test",
            "turns": [
                {"input": "hi", "expect_events": ["turn.start", "turn.done"],
                 "max_elapsed_ms": 5000, "tags": ["smoke"]}
            ],
        }
        m = ReplayManifest.from_dict(data)
        out = m.to_dict()
        assert out["scenario"] == "roundtrip"
        assert out["turns"][0]["max_elapsed_ms"] == 5000

    def test_from_file(self, tmp_path):
        from corvin_console.aco.replay import ReplayManifest
        f = tmp_path / "manifest.json"
        f.write_text(json.dumps({
            "version": 1, "scenario": "file", "description": "",
            "turns": [{"input": "Q"}],
        }))
        m = ReplayManifest.from_file(f)
        assert m.scenario == "file"


class TestReplayValidation:
    """Tests for the log-based replay validator (not the runner)."""

    def _events(self, n_turns: int = 1) -> list[dict]:
        events = []
        for i in range(n_turns):
            events.append({"ts": f"2026-06-28T10:0{i}:00Z", "event": "turn.start",
                           "prompt_preview": f"hello {i}"})
            events.append({"ts": f"2026-06-28T10:0{i}:01Z", "event": "turn.done",
                           "rc": 0, "elapsed_ms": 1200})
        return events

    def test_check_expect_events_all_present(self):
        from corvin_console.aco.replay import _check_expect_events
        events = [{"event": "turn.start"}, {"event": "turn.done"}]
        assert _check_expect_events(events, ["turn.start", "turn.done"]) == []

    def test_check_expect_events_missing(self):
        from corvin_console.aco.replay import _check_expect_events
        events = [{"event": "turn.start"}]
        missing = _check_expect_events(events, ["turn.start", "turn.done"])
        assert missing == ["turn.done"]

    def test_check_expect_fields_match(self):
        from corvin_console.aco.replay import _check_expect_fields
        events = [{"event": "turn.done", "rc": 0, "elapsed_ms": 500}]
        assert _check_expect_fields(events, {"event": "turn.done", "rc": 0}) == []

    def test_check_expect_fields_mismatch(self):
        from corvin_console.aco.replay import _check_expect_fields
        events = [{"event": "turn.done", "rc": 1}]
        missing = _check_expect_fields(events, {"event": "turn.done", "rc": 0})
        assert missing  # rc=0 not satisfied


# ── Layer 3: Anomaly detector ─────────────────────────────────────────────────

class TestAnomalyDetector:
    def test_clean_log_no_anomalies(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
            {"ts": "2026-06-28T10:00:01Z", "event": "turn.done", "rc": 0, "elapsed_ms": 800},
        ])
        anomalies = scan_session(tmp_path)
        assert anomalies == []

    def test_stalled_turn_detected(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        _write_log(tmp_path, [
            # turn.start without turn.done
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
        ])
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "stalled_turn" in classes

    def test_in_flight_turn_not_flagged_stalled(self, tmp_path):
        """A turn.start from just now (still legitimately running) must NOT be
        flagged — only one older than TURN_TIMEOUT_MS with no turn.done is a
        real stall. Regression for the missing time gate (WA-12)."""
        from datetime import datetime, timezone

        from corvin_console.aco.anomaly_detector import scan_session
        now_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_log(tmp_path, [
            {"ts": now_ts, "event": "turn.start"},
        ])
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "stalled_turn" not in classes

    def test_delegation_orphan_detected(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
            {"ts": "2026-06-28T10:00:01Z", "event": "delegation.decision",
             "will_delegate": True},
            # no acs.run.start follows
            {"ts": "2026-06-28T10:00:02Z", "event": "turn.done", "rc": 0, "elapsed_ms": 900},
        ])
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "delegation_orphan" in classes

    def test_delegation_no_orphan_when_acs_follows(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
            {"ts": "2026-06-28T10:00:01Z", "event": "delegation.decision",
             "will_delegate": True},
            {"ts": "2026-06-28T10:00:02Z", "event": "acs.run.start", "run_id": "r1"},
            {"ts": "2026-06-28T10:00:03Z", "event": "acs.run.done", "status": "success"},
            {"ts": "2026-06-28T10:00:04Z", "event": "turn.done", "rc": 0, "elapsed_ms": 3000},
        ])
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "delegation_orphan" not in classes

    def test_acs_error_rate_high(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        events = []
        for i in range(10):
            events.append({"ts": f"2026-06-28T10:0{i}:00Z", "event": "acs.run.done",
                           "status": "error" if i >= 8 else "success"})
        _write_log(tmp_path, events)
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "acs_error_rate" in classes

    def test_acs_error_rate_ok(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        # 1/20 = 5% — below 10% threshold
        events = [
            {"ts": f"2026-06-28T10:00:0{i:02d}Z", "event": "acs.run.done",
             "status": "error" if i == 0 else "success"}
            for i in range(20)
        ]
        _write_log(tmp_path, events)
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "acs_error_rate" not in classes

    def test_ws_instability_detected(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        events = [
            {"ts": f"2026-06-28T10:0{i}:00Z", "event": "ws.close", "code": 1006, "wasClean": False}
            for i in range(4)
        ]
        _write_log(tmp_path, events)
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "ws_instability" in classes

    def test_stream_error_without_delta(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
            {"ts": "2026-06-28T10:00:01Z", "event": "stream.error",
             "message": "engine timeout"},
        ])
        anomalies = scan_session(tmp_path)
        classes = [a.anomaly_class for a in anomalies]
        assert "empty_response_error" in classes

    def test_empty_log_no_anomalies(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session
        anomalies = scan_session(tmp_path)
        assert anomalies == []

    def test_scan_to_dict_shape(self, tmp_path):
        from corvin_console.aco.anomaly_detector import scan_session_to_dict
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
        ])
        result = scan_session_to_dict(tmp_path)
        assert "total" in result
        assert "anomalies" in result
        assert result["high"] >= 1  # stalled turn = HIGH

    def test_pii_stripped_from_evidence(self, tmp_path):
        """prompt_preview must not appear in to_dict() evidence (GDPR)."""
        from corvin_console.aco.anomaly_detector import scan_session_to_dict
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start",
             "prompt_preview": "SECRET USER MESSAGE"},
        ])
        result = scan_session_to_dict(tmp_path)
        result_str = str(result)
        assert "SECRET USER MESSAGE" not in result_str
        assert "prompt_preview" not in result_str


# ── Layer 4: Diagnosis ────────────────────────────────────────────────────────

class TestDiagnosis:
    def test_diagnose_stalled_turn(self):
        from corvin_console.aco.anomaly_detector import Anomaly, SEVERITY_HIGH
        from corvin_console.aco.diagnosis import diagnose_anomaly
        a = Anomaly("stalled_turn", SEVERITY_HIGH, "test stall", [])
        report = diagnose_anomaly(a)
        assert report.anomaly_class == "stalled_turn"
        assert "chat_runtime.py" in report.layers
        assert len(report.repro_steps) > 0
        assert "ADR-0174" in report.adr_refs

    def test_diagnose_unknown_class(self):
        from corvin_console.aco.anomaly_detector import Anomaly, SEVERITY_HIGH
        from corvin_console.aco.diagnosis import diagnose_anomaly
        a = Anomaly("totally_new_class", SEVERITY_HIGH, "some message", [])
        report = diagnose_anomaly(a)
        assert report.anomaly_class == "totally_new_class"
        assert "unknown" in report.layers

    def test_diagnose_only_high_and_critical(self, tmp_path):
        from corvin_console.aco.diagnosis import diagnose_session
        from corvin_console.aco.anomaly_detector import scan_session_to_dict
        # stalled turn (HIGH) + ws instability (MED)
        events = []
        events.append({"ts": "2026-06-28T10:00:00Z", "event": "turn.start"})
        for i in range(4):
            events.append({"ts": f"2026-06-28T10:0{i+1}:00Z", "event": "ws.close",
                           "wasClean": False, "code": 1006})
        _write_log(tmp_path, events)

        result = diagnose_session(tmp_path)
        # anomaly_count should be >= 2
        assert result["anomaly_count"] >= 1
        # Only HIGH is diagnosed (stalled_turn), not MEDIUM (ws_instability)
        for r in result["reports"]:
            assert r["severity"] in ("CRITICAL", "HIGH")

    def test_to_dict_shape(self, tmp_path):
        from corvin_console.aco.diagnosis import diagnose_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
        ])
        result = diagnose_session(tmp_path)
        assert "anomaly_count" in result
        assert "diagnosed_count" in result
        assert "reports" in result
        for r in result["reports"]:
            assert "hypothesis" in r
            assert "repro_steps" in r


# ── Layer 5: Self-Repair + Shared Logger ─────────────────────────────────────

class TestDebugLogger:
    def test_write_event(self, tmp_path):
        from corvin_console.aco.debug_logger import write_event
        write_event(tmp_path, "test.event", foo="bar", count=42)
        log = tmp_path / "chat_debug.jsonl"
        assert log.exists()
        data = json.loads(log.read_text().strip())
        assert data["event"] == "test.event"
        assert data["foo"] == "bar"
        assert data["count"] == 42
        assert "ts" in data

    def test_write_event_non_serializable(self, tmp_path):
        from corvin_console.aco.debug_logger import write_event
        write_event(tmp_path, "test.ns", obj=object())  # non-serializable → str
        log = tmp_path / "chat_debug.jsonl"
        data = json.loads(log.read_text().strip())
        assert isinstance(data["obj"], str)

    def test_write_event_silent_on_bad_path(self):
        from corvin_console.aco.debug_logger import write_event
        write_event(Path("/nonexistent/bad/path"), "x")  # must not raise


class TestRepairLayer5:
    def test_stalled_turn_repair_converges(self, tmp_path):
        from corvin_console.aco.repair import repair_session
        from corvin_console.aco.anomaly_detector import scan_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
        ])
        before = scan_session(tmp_path)
        assert any(a.anomaly_class == "stalled_turn" for a in before)

        result = repair_session(tmp_path)
        assert result.delta_loss >= 1
        assert result.convergence_reached
        assert result.events_written == 1
        assert any(a.action_id == "turn_flush" for a in result.actions_applied)

        after = scan_session(tmp_path)
        assert not any(a.anomaly_class == "stalled_turn" for a in after)

    def test_delegation_orphan_repair(self, tmp_path):
        from corvin_console.aco.repair import repair_session
        from corvin_console.aco.anomaly_detector import scan_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
            {"ts": "2026-06-28T10:00:01Z", "event": "delegation.decision",
             "will_delegate": True},
            {"ts": "2026-06-28T10:00:02Z", "event": "turn.done", "rc": 0, "elapsed_ms": 900},
        ])
        before = scan_session(tmp_path)
        assert any(a.anomaly_class == "delegation_orphan" for a in before)

        result = repair_session(tmp_path)
        assert result.delta_loss >= 1
        after = scan_session(tmp_path)
        assert not any(a.anomaly_class == "delegation_orphan" for a in after)

    def test_acs_error_rate_repair(self, tmp_path):
        from corvin_console.aco.repair import repair_session
        from corvin_console.aco.anomaly_detector import scan_session
        events = [
            {"ts": f"2026-06-28T10:0{i}:00Z", "event": "acs.run.done",
             "status": "error" if i >= 8 else "success"}
            for i in range(10)
        ]
        _write_log(tmp_path, events)
        before = scan_session(tmp_path)
        assert any(a.anomaly_class == "acs_error_rate" for a in before)

        result = repair_session(tmp_path)
        assert result.delta_loss >= 1
        after = scan_session(tmp_path)
        assert not any(a.anomaly_class == "acs_error_rate" for a in after)

    def test_ws_instability_repair(self, tmp_path):
        from corvin_console.aco.repair import repair_session
        from corvin_console.aco.anomaly_detector import scan_session, SEVERITY_HIGH
        # ≥10 closes → severity HIGH (below 10 = MEDIUM, not repaired by Layer 5)
        events = [
            {"ts": f"2026-06-28T10:{i:02d}:00Z", "event": "ws.close",
             "code": 1006, "wasClean": False}
            for i in range(10)
        ]
        _write_log(tmp_path, events)
        before = scan_session(tmp_path)
        ws_anomalies = [a for a in before if a.anomaly_class == "ws_instability"]
        assert ws_anomalies and ws_anomalies[0].severity == SEVERITY_HIGH

        result = repair_session(tmp_path)
        assert result.delta_loss >= 1
        after = scan_session(tmp_path)
        assert not any(a.anomaly_class == "ws_instability" for a in after)

    def test_dry_run_writes_nothing(self, tmp_path):
        from corvin_console.aco.repair import repair_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
        ])
        log_before = (tmp_path / "chat_debug.jsonl").read_text()
        result = repair_session(tmp_path, dry_run=True)
        log_after = (tmp_path / "chat_debug.jsonl").read_text()

        assert log_before == log_after          # nothing written in dry_run
        assert result.dry_run is True
        assert result.events_written == 0
        assert any(a.status == "dry_run" for a in result.actions_applied)

    def test_clean_session_no_actions(self, tmp_path):
        from corvin_console.aco.repair import repair_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
            {"ts": "2026-06-28T10:00:01Z", "event": "turn.done", "rc": 0, "elapsed_ms": 500},
        ])
        result = repair_session(tmp_path)
        assert result.events_written == 0
        assert result.convergence_reached  # 0 HIGH/CRITICAL
        assert all(a.status == "skipped" for a in result.actions_skipped)

    def test_repair_result_to_dict_shape(self, tmp_path):
        from corvin_console.aco.repair import repair_session
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.start"},
        ])
        result = repair_session(tmp_path)
        d = result.to_dict()
        assert "dry_run" in d
        assert "before" in d and "after" in d
        assert "delta_loss" in d
        assert "convergence_reached" in d
        assert "actions_applied" in d
        assert "total_events_written" in d

    def test_is_acs_throttled_active(self, tmp_path):
        from corvin_console.aco.repair import is_acs_throttled
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "repair.acs_throttle_on",
             "throttle_turns": 3},
            # only 1 turn.done after the throttle
            {"ts": "2026-06-28T10:00:01Z", "event": "turn.done", "rc": 0, "elapsed_ms": 100},
        ])
        assert is_acs_throttled(tmp_path) is True

    def test_is_acs_throttled_expired(self, tmp_path):
        from corvin_console.aco.repair import is_acs_throttled
        events = [
            {"ts": "2026-06-28T10:00:00Z", "event": "repair.acs_throttle_on",
             "throttle_turns": 3},
        ]
        for i in range(3):
            events.append({
                "ts": f"2026-06-28T10:0{i+1}:00Z",
                "event": "turn.done", "rc": 0, "elapsed_ms": 100,
            })
        _write_log(tmp_path, events)
        assert is_acs_throttled(tmp_path) is False

    def test_is_acs_throttled_no_event(self, tmp_path):
        from corvin_console.aco.repair import is_acs_throttled
        _write_log(tmp_path, [
            {"ts": "2026-06-28T10:00:00Z", "event": "turn.done", "rc": 0, "elapsed_ms": 200},
        ])
        assert is_acs_throttled(tmp_path) is False


import unittest

# ── Boot Healer Tests ─────────────────────────────────────────────────────────

class TestBootHealer(unittest.TestCase):
    """Test the ACO Boot-Healer background task."""

    def test_discover_tenants_returns_list(self):
        from corvin_console.aco.boot_healer import _discover_tenants
        tenants = _discover_tenants()
        self.assertIsInstance(tenants, list)
        self.assertGreater(len(tenants), 0)

    def test_start_boot_healer_returns_task(self):
        import asyncio
        from corvin_console.aco.boot_healer import start_boot_healer

        async def _run():
            task = start_boot_healer(boot_delay=999, cycle_interval=999)
            self.assertIsInstance(task, asyncio.Task)
            self.assertEqual(task.get_name(), "aco-boot-healer")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

    def test_find_all_workdirs_covers_all_channels(self):
        """_find_all_workdirs must find workdirs regardless of channel type."""
        import tempfile
        from corvin_console.aco.boot_healer import _find_all_workdirs

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Simulate sessions/ with web, discord, voice, cli channels
            (tmp_path / "web:abc123").mkdir()
            (tmp_path / "web:abc123" / "chat_debug.jsonl").write_text('{"event":"turn.start"}\n')
            (tmp_path / "discord:12345").mkdir()
            (tmp_path / "discord:12345" / "chat_debug.jsonl").write_text('{"event":"turn.start"}\n')
            voice_dir = tmp_path / "voice" / "telegram" / "user42"
            voice_dir.mkdir(parents=True)
            (voice_dir / "chat_debug.jsonl").write_text('{"event":"turn.start"}\n')
            # cli session with no chat_debug.jsonl — must NOT appear
            (tmp_path / "cli:test").mkdir()

            import unittest.mock as mock
            with mock.patch(
                "corvin_console.aco.boot_healer._find_all_workdirs",
                wraps=lambda tenant_id: [
                    p.parent for p in tmp_path.rglob("chat_debug.jsonl")
                ],
            ):
                result = [p.parent for p in tmp_path.rglob("chat_debug.jsonl")]

            self.assertEqual(len(result), 3)
            names = {p.name for p in result}
            self.assertIn("web:abc123", names)
            self.assertIn("discord:12345", names)
            self.assertIn("user42", names)

    def test_heal_cycle_does_not_raise(self):
        """_heal_cycle must never raise even with missing or empty sessions."""
        import asyncio
        from corvin_console.aco.boot_healer import _heal_cycle

        async def _run():
            await _heal_cycle()

        asyncio.run(_run())  # Must complete without exception

    def test_healer_cancels_cleanly_on_shutdown(self):
        """Cancelling the task during boot_delay must not raise."""
        import asyncio
        from corvin_console.aco.boot_healer import start_boot_healer

        async def _run():
            task = start_boot_healer(boot_delay=60, cycle_interval=300)
            await asyncio.sleep(0.05)  # let it start sleeping
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.assertTrue(task.done())

        asyncio.run(_run())


# ── Engine Healer Tests ────────────────────────────────────────────────────────

class TestEngineHealer(unittest.TestCase):
    """Test the ACO Engine + Voice Healer."""

    def test_check_tts_readiness_returns_tuple(self):
        """check_tts_readiness must return (bool, str, str) without crashing."""
        from corvin_console.aco.engine_healer import check_tts_readiness
        ok, provider, action = check_tts_readiness()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(provider, str)
        self.assertIsInstance(action, str)
        # At minimum, one TTS should be available (edge-tts is a base dep)
        if ok:
            self.assertIn(provider, ("openai", "edge", "piper"))

    def test_check_stt_readiness_returns_tuple(self):
        """check_stt_readiness must return (bool, str, str) without crashing."""
        from corvin_console.aco.engine_healer import check_stt_readiness
        ok, provider, action = check_stt_readiness()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(provider, str)
        if ok:
            self.assertIn(provider, ("pywhispercpp", "openai_whisper"))

    def test_run_readiness_check_does_not_raise(self):
        """run_readiness_check must complete without crashing, even on bad config."""
        from corvin_console.aco.engine_healer import run_readiness_check, EngineHealResult
        result = run_readiness_check("_default")
        self.assertIsInstance(result, EngineHealResult)
        self.assertIsInstance(result.tts_ok, bool)
        self.assertIsInstance(result.stt_ok, bool)
        self.assertIsInstance(result.warnings, list)

    def test_heal_result_to_audit_details_shape(self):
        """to_audit_details must return a dict with required keys."""
        from corvin_console.aco.engine_healer import EngineHealResult
        r = EngineHealResult(
            engine_ok=True, engine_id="hermes", engine_action="started_ollama",
            tts_ok=True, tts_provider="edge", tts_action="installed_edge_tts",
            stt_ok=False, stt_provider="none", warnings=["STT unavailable"],
        )
        d = r.to_audit_details()
        for key in ("engine_ok", "engine_id", "engine_action",
                    "tts_ok", "tts_provider", "tts_action",
                    "stt_ok", "stt_provider", "warnings"):
            self.assertIn(key, d)
        self.assertEqual(d["engine_action"], "started_ollama")
        self.assertEqual(d["tts_action"], "installed_edge_tts")

    def test_hermes_reachable_does_not_raise(self):
        """_hermes_reachable must return bool, not raise on connection refused."""
        from corvin_console.aco.engine_healer import _hermes_reachable
        result = _hermes_reachable()
        self.assertIsInstance(result, bool)

    def test_heal_cycle_with_engine_healer_does_not_raise(self):
        """_heal_cycle (with engine_healer integrated) must not raise."""
        import asyncio
        from corvin_console.aco.boot_healer import _heal_cycle

        async def _run():
            await _heal_cycle()

        asyncio.run(_run())


# ── Integrity Monitor Tests (Immunsystem) ─────────────────────────────────────

class TestIntegrityMonitor(unittest.TestCase):
    """Tests für den ACO Integrity Monitor (Immunsystem)."""

    def test_run_integrity_scan_does_not_raise(self):
        """run_integrity_scan muss ohne Exception abschließen, auch auf leerem System."""
        from corvin_console.aco.integrity_monitor import run_integrity_scan
        findings = run_integrity_scan("_default")
        self.assertIsInstance(findings, list)

    def test_integrity_finding_to_dict(self):
        """IntegrityFinding.to_dict() muss alle Pflichtfelder enthalten."""
        from corvin_console.aco.integrity_monitor import IntegrityFinding
        f = IntegrityFinding(
            severity="CRITICAL",
            check_name="test_check",
            message="Test-Befund",
            action_taken="alerted",
            detail={"key": "value"},
        )
        d = f.to_dict()
        for key in ("severity", "check_name", "message", "action_taken", "detail"):
            self.assertIn(key, d)
        self.assertEqual(d["severity"], "CRITICAL")

    def test_check_config_integrity_baseline(self):
        """check_config_integrity setzt Baseline beim ersten Aufruf, meldet kein Finding."""
        from corvin_console.aco.integrity_monitor import check_config_integrity
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            config = tmp_path / "tenant.corvin.yaml"
            config.write_text("spec:\n  default_engine: claude_code\n")

            with mock.patch(
                "corvin_console.aco.integrity_monitor._tenant_global_dir",
                return_value=tmp_path,
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._corvin_home",
                return_value=tmp_path.parent,
            ):
                findings = check_config_integrity("_default")
                self.assertEqual(findings, [])

    def test_check_config_integrity_detects_change(self):
        """check_config_integrity erkennt Änderung nach Baseline-Setzung."""
        from corvin_console.aco.integrity_monitor import check_config_integrity
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            config = tmp_path / "tenant.corvin.yaml"
            config.write_text("spec:\n  default_engine: claude_code\n")

            # Alter Hash (simuliert anderen gespeicherten Zustand)
            fake_state = {
                "tenant_config_sha256": "deadbeef" + "0" * 56,
                "tenant_config_ts": "2026-01-01T00:00:00Z",
            }

            with mock.patch(
                "corvin_console.aco.integrity_monitor._tenant_global_dir",
                return_value=tmp_path,
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._corvin_home",
                return_value=tmp_path.parent,
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._load_checksum_state",
                return_value=fake_state,
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._save_checksum_state",
            ):
                findings = check_config_integrity("_default")

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].check_name, "config_integrity")
            self.assertEqual(findings[0].severity, "HIGH")

    def test_check_engine_config_safe_known_engines(self):
        """check_engine_config_safe gibt kein Finding bei bekannten Engines."""
        from corvin_console.aco.integrity_monitor import check_engine_config_safe
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            config = tmp_path / "tenant.corvin.yaml"
            config.write_text("spec:\n  default_engine: hermes\n")
            with mock.patch(
                "corvin_console.aco.integrity_monitor._tenant_global_dir",
                return_value=tmp_path,
            ):
                findings = check_engine_config_safe("_default")
                self.assertEqual(findings, [])

    def test_check_engine_config_safe_detects_redirect(self):
        """check_engine_config_safe erkennt Engine-Redirect auf fremden Host als CRITICAL."""
        from corvin_console.aco.integrity_monitor import check_engine_config_safe
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            config = tmp_path / "tenant.corvin.yaml"
            config.write_text("spec:\n  default_engine: http://evil.hacker.com/v1\n")
            with mock.patch(
                "corvin_console.aco.integrity_monitor._tenant_global_dir",
                return_value=tmp_path,
            ):
                findings = check_engine_config_safe("_default")
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].severity, "CRITICAL")
                self.assertEqual(findings[0].check_name, "engine_config_safe")

    def test_check_engine_config_safe_allows_localhost(self):
        """check_engine_config_safe erlaubt localhost-URLs (lokaler Ollama)."""
        from corvin_console.aco.integrity_monitor import check_engine_config_safe
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            config = tmp_path / "tenant.corvin.yaml"
            config.write_text("spec:\n  default_engine: http://localhost:11434\n")
            with mock.patch(
                "corvin_console.aco.integrity_monitor._tenant_global_dir",
                return_value=tmp_path,
            ):
                findings = check_engine_config_safe("_default")
                self.assertEqual(findings, [])

    def test_check_compliance_gate_baseline_no_files(self):
        """Compliance-Gate-Check gibt kein Finding wenn Dateien nicht existieren."""
        from corvin_console.aco.integrity_monitor import check_compliance_gate_integrity
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            empty_root = Path(td)
            with mock.patch(
                "corvin_console.aco.integrity_monitor._repo_root",
                return_value=empty_root,
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._load_checksum_state",
                return_value={},
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._save_checksum_state",
            ):
                findings = check_compliance_gate_integrity()
                self.assertEqual(findings, [])

    def test_check_compliance_gate_detects_tampering(self):
        """Compliance-Gate-Check erkennt Datei-Änderung nach Baseline."""
        from corvin_console.aco.integrity_monitor import check_compliance_gate_integrity
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            fake_dir = tmp_path / "operator" / "bridges" / "shared"
            fake_dir.mkdir(parents=True)
            target = fake_dir / "house_rules.py"
            target.write_text("# original content")

            fake_state = {
                "compliance_file_hashes": {
                    "house_rules": "deadbeef" + "0" * 56,
                }
            }

            with mock.patch(
                "corvin_console.aco.integrity_monitor._repo_root",
                return_value=tmp_path,
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._load_checksum_state",
                return_value=dict(fake_state),
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._save_checksum_state",
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._COMPLIANCE_FILES",
                [("operator/bridges/shared/house_rules.py", "house_rules")],
            ):
                findings = check_compliance_gate_integrity()
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].check_name, "compliance_gate_integrity")
                self.assertEqual(findings[0].severity, "HIGH")

    def test_alert_operator_writes_file(self):
        """alert_operator schreibt einen Alert-File für CRITICAL-Befunde."""
        from corvin_console.aco.integrity_monitor import alert_operator, IntegrityFinding
        import unittest.mock as mock

        findings = [
            IntegrityFinding(severity="CRITICAL", check_name="test", message="Test-Alert"),
            IntegrityFinding(severity="HIGH", check_name="test2", message="High-Finding"),
        ]

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            with mock.patch(
                "corvin_console.aco.integrity_monitor._corvin_home",
                return_value=tmp_path,
            ), mock.patch(
                "corvin_console.aco.integrity_monitor._now_iso",
                return_value="2026-06-28T10:00:00Z",
            ):
                alert_operator(findings, "_default")
                alerts_dir = tmp_path / "alerts"
                alert_files = list(alerts_dir.glob("integrity-*.json"))
                self.assertEqual(len(alert_files), 1)
                content = json.loads(alert_files[0].read_text())
                self.assertEqual(content["severity"], "CRITICAL")
                self.assertEqual(content["finding_count"], 1)  # nur CRITICALs

    def test_get_alert_count(self):
        """get_alert_count zählt ungelesene Integrity-Alert-Files."""
        from corvin_console.aco.integrity_monitor import get_alert_count
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            alerts_dir = tmp_path / "alerts"
            alerts_dir.mkdir()
            (alerts_dir / "integrity-2026-01.json").write_text("{}")
            (alerts_dir / "integrity-2026-02.json").write_text("{}")
            (alerts_dir / "other-file.json").write_text("{}")  # zählt nicht

            with mock.patch(
                "corvin_console.aco.integrity_monitor._corvin_home",
                return_value=tmp_path,
            ):
                count = get_alert_count()
                self.assertEqual(count, 2)

    def test_boot_healer_with_integrity_scan_does_not_raise(self):
        """_heal_cycle mit integriertem Integrity-Monitor darf nicht werfen."""
        import asyncio
        from corvin_console.aco.boot_healer import _heal_cycle

        async def _run():
            await _heal_cycle()

        asyncio.run(_run())
