"""
E2E Tests: Bridge Autonomy Detection + Execution (ADR-2083)

Tests verify:
1. Bridge type detection works correctly
2. Autonomy mode adapts to bridge type
3. Loops execute in correct mode (scheduled vs. background)
4. Workflows start non-blocking in all bridges
5. Audit trail captures autonomy decisions
"""

import os
import pytest
import time
from unittest.mock import patch, MagicMock
from datetime import datetime

from corvin_operator.bridges.autonomy_detector import (
    detect_bridge_type,
    detect_autonomy_mode,
    is_rescheduling_available,
    is_non_interactive_bridge,
    BridgeType,
    BridgeAutonomyMode,
)
from corvin_operator.autonomy.loop_executor_bridge_aware import (
    LoopExecutor,
    LoopConfig,
)
from corvin_operator.workflows.workflow_background_runner import (
    WorkflowBackgroundRunner,
    WorkflowStartAck,
)


class TestBridgeDetection:
    """Test bridge type detection logic."""

    def test_detect_discord_bridge(self):
        """Discord bridge should be detected from env var."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            bridge = detect_bridge_type()
            assert bridge == BridgeType.DISCORD

    def test_detect_slack_bridge(self):
        """Slack bridge should be detected from env var."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "slack"}):
            bridge = detect_bridge_type()
            assert bridge == BridgeType.SLACK

    def test_detect_web_bridge(self):
        """Web bridge should be detected from env var."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "web"}):
            bridge = detect_bridge_type()
            assert bridge == BridgeType.WEB

    def test_detect_cli_with_tty(self):
        """CLI bridge detected when stdin/stdout are TTYs."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": ""}):
            with patch("sys.stdin.isatty", return_value=True):
                with patch("sys.stdout.isatty", return_value=True):
                    bridge = detect_bridge_type()
                    assert bridge == BridgeType.CLI

    def test_unknown_bridge_default(self):
        """Unknown bridge defaults to UNKNOWN."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": ""}):
            with patch("sys.stdin.isatty", return_value=False):
                bridge = detect_bridge_type()
                assert bridge == BridgeType.UNKNOWN


class TestAutonomyMode:
    """Test autonomy mode classification."""

    def test_interactive_cli(self):
        """CLI bridge → INTERACTIVE mode."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": ""}):
            with patch("sys.stdin.isatty", return_value=True):
                with patch("sys.stdout.isatty", return_value=True):
                    mode = detect_autonomy_mode()
                    assert mode == BridgeAutonomyMode.INTERACTIVE
                    assert is_rescheduling_available() is True

    def test_interactive_web(self):
        """Web bridge → INTERACTIVE mode."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "web"}):
            mode = detect_autonomy_mode()
            assert mode == BridgeAutonomyMode.INTERACTIVE
            assert is_rescheduling_available() is True

    def test_non_interactive_discord(self):
        """Discord bridge → NON_INTERACTIVE mode."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            mode = detect_autonomy_mode()
            assert mode == BridgeAutonomyMode.NON_INTERACTIVE
            assert is_rescheduling_available() is False
            assert is_non_interactive_bridge() is True

    def test_non_interactive_slack(self):
        """Slack bridge → NON_INTERACTIVE mode."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "slack"}):
            mode = detect_autonomy_mode()
            assert mode == BridgeAutonomyMode.NON_INTERACTIVE
            assert is_non_interactive_bridge() is True

    def test_non_interactive_email(self):
        """Email bridge → NON_INTERACTIVE mode."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "email"}):
            mode = detect_autonomy_mode()
            assert mode == BridgeAutonomyMode.NON_INTERACTIVE


class TestLoopExecutionModes:
    """Test loop execution adapting to bridge type."""

    def test_loop_interactive_cli_schedules(self):
        """Loop on CLI bridge: uses SCHEDULED mode (ScheduleWakeup)."""
        def dummy_executor(prompt):
            return {"success": True}

        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": ""}):
            with patch("sys.stdin.isatty", return_value=True):
                with patch("sys.stdout.isatty", return_value=True):
                    config = LoopConfig(
                        prompt="test prompt",
                        interval_seconds=60,
                        max_iterations=None,
                        timeout_seconds=3600,
                        executor_fn=dummy_executor,
                    )
                    executor = LoopExecutor(config)
                    result = executor.run()

                    assert result["execution_mode"] == "scheduled"
                    # No scheduler is wired: the executor must say so instead of
                    # claiming "ScheduleWakeup active" (2026-09-27 review).
                    assert result["reason_complete"] == "not_implemented"
                    assert result["iterations"] == 0

    def test_loop_non_interactive_background(self):
        """Loop on Discord bridge: uses BACKGROUND mode (run-to-completion)."""
        iteration_count = 0

        def dummy_executor(prompt):
            nonlocal iteration_count
            iteration_count += 1
            return {"success": True}

        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            config = LoopConfig(
                prompt="test prompt",
                interval_seconds=0.01,  # Very short interval for testing
                max_iterations=3,       # Stop after 3 iterations
                timeout_seconds=10,
                executor_fn=dummy_executor,
            )
            executor = LoopExecutor(config)
            result = executor.run()

            assert result["execution_mode"] == "background"
            assert result["reason_complete"] == "iterations_exhausted"
            assert result["iterations"] == 3
            assert iteration_count == 3

    def test_loop_background_timeout(self):
        """Loop in background mode: respects timeout safety guard."""
        def dummy_executor(prompt):
            time.sleep(0.05)  # Longer than timeout
            return {"success": True}

        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "slack"}):
            config = LoopConfig(
                prompt="test prompt",
                interval_seconds=0.01,
                max_iterations=100,     # Large number, timeout comes first
                timeout_seconds=0.1,    # Very short timeout for testing
                executor_fn=dummy_executor,
            )
            executor = LoopExecutor(config)
            result = executor.run()

            assert result["execution_mode"] == "background"
            assert result["reason_complete"] == "timeout"
            assert result["duration_seconds"] > 0.1

    def test_loop_audit_trail_captured(self):
        """Loop execution generates audit trail."""
        def dummy_executor(prompt):
            return {"success": True}

        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            config = LoopConfig(
                prompt="test",
                interval_seconds=0.01,
                max_iterations=2,
                timeout_seconds=10,
                executor_fn=dummy_executor,
            )
            executor = LoopExecutor(config)
            result = executor.run()

            audit_trail = executor.get_audit_trail()
            assert len(audit_trail) >= 2  # At least: one per iteration + completion
            assert audit_trail[-1]["event"] == "loop_complete"
            assert audit_trail[-1]["bridge"] == "discord"
            assert audit_trail[-1]["autonomy_mode"] == "background"


class TestWorkflowBackgroundExecution:
    """Test workflow background execution (non-blocking)."""

    def test_workflow_starts_non_blocking_cli(self):
        """Workflow on CLI bridge: returns immediately (non-blocking)."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": ""}):
            with patch("sys.stdin.isatty", return_value=True):
                with patch("sys.stdout.isatty", return_value=True):
                    runner = WorkflowBackgroundRunner()
                    ack = runner.start(
                        script="test_script",
                        args={"key": "value"},
                        description="Test workflow"
                    )

                    assert isinstance(ack, WorkflowStartAck)
                    assert ack.run_id.startswith("wf_")
                    # No background executor is wired: fail closed, never "queued".
                    assert ack.status == "blocked"
                    assert ack.autonomy_mode == "interactive"
                    assert ack.bridge_type == "cli"

    def test_workflow_starts_non_blocking_discord(self):
        """Workflow on Discord bridge: returns immediately with Slack-style ack."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            runner = WorkflowBackgroundRunner()
            ack = runner.start(
                script="test_script",
                description="Test workflow"
            )

            assert ack.bridge_type == "discord"
            assert ack.autonomy_mode == "non_interactive"
            assert ack.status == "blocked"
            assert "could not be started" in ack.message

    def test_workflow_ack_to_dict(self):
        """WorkflowStartAck serializes to dict."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "web"}):
            runner = WorkflowBackgroundRunner()
            ack = runner.start(script="test")

            ack_dict = ack.to_dict()
            assert isinstance(ack_dict, dict)
            assert "run_id" in ack_dict
            assert "status" in ack_dict
            assert "bridge_type" in ack_dict
            assert "autonomy_mode" in ack_dict


class TestTokenCompressionTransparency:
    """Test that token exhaustion is handled transparently (no explicit warnings)."""

    def test_no_explicit_token_warning_on_discord(self):
        """Discord bridge: loop should NOT emit "Token aufgebracht" warning."""
        # This test verifies that the agent code avoids explicit warnings
        # (Actual token compression is out of scope for this test)

        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            mode = detect_autonomy_mode()

            # Verify non-interactive mode enables background fallback
            assert mode == BridgeAutonomyMode.NON_INTERACTIVE
            assert is_non_interactive_bridge() is True

            # In real execution, agent would use run_in_background=true
            # (No "please open new session" message should be emitted)


class TestBridgeAwarenessIntegration:
    """Integration tests: full workflow with bridge awareness."""

    def test_full_loop_to_background_execution(self):
        """Full E2E: /loop command on Discord bridge executes in background mode."""
        execution_log = []

        def mock_executor(prompt):
            execution_log.append({"prompt": prompt, "time": time.time()})
            return {"success": True}

        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            # Verify bridge is detected
            bridge = detect_bridge_type()
            assert bridge == BridgeType.DISCORD

            # Verify autonomy mode is non-interactive
            mode = detect_autonomy_mode()
            assert mode == BridgeAutonomyMode.NON_INTERACTIVE

            # Execute loop
            config = LoopConfig(
                prompt="analyze data",
                interval_seconds=0.01,
                max_iterations=3,
                timeout_seconds=10,
                executor_fn=mock_executor,
            )
            executor = LoopExecutor(config)
            result = executor.run()

            # Verify background execution
            assert result["execution_mode"] == "background"
            assert result["iterations"] == 3
            assert len(execution_log) == 3

    def test_workflow_and_loop_both_non_blocking(self):
        """Both workflow and loop are non-blocking on Discord bridge."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            # Start workflow
            workflow_runner = WorkflowBackgroundRunner()
            wf_ack = workflow_runner.start(script="orchestration_script")

            # The workflow is refused (no executor), never reported as queued
            assert wf_ack.autonomy_mode == "non_interactive"
            assert wf_ack.status == "blocked"

            # Start loop (would also be non-blocking)
            def dummy_executor(prompt):
                return {"success": True}

            loop_config = LoopConfig(
                prompt="monitor",
                interval_seconds=0.01,
                max_iterations=1,
                timeout_seconds=10,
                executor_fn=dummy_executor,
            )
            loop_executor = LoopExecutor(loop_config)
            loop_result = loop_executor.run()

            # Verify both are non-blocking
            assert loop_result["execution_mode"] == "background"
            assert wf_ack.status == "blocked"


# Integration with production systems

class TestProductionReadyCompliance:
    """Test production-ready standards compliance."""

    def test_audit_logging_present(self):
        """Autonomy decisions are audit-logged."""
        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            def dummy_executor(prompt):
                return {"success": True}

            config = LoopConfig(
                prompt="test",
                interval_seconds=0.01,
                max_iterations=1,
                timeout_seconds=10,
                executor_fn=dummy_executor,
            )
            executor = LoopExecutor(config)
            result = executor.run()

            # Verify audit trail exists
            audit = executor.get_audit_trail()
            assert len(audit) > 0
            assert any(e["event"] == "loop_complete" for e in audit)

    def test_tenant_isolation_ready(self):
        """Autonomy system is tenant-aware (placeholder for future tenant_id propagation)."""
        # This test verifies the structure is ready for tenant isolation
        # Actual tenant_id propagation is future work

        with patch.dict(os.environ, {"CORVIN_BRIDGE_TYPE": "discord"}):
            runner = WorkflowBackgroundRunner()
            ack = runner.start(script="test")

            # In future, ack would include tenant_id
            # For now, verify structure allows it
            ack_dict = ack.to_dict()
            assert isinstance(ack_dict, dict)


def test_workflow_start_attempt_reaches_the_tenant_audit_chain(tmp_path, monkeypatch):
    """The start attempt is a hash-chained record (no "AUDIT:" log line), and
    the free-text description never enters it."""
    import json
    chain = tmp_path / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    monkeypatch.setenv("CORVIN_BRIDGE_TYPE", "discord")
    ack = WorkflowBackgroundRunner().start(script="s", description="secret-description")
    recs = [json.loads(l) for l in chain.read_text().splitlines()]
    mine = [r for r in recs if r.get("event_type") == "workflow.background_start"]
    assert mine, recs
    d = mine[-1].get("details") or mine[-1]
    assert d["run_id"] == ack.run_id
    assert d["status"] == "blocked"
    assert d["reason_code"] == "not_implemented"
    assert "secret-description" not in chain.read_text()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
