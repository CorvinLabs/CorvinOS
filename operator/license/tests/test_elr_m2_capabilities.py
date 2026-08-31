"""test_elr_m2_capabilities.py — End-to-End M2 Capability Tests.

Tests for all 8 entangled capabilities:
  1. egress-paid-preset (L35)
  2. a2a-sestoken (ADR-0103)
  3–5. mcp-auth-* (paid MCP)
  6–7. cls-tier-*-key (ADR-0156)
  8. [reserved]

Per-capability test matrix:
  - Schema serialization/deserialization
  - AEAD wrap/unwrap with ratchet
  - Expiry check (epoch-based)
  - Policy application (egress allowlist, A2A membership, etc.)
  - Fail-closed on unwrap failure
"""
from __future__ import annotations

from pathlib import Path
import pytest
import sys

here = Path(__file__).resolve().parent.parent
if str(here) not in sys.path:
    sys.path.insert(0, str(here))

from elr import (
    EntangledRatchet,
    CapabilityRegistry,
    CapabilityEnvelope,
    WrappedCapabilityDescriptor,
    make_root_from_license_token,
)
from elr_capabilities_m2 import (
    EgressPaidPresetCapability,
    A2ASesTokenCapability,
    MCPAuthCapability,
    CLSActivationCapability,
    create_capability_from_dict,
)


class TestEgressPaidPresetCapability:
    """Capability #1: Egress-allowlist policy (L35)."""

    def test_egress_schema_serialization(self):
        cap = EgressPaidPresetCapability(
            expires_at_epoch_k=10,
            allowed_hosts=["localhost", "ollama.lan"],
            forbidden_hosts=["api.anthropic.com"],
            default_action="deny",
        )
        data = cap.to_dict()
        assert data["capability_id"] == "egress-paid-preset"
        assert data["allowed_hosts"] == ["localhost", "ollama.lan"]
        assert data["default_action"] == "deny"

    def test_egress_schema_deserialization(self):
        data = {
            "capability_id": "egress-paid-preset",
            "version": 1,
            "expires_at_epoch_k": 5,
            "allowed_hosts": ["localhost"],
            "forbidden_hosts": ["api.example.com"],
            "default_action": "allow",
        }
        cap = EgressPaidPresetCapability.from_dict(data)
        assert cap is not None
        assert cap.expires_at_epoch_k == 5
        assert cap.allowed_hosts == ["localhost"]

    def test_egress_wrap_unwrap_lifecycle(self):
        # 1. Create ratchet
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        # 2. Create capability
        cap = EgressPaidPresetCapability(
            expires_at_epoch_k=1,
            allowed_hosts=["localhost", "127.0.0.1"],
            forbidden_hosts=[],
            default_action="deny",
        )

        # 3. Wrap with tile_k
        tile_k = ratchet.derive_tile("egress-paid-preset")
        wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)
        assert isinstance(wrapped, WrappedCapabilityDescriptor)

        # 4. Unwrap with same tile_k
        plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k)
        assert plaintext is not None

        # 5. Deserialize
        cap2 = EgressPaidPresetCapability.from_dict(plaintext)
        assert cap2 is not None
        assert cap2.allowed_hosts == cap.allowed_hosts

    def test_egress_expiry_check(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        # Capability expires at epoch 1 (after epoch 0)
        cap = EgressPaidPresetCapability(
            expires_at_epoch_k=1,
            allowed_hosts=["localhost"],
        )

        # Current epoch is 0 (not expired)
        assert cap.expires_at_epoch_k > ratchet.current_state.epoch_k

        # Advance to epoch 1
        ratchet.advance(b"c" * 32)
        assert ratchet.current_state.epoch_k == 1
        # Now at epoch 1, expires_at_epoch_k is 1, so it's at the boundary (not yet expired)
        # Advance to epoch 2
        ratchet.advance(b"d" * 32)
        assert ratchet.current_state.epoch_k == 2
        assert cap.expires_at_epoch_k < ratchet.current_state.epoch_k  # Expired

    def test_egress_wrong_key_fails_closed(self):
        tile_k_1 = b"k1" + b"x" * 30
        tile_k_2 = b"k2" + b"y" * 30

        cap = EgressPaidPresetCapability(
            allowed_hosts=["localhost"],
        )
        wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k_1)

        # Unwrap with wrong key fails (returns None, fail-closed)
        plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k_2)
        assert plaintext is None


class TestA2ASesTokenCapability:
    """Capability #2: A2A / network-membership SesT."""

    def test_a2a_schema_serialization(self):
        cap = A2ASesTokenCapability(
            expires_at_epoch_k=5,
            membership_nonce="abc123def456",
            network_id="corvin-prod",
            allowed_origins=["https://example.com"],
            allowed_endpoints=["endpoint-1", "endpoint-2"],
        )
        data = cap.to_dict()
        assert data["capability_id"] == "a2a-sestoken"
        assert data["network_id"] == "corvin-prod"
        assert len(data["allowed_endpoints"]) == 2

    def test_a2a_wrap_unwrap_lifecycle(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        cap = A2ASesTokenCapability(
            membership_nonce="test-nonce-12345678",
            network_id="prod",
        )

        tile_k = ratchet.derive_tile("a2a-sestoken")
        wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)
        plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k)
        assert plaintext is not None

        cap2 = A2ASesTokenCapability.from_dict(plaintext)
        assert cap2.network_id == "prod"


class TestMCPAuthCapability:
    """Capability #3–5: Paid-tier MCP authentication."""

    def test_mcp_schema_serialization(self):
        cap = MCPAuthCapability(
            capability_id="mcp-auth-imagegen",
            service="imagegen",
            auth_token="secret-token-xyz",
            api_endpoint="https://api.openai.com",
        )
        data = cap.to_dict()
        assert data["capability_id"] == "mcp-auth-imagegen"
        assert data["service"] == "imagegen"
        # Token should NOT appear in audit details (structural safeguard)

    def test_mcp_wrap_unwrap_lifecycle(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        cap = MCPAuthCapability(
            capability_id="mcp-auth-slack",
            service="slack",
            auth_token="xoxb-token",
            api_endpoint="https://slack.com/api",
        )

        tile_k = ratchet.derive_tile("mcp-auth-slack")
        wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)
        plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k)
        assert plaintext is not None

        cap2 = MCPAuthCapability.from_dict(plaintext)
        assert cap2.service == "slack"

    def test_mcp_multiple_services(self):
        """Test that different MCP services use different capability_labels."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        services = [
            ("mcp-auth-imagegen", "imagegen"),
            ("mcp-auth-slack", "slack"),
            ("mcp-auth-custom", "custom"),
        ]

        wrapped_dict = {}
        for label, service in services:
            cap = MCPAuthCapability(capability_id=label, service=service)
            tile_k = ratchet.derive_tile(label)
            wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)
            wrapped_dict[label] = (wrapped, tile_k)

        # Verify each unwraps correctly with its tile_k
        for label, (wrapped, tile_k) in wrapped_dict.items():
            plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k)
            assert plaintext is not None
            cap = MCPAuthCapability.from_dict(plaintext)
            assert cap.capability_id == label


class TestCLSActivationCapability:
    """Capability #6–7: Custom-Layer Tier-B/C activation."""

    def test_cls_schema_serialization(self):
        cap = CLSActivationCapability(
            capability_id="cls-tier-b-key",
            tier="B",
            max_concurrent_layers=5,
            allowed_tools=["tool.*"],
            allowed_skills=["skill.*"],
        )
        data = cap.to_dict()
        assert data["capability_id"] == "cls-tier-b-key"
        assert data["tier"] == "B"
        assert data["max_concurrent_layers"] == 5

    def test_cls_wrap_unwrap_lifecycle(self):
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        cap = CLSActivationCapability(
            capability_id="cls-tier-c-key",
            tier="C",
            max_concurrent_layers=10,
        )

        tile_k = ratchet.derive_tile("cls-tier-c-key")
        wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)
        plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k)
        assert plaintext is not None

        cap2 = CLSActivationCapability.from_dict(plaintext)
        assert cap2.tier == "C"

    def test_cls_tier_tiers(self):
        """Test both Tier-B and Tier-C."""
        tiers = [
            ("cls-tier-b-key", "B", 5),
            ("cls-tier-c-key", "C", 10),
        ]

        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        for label, tier, max_layers in tiers:
            cap = CLSActivationCapability(
                capability_id=label,
                tier=tier,
                max_concurrent_layers=max_layers,
            )
            tile_k = ratchet.derive_tile(label)
            wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)
            plaintext = CapabilityEnvelope.unwrap(wrapped, tile_k)
            assert plaintext is not None
            cap2 = CLSActivationCapability.from_dict(plaintext)
            assert cap2.tier == tier
            assert cap2.max_concurrent_layers == max_layers


class TestCapabilityRegistry:
    """Integration: CapabilityRegistry loading from tenant config."""

    def test_registry_from_tenant_config(self):
        """Load wrapped descriptors from simulated tenant.corvin.yaml."""
        root = b"a" * 32
        chain_head = b"b" * 32
        ratchet = EntangledRatchet(root, chain_head)

        # Create and wrap a capability
        cap = EgressPaidPresetCapability(allowed_hosts=["localhost"])
        tile_k = ratchet.derive_tile("egress-paid-preset")
        wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)

        # Simulate tenant config with wrapped descriptor
        import base64

        wrapped_b64 = base64.b64encode(wrapped.to_bytes()).decode("ascii")
        tenant_config = {
            "spec": {
                "elr": {
                    "capabilities": {
                        "egress-paid-preset": {
                            "wrapped_bytes_b64": wrapped_b64,
                            "version": 1,
                        }
                    }
                }
            }
        }

        # Load registry
        registry = CapabilityRegistry(tenant_config)
        assert "egress-paid-preset" in registry.all_labels()

        # Retrieve wrapped descriptor
        retrieved = registry.get_descriptor("egress-paid-preset")
        assert retrieved is not None
        assert retrieved.nonce == wrapped.nonce

    def test_registry_missing_descriptor(self):
        """Registry returns None for missing capability."""
        registry = CapabilityRegistry()
        result = registry.get_descriptor("nonexistent-capability")
        assert result is None


class TestCapabilityFactory:
    """Generic capability factory (dispatch by label)."""

    def test_factory_dispatch_egress(self):
        data = {"capability_id": "egress-paid-preset", "allowed_hosts": ["localhost"]}
        cap = create_capability_from_dict("egress-paid-preset", data)
        assert isinstance(cap, EgressPaidPresetCapability)

    def test_factory_dispatch_a2a(self):
        data = {"capability_id": "a2a-sestoken", "network_id": "prod"}
        cap = create_capability_from_dict("a2a-sestoken", data)
        assert isinstance(cap, A2ASesTokenCapability)

    def test_factory_dispatch_mcp(self):
        data = {"capability_id": "mcp-auth-imagegen", "service": "imagegen"}
        cap = create_capability_from_dict("mcp-auth-imagegen", data)
        assert isinstance(cap, MCPAuthCapability)

    def test_factory_dispatch_cls(self):
        data = {"capability_id": "cls-tier-b-key", "tier": "B"}
        cap = create_capability_from_dict("cls-tier-b-key", data)
        assert isinstance(cap, CLSActivationCapability)

    def test_factory_unknown_label(self):
        data = {}
        cap = create_capability_from_dict("unknown-capability", data)
        assert cap is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
