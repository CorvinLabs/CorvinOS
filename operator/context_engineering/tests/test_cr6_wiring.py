"""
CR-6 Guard Integration Wiring Tests

Verifies that the guard integration hook correctly filters suggestions.
"""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

# Import wiring hook
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from guard_integration_hook import (
    ContextSuggestionGate,
    console_suggest_contexts_with_guard,
    agent_filter_context_pool_with_guard,
)


class TestContextSuggestionGate:
    """CR-6: Test the suggestion gate."""

    def test_gate_loads_profile_and_blocks_dangerous(self):
        """CR-6: Gate loads profile and blocks dangerous contexts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)

            # Create a mock profile
            profile = {
                "version": "202608071800",
                "danger_zones": ["skipping tests when urgent (70% fail)"],
            }
            profile_file = profile_dir / "tenant-baseline.json"
            profile_file.write_text(json.dumps(profile))

            # Create gate
            gate = ContextSuggestionGate(profile_dir)

            # Test filtering
            suggested = ["adr-0269", "skill-e2e-wiring", "memory-phase3"]
            approved, blocked = gate.filter_suggestions(
                suggested,
                user_id="user1",
                task_conditions={"urgency": "asap"},
            )

            # Should block e2e-wiring when urgent
            assert "skill-e2e-wiring" not in approved
            assert len(blocked) > 0

    def test_gate_passes_all_when_no_danger(self):
        """CR-6: Gate passes all contexts when no danger pattern matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)

            # Create profile with no danger zones
            profile = {
                "version": "202608071800",
                "danger_zones": [],
            }
            profile_file = profile_dir / "tenant-baseline.json"
            profile_file.write_text(json.dumps(profile))

            gate = ContextSuggestionGate(profile_dir)

            suggested = ["adr-0269", "skill-e2e-wiring", "memory-phase3"]
            approved, blocked = gate.filter_suggestions(
                suggested,
                user_id="user1",
                task_conditions={"urgency": "low"},
            )

            # Should pass all when not urgent
            assert len(approved) == len(suggested)
            assert len(blocked) == 0

    def test_gate_audit_log(self):
        """CR-6: Gate records blocks in audit log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)

            profile = {
                "version": "202608071800",
                "danger_zones": ["skipping tests when urgent (70% fail)"],
            }
            profile_file = profile_dir / "tenant-baseline.json"
            profile_file.write_text(json.dumps(profile))

            gate = ContextSuggestionGate(profile_dir)

            gate.filter_suggestions(
                ["skill-e2e-wiring"],
                user_id="user1",
                task_conditions={"urgency": "asap"},
            )

            audit_log = gate.get_blocked_audit_log()
            assert len(audit_log) > 0
            assert audit_log[0]["type"] == "context_blocked"


class TestConsoleIntegration:
    """CR-6: Test console integration hook."""

    def test_console_suggest_with_guard(self):
        """CR-6: Console filters suggestions through guard."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)

            profile = {
                "version": "202608071800",
                "danger_zones": ["skipping tests when urgent (70% fail)"],
            }
            profile_file = profile_dir / "tenant-baseline.json"
            profile_file.write_text(json.dumps(profile))

            suggested = ["adr-0269", "skill-e2e-wiring", "memory-phase3"]
            approved, blocked = console_suggest_contexts_with_guard(
                suggested,
                user_id="user1",
                task_conditions={"urgency": "asap"},
                profile_dir=profile_dir,
            )

            assert len(approved) < len(suggested)
            assert len(blocked) > 0


class TestAgentIntegration:
    """CR-6: Test agent integration hook."""

    def test_agent_filter_context_pool(self):
        """CR-6: Agent filters context pool through guard."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = Path(tmpdir)

            profile = {
                "version": "202608071800",
                "danger_zones": ["skipping tests when urgent (70% fail)"],
            }
            profile_file = profile_dir / "tenant-baseline.json"
            profile_file.write_text(json.dumps(profile))

            context_pool = {
                "adrs": ["ADR-0269", "ADR-0270"],
                "skills": ["skill-e2e-wiring", "skill-testing"],
                "memory": ["memory-phase3"],
            }

            filtered = agent_filter_context_pool_with_guard(
                context_pool,
                user_id="user1",
                task_conditions={"urgency": "asap"},
                profile_dir=profile_dir,
            )

            # Should have same keys but possibly fewer items
            assert set(filtered.keys()) == set(context_pool.keys())
            # e2e-wiring should be filtered from skills pool
            assert "skill-e2e-wiring" not in filtered["skills"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
