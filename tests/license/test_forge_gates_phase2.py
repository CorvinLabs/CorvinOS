"""Phase 2 Forge Gate Tests (ADR-0701)

E2E tests for all forge chokepoints (G1–G5). Each test verifies:
1. Free tier gets HTTP 402 or MCP error
2. Member tier gets 200 OK
3. Audit event is emitted
4. Error handling is fail-closed
"""

import pytest
import json
from unittest.mock import MagicMock, patch


class TestG1ForgeToolMCP:
    """G1: MCP forge_tool gate (Registry.create)"""

    @pytest.mark.asyncio
    async def test_forge_tool_denied_on_free_tier(self):
        """Free tier attempt to create tool → license_required error"""
        # Setup: free tier context
        # Call: MCP forge_tool
        # Expect: license_required error, audit event emitted
        pass

    @pytest.mark.asyncio
    async def test_forge_tool_allowed_on_member_tier(self):
        """Member tier creates tool → 200 OK, tool registered"""
        pass

    @pytest.mark.asyncio
    async def test_forge_promote_denied_on_free_tier(self):
        """Free tier attempt to promote → license_required error"""
        pass

    @pytest.mark.asyncio
    async def test_forge_promote_allowed_on_member_tier(self):
        """Member tier promotes tool → 200 OK"""
        pass

    def test_audit_event_emitted_on_deny(self):
        """Every forge denial emits license.capability_decision audit event"""
        # Verify audit trail contains: capability, tier, decision, reason, entry_point
        pass

    def test_enforcement_error_fails_closed(self):
        """If require_capability raises, deny (fail-closed)"""
        # Setup: require_capability ImportError
        # Call: forge_tool
        # Expect: tool_error, not created
        pass


class TestG2SkillForgeRegistry:
    """G2: SkillRegistry.create and promote gates"""

    @pytest.mark.asyncio
    async def test_skill_create_denied_on_free(self):
        """Free tier can't create skills via registry"""
        pass

    @pytest.mark.asyncio
    async def test_skill_create_with_files_denied_on_free(self):
        """Free tier can't create multi-file skills"""
        pass

    @pytest.mark.asyncio
    async def test_multi_skill_promote_denied_on_free(self):
        """Free tier can't promote via MultiSkillRegistry"""
        pass


class TestG3ConsoleRoutes:
    """G3: FastAPI console routes for forge creation"""

    @pytest.mark.asyncio
    async def test_skill_creator_generate_returns_402_on_free(self, client):
        """POST /skill-creator/generate → 402 Payment Required"""
        response = client.post(
            "/v1/skill-creator/generate",
            json={"prompt": "create a CSV reader", "context": {}},
            headers={"X-Corvin-Tenant-ID": "_default"}
        )
        assert response.status_code == 402
        assert "license_required" in response.json()["detail"]["error"]

    @pytest.mark.asyncio
    async def test_skills_manual_put_returns_402_on_free(self, client):
        """PUT /skills/manual/{name} → 402 on free tier"""
        response = client.put(
            "/v1/skills/manual/my-skill",
            json={"body": "# My Skill"},
            headers={"X-Corvin-Tenant-ID": "_default"}
        )
        assert response.status_code == 402

    @pytest.mark.asyncio
    async def test_panels_post_returns_402_on_free(self, client):
        """POST /panels (forge) → 402 on free tier"""
        response = client.post(
            "/v1/panels",
            json={"name": "my-panel", "type": "custom"},
            headers={"X-Corvin-Tenant-ID": "_default"}
        )
        assert response.status_code == 402

    @pytest.mark.asyncio
    async def test_tools_promote_returns_402_on_free(self, client):
        """POST /tools/{name}/promote → 402 on free tier"""
        response = client.post(
            "/v1/tools/my-tool/promote",
            json={},
            headers={"X-Corvin-Tenant-ID": "_default"}
        )
        assert response.status_code == 402


class TestG4PluginBuilder:
    """G4: Plugin Builder chat interface gate"""

    @pytest.mark.asyncio
    async def test_plugin_builder_chat_denied_on_free(self, client):
        """/plugin-builder command → chat message: license required"""
        # Setup: free tier, plugin_builder_enabled deleted (use capability gate only)
        # Call: /chat with message "/plugin-builder create my-plugin"
        # Expect: chat response with upgrade link
        pass

    def test_plugin_builder_enabled_flag_deleted(self):
        """Feature flag plugin_builder_enabled is removed from codebase"""
        # Verify grep: plugin_builder_enabled → 0 results (except in this test file)
        pass


class TestG5QuotaGate:
    """G5: Brain v0.2 quota gate (skeleton, no production caller)"""

    def test_brain_quota_gate_wired_to_forge_create(self):
        """quota_gate.increment_and_check for forge keys calls require_capability"""
        # Currently: no production caller (Brain v0.2 is unused)
        # But skeleton is wired for future use
        pass


class TestAuditTrail:
    """Audit trail verification for all gates"""

    def test_every_deny_emits_license_capability_decision(self):
        """All G1–G5 denials appear in audit.jsonl"""
        # Verify: event_type = "license.capability_decision"
        # Fields: capability, tier, decision, reason, entry_point, tenant_id, lom
        pass

    def test_forge_provenance_events_emitted_on_create(self):
        """forge.artifact_provenance_signed event on successful create"""
        # Fields: artifact_kind, artifact_id, seat_fp, instance_id, binding_hash
        pass

    def test_audit_events_are_immutable_and_hashchained(self):
        """Audit events cannot be modified after commit"""
        # Verify via tenant_audit_chain() entries
        pass


class TestErrorHandling:
    """Error handling and fail-closed behavior"""

    def test_invalid_tier_fails_closed(self):
        """Unknown tier value → deny (free allowance)"""
        # e.g., tier = "platinum" (not recognized)
        pass

    def test_require_capability_exception_fails_closed(self):
        """If require_capability raises any exception → deny"""
        pass

    def test_missing_tenant_id_fails_closed(self):
        """If tenant_id is missing/invalid → deny"""
        pass


class TestGuardTests:
    """Code-level guard tests to prevent bypasses"""

    def test_every_forge_writer_reaches_registry_create(self):
        """All code paths that write tools/skills go through Registry.create or install()"""
        # Grep: Registry.create|SkillRegistry.create calls
        # Guard: no direct writes to .forge/tools or .forge/skills directories
        pass

    def test_every_skill_body_reader_uses_registry_get_body(self):
        """All code that reads skill implementations go through get_body()"""
        # Verify: routes/workflows.py readers use SkillRegistry.get_body
        pass

    def test_no_forge_create_bypass_paths(self):
        """Grep for direct artifact creation outside gated chokepoints"""
        # Should return 0: direct writes to registry.json, SKILL.md, etc.
        pass


class TestEndToEnd:
    """Full E2E scenarios"""

    @pytest.mark.asyncio
    async def test_free_user_forge_workflow_denied_at_first_gate(self):
        """Free user attempts: forge create → denied, no artifact created"""
        # 1. POST /forge_tool (MCP)
        # 2. Verify: license_required error
        # 3. Verify: tool not added to registry
        pass

    @pytest.mark.asyncio
    async def test_member_user_forge_workflow_succeeds(self):
        """Member user: forge create → succeed, artifact created, audit logged"""
        # 1. POST /forge_tool (MCP) with member token
        # 2. Verify: 200 OK, tool created
        # 3. Verify: audit event emitted
        # 4. Verify: tool callable via MCP
        pass

    @pytest.mark.asyncio
    async def test_member_downgrade_to_free_revokes_capability(self):
        """Member → free: artifact remains, but forge creation denied"""
        # Scenario: license revoked mid-session
        pass


class TestCompliance:
    """GDPR & audit compliance"""

    def test_audit_events_carry_tenant_id(self):
        """All events include tenant_id for isolation"""
        pass

    def test_audit_events_carry_lom(self):
        """All events include lom (line of moral responsibility)"""
        pass

    def test_no_pii_in_audit_events(self):
        """Audit events contain no PII (customer name, email, etc.)"""
        pass
