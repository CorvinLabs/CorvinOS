"""L2: Composition Validator + L3: Version Compatibility Gate."""

from core.skills.contract import SkillContract, SkillRegistry, MajorVersionMarker, SKILL_REGISTRY


class CompositionValidator:
    """L2: Pre-flight DAG validation."""

    def __init__(self, registry: SkillRegistry = SKILL_REGISTRY):
        self.registry = registry

    async def validate_dag(self, dag_skills: list[str], context=None) -> tuple[bool, str]:
        """Validate skill DAG before execution."""

        # 1. Validate chain continuity (A.output == B.input)
        for i in range(len(dag_skills) - 1):
            skill_a_id = dag_skills[i]
            skill_b_id = dag_skills[i + 1]

            contract_a = self.registry.get_latest_contract(skill_a_id)
            contract_b = self.registry.get_latest_contract(skill_b_id)

            if not contract_a or not contract_b:
                return False, f"Skill not found: {skill_a_id} or {skill_b_id}"

            # Check schema match (strict pattern matching)
            if contract_a.output_schema != contract_b.input_schema:
                return False, f"Schema mismatch: {skill_a_id}.output != {skill_b_id}.input"

        # 2. Validate all side effects are reversible (CRITICAL - Remediation C6)
        for skill_id in dag_skills:
            contract = self.registry.get_latest_contract(skill_id)

            for side_effect in contract.side_effects:
                if side_effect not in contract.reversible_side_effects:
                    return False, f"Non-reversible side effect '{side_effect}' in {skill_id}"

                # Verify reversal skill exists (Remediation C2)
                reversal_skill_id = contract.reversible_side_effects[side_effect]
                reversal_contract = self.registry.get_latest_contract(reversal_skill_id)
                if not reversal_contract:
                    return False, f"Reversal skill '{reversal_skill_id}' not found for '{side_effect}'"

        return True, "DAG valid"


class VersionCompatibilityGate:
    """L3: Version compatibility checking + auto-conversion."""

    def __init__(self, registry: SkillRegistry = SKILL_REGISTRY):
        self.registry = registry

    async def check_upgrade(self, skill_id: str, old_version: str, new_version: str) -> tuple[str, str]:
        """Check if skill can be auto-upgraded or needs human decision."""

        marker = self.registry.get_version_marker(skill_id, old_version, new_version)

        if not marker:
            # No breaking change marker = non-breaking upgrade (safe)
            return "auto_upgrade", "Non-breaking upgrade"

        if not marker.compatibility_layer:
            # Breaking change, no auto-conversion available
            return "require_human_decision", f"Breaking change: {', '.join(marker.breaking_changes)}"

        # Auto-conversion available (Remediation C2: validate conversion skill)
        if marker.conversion_skill_id:
            conversion_contract = self.registry.get_latest_contract(marker.conversion_skill_id)
            if not conversion_contract:
                return "require_human_decision", f"Conversion skill '{marker.conversion_skill_id}' not found"

            # Assume conversion skill has been tested; trust it
            return "auto_upgrade_with_conversion", f"Auto-converting via {marker.conversion_skill_id}"

        return "require_human_decision", "Compatibility layer declared but no conversion skill"
