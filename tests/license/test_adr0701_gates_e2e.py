"""End-to-end tests for ADR-0701 Gates G1, G4, G5.

G1 and G5 run the REAL ``capability_api.require_capability`` (free tier = a
scratch CORVIN_HOME with no licence; member tier = the tier resolver patched,
see ``_license_console_sandbox.member_tier``). Until 2026-09-27 these patched
the gate itself (``registry.require_capability``), which proved only that a
mock is called — while the real G1 was bound to a no-op fallback and admitted
the free tier.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import member_tier  # noqa: E402

IMPL = "def main(**kwargs):\n    return {'ok': True}\n"


def _chain_decisions(home: Path) -> list[dict]:
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [r for r in map(json.loads, chain.read_text().splitlines())
            if r.get("event_type") == "license.capability_decision"]


class TestG1E2E:
    """E2E: forge.create gate on Registry.create/promote"""

    def test_g1_create_with_free_tier_denied(self, tmp_path):
        from corvin_operator.forge.forge.registry import Registry

        registry = Registry(tmp_path / "reg")
        with pytest.raises(PermissionError, match="forge.create denied"):
            registry.create(name="test_tool", description="test",
                            input_schema={"type": "object"}, impl=IMPL)
        assert registry.get("test_tool") is None
        assert not (tmp_path / "reg" / "tools").exists() or not any((tmp_path / "reg" / "tools").iterdir())

    def test_g1_create_with_member_tier_allowed(self, tmp_path):
        from corvin_operator.forge.forge.registry import Registry

        registry = Registry(tmp_path / "reg")
        with member_tier():
            result = registry.create(name="test_tool", description="test tool",
                                     input_schema={"type": "object"}, impl=IMPL)
        assert result.name == "test_tool"

    def test_g1_promote_enforces_gate(self, tmp_path):
        """Created as member, promote after a downgrade to free is refused."""
        from corvin_operator.forge.forge.registry import Registry

        registry = Registry(tmp_path / "reg")
        with member_tier():
            registry.create(name="test_tool", description="test",
                            input_schema={"type": "object"}, impl=IMPL)
            assert registry.promote("test_tool").is_dir()
        with pytest.raises(PermissionError, match="forge.create denied"):
            registry.promote("test_tool")

    def test_g1_ignores_an_import_order_no_op(self, tmp_path):
        """The gate is resolved at call time, so the capability_api/forge import
        cycle can no longer bind a no-op (fresh interpreter, both orders)."""
        import subprocess

        repo = Path(__file__).resolve().parents[2]
        probe = (
            "import sys, pathlib\n"
            "{first}\n{second}\n"
            "from corvin_operator.forge.forge.registry import Registry\n"
            "try:\n"
            "    Registry(pathlib.Path(sys.argv[1])).create('t', 'd', {{'type': 'object'}}, 'x=1')\n"
            "    print('ALLOWED')\n"
            "except PermissionError:\n"
            "    print('DENIED')\n"
        )
        a = "import corvin_operator.license.capability_api"
        b = "import corvin_operator.forge.forge.registry"
        for first, second in ((a, b), (b, a)):
            out = subprocess.run(
                [sys.executable, "-c", probe.format(first=first, second=second),
                 str(tmp_path / f"r{len(first)}")],
                cwd=repo, capture_output=True, text=True, timeout=120,
            )
            assert out.stdout.strip().splitlines()[-1] == "DENIED", out.stderr[-2000:]


class TestG4E2E:
    """E2E: plugin_builder gate on /plugin-builder command"""

    def test_g4_command_denies_free_tier(self):
        """E2E: /plugin-builder command denies free tier."""
        from core.plugins.plugin_builder.turn import command, LicenseDenied
        from corvin_operator.license.capability_api import Tier

        with patch('core.plugins.plugin_builder.turn.require_capability') as mock_req:
            # LicenseDenied(capability, tier, reason) — the single-arg form
            # raised TypeError inside the test itself.
            mock_req.side_effect = LicenseDenied(
                "forge.create", Tier.FREE, "forge.create not available"
            )

            result = command(
                "",
                tenant_id="_default",
                session_key="test_session"
            )

            # Should return error message, not raise
            assert isinstance(result, str)
            assert len(result) > 0

    def test_g4_command_allows_member_tier(self):
        """E2E: /plugin-builder command allows member tier."""
        from core.plugins.plugin_builder.turn import command

        with patch('core.plugins.plugin_builder.turn.require_capability') as mock_req:
            mock_req.return_value = None  # Allowed

            # Mock session_store to return no existing session
            with patch('core.plugins.plugin_builder.turn.session_store.get') as mock_get:
                mock_get.return_value = None

                # Call with "status" which doesn't need to start interview
                result = command(
                    "status",
                    tenant_id="_default",
                    session_key="test_session"
                )

                # Should return status message
                assert "no interview" in result.lower() or "active" in result.lower()


class TestG5E2E:
    """E2E: orchestration quota gate on skill_forge/tool_forge"""

    def test_g5_skill_forge_denies_free_tier(self):
        from core.orchestration.quota_gate import check_forge_capability
        from corvin_operator.license.capability_api import LicenseDenied

        with pytest.raises(LicenseDenied):
            check_forge_capability("_default", entry_point="skill_forge_mcp")

    def test_g5_tool_forge_allows_member_tier(self):
        from core.orchestration.quota_gate import check_forge_capability

        with member_tier():
            assert check_forge_capability("_default", entry_point="tool_forge_mcp") is None

    def test_g5_licensing_import_failure_denies(self):
        from core.orchestration.quota_gate import check_forge_capability

        with patch.dict(sys.modules, {"corvin_operator.license.capability_api": None}):
            with pytest.raises(PermissionError, match="unavailable"):
                check_forge_capability("_default", entry_point="tool_forge_mcp")


class TestGateAuditTrail:
    """Every decision — allow and deny — lands on THE tenant chain."""

    def test_g1_decisions_are_on_the_tenant_chain(self, tmp_path):
        import os
        from corvin_operator.forge.forge.registry import Registry

        home = Path(os.environ["CORVIN_HOME"])
        before = len(_chain_decisions(home))
        registry = Registry(tmp_path / "reg")
        with pytest.raises(PermissionError):
            registry.create("denied_tool", "d", {"type": "object"}, IMPL)
        with member_tier():
            registry.create("allowed_tool", "d", {"type": "object"}, IMPL)
        new = _chain_decisions(home)[before:]
        verdicts = [(r["details"]["tier"], r["details"]["decision"]) for r in new]
        assert verdicts == [("free", "deny"), ("member", "allow")]
        for r in new:
            assert r["details"]["capability"] == "forge.create"
            assert r["details"]["entry_point"] == "forge:registry.create"
            assert r["details"]["tenant_id"] == "_default"
            assert r["hash"] and "prev_hash" in r
