"""Tier Enforcement — Gate MCP workflows and plugin capabilities (ADR-0701-0703)"""
from enum import Enum


class CapabilityType(Enum):
    MCP_TOOL_EXECUTION = "mcp_tool_execution"
    SKILL_EXECUTION = "skill_execution"
    CONNECTOR_AUTH = "connector_auth"
    LAYER_OVERRIDE = "layer_override"


class TierEnforcer:
    CAPABILITY_GATES = {
        CapabilityType.MCP_TOOL_EXECUTION: {
            'tier_a_allowed': True, 'tier_b_allowed': True, 'tier_c_allowed': True,
            'quota_per_hour': 100,
        },
        CapabilityType.SKILL_EXECUTION: {
            'tier_a_allowed': True, 'tier_b_allowed': True, 'tier_c_allowed': True,
            'quota_per_hour': 50,
        },
        CapabilityType.CONNECTOR_AUTH: {
            'tier_a_allowed': False, 'tier_b_allowed': True, 'tier_c_allowed': True,
            'quota_per_hour': 10,
        },
        CapabilityType.LAYER_OVERRIDE: {
            'tier_a_allowed': False, 'tier_b_allowed': False, 'tier_c_allowed': True,
            'quota_per_hour': 5,
        },
    }
    
    def __init__(self, tenant_id: str, license_tier: str):
        self.tenant_id = tenant_id
        self.license_tier = license_tier
        self.usage_per_hour = {}
    
    def is_capability_allowed(self, capability: CapabilityType) -> bool:
        gate = self.CAPABILITY_GATES.get(capability)
        if not gate:
            return False
        key = f'tier_{self.license_tier.lower()}_allowed'
        return gate.get(key, False)
    
    def check_quota(self, capability: CapabilityType, delta: int = 1) -> bool:
        gate = self.CAPABILITY_GATES.get(capability)
        if not gate or gate.get('quota_per_hour') is None:
            return True
        current = self.usage_per_hour.get(capability.value, 0)
        return (current + delta) <= gate['quota_per_hour']
    
    def enforce_capability(self, capability: CapabilityType, delta: int = 1) -> None:
        if not self.is_capability_allowed(capability):
            raise CapabilityDeniedError(f'{capability.value} not allowed')
        if not self.check_quota(capability, delta):
            raise CapabilityDeniedError(f'{capability.value} quota exceeded')
        self.usage_per_hour[capability.value] = self.usage_per_hour.get(capability.value, 0) + delta


class CapabilityDeniedError(Exception):
    pass
