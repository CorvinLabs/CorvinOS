"""
Plugin Test Checklist — Define Mandatory Test Requirements Per Plugin

Every plugin must pass a minimum test checklist based on boot_layer and origin.
This module defines and validates those requirements.
"""

from dataclasses import dataclass
from typing import Dict, List, Set, Optional
from enum import Enum


class BootLayer(str, Enum):
    """Plugin boot layer classification"""
    COMPLIANCE = "compliance"
    CORE = "core"
    BUNDLED = "bundled"
    INSTALLED = "installed"


class Origin(str, Enum):
    """Plugin origin classification"""
    BUILDIN = "buildin"
    VETTED = "vetted"
    COMMUNITY = "community"


class TestCategory(str, Enum):
    """All possible test categories for plugins"""
    # Tier 1: Unit
    UNIT_MANIFEST = "test_manifest_parsing"
    UNIT_VALIDATION = "test_validation_schemas"
    UNIT_CONTEXT = "test_context_construction"
    UNIT_ERROR_CLASSES = "test_error_classes"

    # Tier 2: Integration
    INTEGRATION_REGISTRY = "test_registry_integration"
    INTEGRATION_PROCESS = "test_process_manager_integration"
    INTEGRATION_HEALTH = "test_health_monitoring"
    INTEGRATION_CLI = "test_cli_integration"
    INTEGRATION_MANIFEST = "test_manifest_validation_integration"
    INTEGRATION_DEPS = "test_dependency_resolution"

    # Tier 3: Feature-E2E
    E2E_INIT = "test_init_lifecycle"
    E2E_FEATURES = "test_features"
    E2E_HOOKS = "test_hooks"
    E2E_INTEGRATION = "test_integration"
    E2E_CLEANUP = "test_cleanup"

    # Tier 4: System-Health
    HEALTH_ISOLATION = "test_cross_tenant_isolation"
    HEALTH_DRIFT = "test_config_drift_detection"
    HEALTH_LOAD_ORDER = "test_load_order_verification"
    HEALTH_HOT_RELOAD = "test_hot_reload_consistency"
    HEALTH_CONFLICTS = "test_marketplace_conflict_matrix"

    # Risk-based extras
    EXTRA_LOAD_ORDER = "test_load_order"
    EXTRA_HOT_RELOAD = "test_hot_reload"
    EXTRA_FAULT = "test_fault_injection"
    EXTRA_SANDBOX = "test_sandbox"
    EXTRA_RESOURCES = "test_resource_limits"


@dataclass
class PluginTestRequirements:
    """Test requirements for a plugin based on boot_layer and origin"""
    plugin_id: str
    boot_layer: BootLayer
    origin: Origin

    def get_mandatory_tests(self) -> Set[TestCategory]:
        """Get mandatory test categories for this plugin"""
        # All plugins need these
        mandatory = {
            # Tier 1: Unit basics
            TestCategory.UNIT_MANIFEST,
            TestCategory.UNIT_VALIDATION,
            TestCategory.UNIT_CONTEXT,

            # Tier 2: Integration basics
            TestCategory.INTEGRATION_REGISTRY,
            TestCategory.INTEGRATION_MANIFEST,
            TestCategory.INTEGRATION_DEPS,

            # Tier 3: Feature-E2E basics
            TestCategory.E2E_INIT,
            TestCategory.E2E_FEATURES,
            TestCategory.E2E_HOOKS,
            TestCategory.E2E_INTEGRATION,
            TestCategory.E2E_CLEANUP,
        }

        return mandatory

    def get_risk_based_tests(self) -> Set[TestCategory]:
        """Get risk-based (high-risk plugin) test categories"""
        risk_based = set()

        # High-risk plugins: compliance, core
        if self.boot_layer in [BootLayer.COMPLIANCE, BootLayer.CORE]:
            risk_based.update({
                TestCategory.EXTRA_LOAD_ORDER,
                TestCategory.EXTRA_HOT_RELOAD,
                TestCategory.EXTRA_FAULT,
                TestCategory.HEALTH_LOAD_ORDER,
                TestCategory.HEALTH_HOT_RELOAD,
                TestCategory.HEALTH_CONFLICTS,
            })

        # Third-party: community origin
        if self.origin == Origin.COMMUNITY:
            risk_based.update({
                TestCategory.EXTRA_SANDBOX,
                TestCategory.EXTRA_RESOURCES,
                TestCategory.HEALTH_ISOLATION,
            })

        return risk_based

    def get_all_tests(self) -> Set[TestCategory]:
        """Get all required test categories (mandatory + risk-based)"""
        return self.get_mandatory_tests() | self.get_risk_based_tests()

    def get_system_health_tests(self) -> Set[TestCategory]:
        """Get Tier-4 system-health tests (run only on main merge)"""
        return {
            TestCategory.HEALTH_ISOLATION,
            TestCategory.HEALTH_DRIFT,
            TestCategory.HEALTH_LOAD_ORDER,
            TestCategory.HEALTH_HOT_RELOAD,
            TestCategory.HEALTH_CONFLICTS,
        }

    def get_quick_tests(self) -> Set[TestCategory]:
        """Get quick tests (TIER-1 + TIER-2, ~5-15 min)"""
        return {
            TestCategory.UNIT_MANIFEST,
            TestCategory.UNIT_VALIDATION,
            TestCategory.UNIT_CONTEXT,
            TestCategory.INTEGRATION_REGISTRY,
            TestCategory.INTEGRATION_MANIFEST,
            TestCategory.INTEGRATION_DEPS,
        }


class TestChecklistValidator:
    """Validate that a plugin has all required tests"""

    @staticmethod
    def validate(
        plugin_id: str,
        boot_layer: str,
        origin: str,
        existing_tests: Set[str]
    ) -> Dict[str, any]:
        """
        Validate a plugin's test coverage.

        Args:
            plugin_id: Plugin ID
            boot_layer: BootLayer value
            origin: Origin value
            existing_tests: Set of test categories that exist

        Returns:
            Dict with:
            - "valid": bool (all required tests exist)
            - "mandatory_missing": List (required tests that are missing)
            - "coverage": float (% of required tests present)
        """
        try:
            bl = BootLayer(boot_layer)
            org = Origin(origin)
        except ValueError as e:
            return {
                "valid": False,
                "error": str(e),
                "plugin_id": plugin_id,
            }

        requirements = PluginTestRequirements(plugin_id, bl, org)
        mandatory = requirements.get_mandatory_tests()
        all_required = requirements.get_all_tests()

        # Convert to strings for comparison
        mandatory_str = {cat.value for cat in mandatory}
        all_required_str = {cat.value for cat in all_required}

        # Find missing tests
        mandatory_missing = sorted(list(mandatory_str - existing_tests))
        optional_missing = sorted(list(all_required_str - existing_tests - set(mandatory_missing)))

        # Calculate coverage
        coverage = (len(existing_tests & all_required_str) / len(all_required_str) * 100) if all_required_str else 100

        return {
            "valid": len(mandatory_missing) == 0,
            "plugin_id": plugin_id,
            "boot_layer": boot_layer,
            "origin": origin,
            "mandatory_missing": mandatory_missing,
            "optional_missing": optional_missing,
            "coverage": coverage,
            "total_required": len(all_required_str),
            "total_existing": len(existing_tests),
        }


# Predefined checklist requirements per plugin type
PLUGIN_CHECKLISTS: Dict[str, Dict[str, List[str]]] = {
    # Compliance plugins (highest bar)
    "compliance": {
        "mandatory": [
            "test_manifest_parsing",
            "test_validation_schemas",
            "test_context_construction",
            "test_registry_integration",
            "test_dependency_resolution",
            "test_init_lifecycle",
            "test_features",
            "test_hooks",
            "test_integration",
            "test_cleanup",
            "test_load_order",
            "test_fault_injection",
            "test_cross_tenant_isolation",
            "test_config_drift_detection",
        ],
        "recommended": [
            "test_hot_reload_consistency",
            "test_health_monitoring",
        ],
    },

    # Core plugins
    "core": {
        "mandatory": [
            "test_manifest_parsing",
            "test_validation_schemas",
            "test_registry_integration",
            "test_init_lifecycle",
            "test_features",
            "test_hooks",
            "test_cleanup",
            "test_load_order",
        ],
        "recommended": [
            "test_fault_injection",
            "test_health_monitoring",
        ],
    },

    # Bundled plugins
    "bundled": {
        "mandatory": [
            "test_manifest_parsing",
            "test_validation_schemas",
            "test_init_lifecycle",
            "test_features",
            "test_hooks",
            "test_cleanup",
        ],
        "recommended": [
            "test_dependency_resolution",
            "test_health_monitoring",
        ],
    },

    # Installed plugins (default)
    "installed": {
        "mandatory": [
            "test_manifest_parsing",
            "test_init_lifecycle",
            "test_features",
            "test_cleanup",
        ],
        "recommended": [
            "test_hooks",
            "test_dependency_resolution",
        ],
    },

    # Community (third-party)
    "community": {
        "mandatory": [
            "test_manifest_parsing",
            "test_validation_schemas",
            "test_init_lifecycle",
            "test_features",
            "test_cleanup",
            "test_sandbox",
            "test_resource_limits",
            "test_cross_tenant_isolation",
        ],
        "recommended": [
            "test_hooks",
            "test_health_monitoring",
        ],
    },
}


def get_checklist(boot_layer: str, origin: str = "buildin") -> Dict[str, List[str]]:
    """
    Get test checklist for a plugin based on boot_layer and origin.

    Args:
        boot_layer: BootLayer value (compliance, core, bundled, installed)
        origin: Origin value (buildin, vetted, community)

    Returns:
        Dict with "mandatory" and "recommended" test categories
    """
    # Use boot_layer as primary key
    checklist = PLUGIN_CHECKLISTS.get(boot_layer, PLUGIN_CHECKLISTS["installed"])

    # For community plugins, add extra requirements
    if origin == "community":
        community_checklist = PLUGIN_CHECKLISTS["community"]
        checklist = {
            "mandatory": list(set(checklist["mandatory"]) | set(community_checklist["mandatory"])),
            "recommended": list(set(checklist.get("recommended", [])) | set(community_checklist.get("recommended", []))),
        }

    return checklist
