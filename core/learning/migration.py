"""Migration: import Concepts/Metaphers/Skills into TreeOfThoughts (Phase 6)."""
from __future__ import annotations
from pathlib import Path
from .storage import LearningEventStore
from .models import TreeNode, CompositionType
import json


class MigrationPlanner:
    """Plan migration from fragmented systems to unified TreeOfThoughts."""
    
    def __init__(self, store: LearningEventStore):
        self.store = store
        self.migration_log = []
    
    def migrate_concept_to_framework(self, concept_path: Path, framework_id: str) -> TreeNode:
        """Convert a Concept doc → Framework node."""
        content = concept_path.read_text()
        
        # Parse frontmatter
        framework = TreeNode(
            id=framework_id,
            level="framework",
            name=self._extract_title(content),
            when=self._extract_when(content),
            anti_when=self._extract_anti_when(content),
            adr_link=concept_path.name,
        )
        
        self.store.register_node(framework)
        self.migration_log.append(f"✅ Migrated {concept_path} → {framework_id}")
        
        return framework
    
    def migrate_metapher_to_pattern(self, metapher_dict: dict, pattern_id: str) -> TreeNode:
        """Convert a Metapher entry → Pattern node."""
        pattern = TreeNode(
            id=pattern_id,
            level="pattern",
            name=metapher_dict.get("name", pattern_id),
            when=metapher_dict.get("when", []),
            anti_when=metapher_dict.get("anti_when", []),
        )
        
        self.store.register_node(pattern)
        self.migration_log.append(f"✅ Migrated metapher {pattern_id}")
        
        return pattern
    
    def migrate_skill_to_method(self, skill_name: str, skill_body: str, method_id: str) -> TreeNode:
        """Convert a SkillForge entry → Method node."""
        method = TreeNode(
            id=method_id,
            level="method",
            name=skill_name,
            body=skill_body[:500],  # Truncate to summary
            children=[],
            composition_type=CompositionType.OR,
        )
        
        self.store.register_node(method)
        self.migration_log.append(f"✅ Migrated skill {skill_name} → {method_id}")
        
        return method
    
    def validate_migration(self) -> dict[str, list[str]]:
        """Check for gaps in migration."""
        issues = []
        
        all_nodes = self.store.all_nodes()
        
        # Check: every method has ≥1 child pattern
        for node in all_nodes:
            if node.level == "method" and not node.children:
                issues.append(f"Method {node.id} has no child patterns")
        
        # Check: every pattern has E2E test or production usage
        for node in all_nodes:
            if node.level == "pattern":
                if node.calls_in_production == 0:
                    issues.append(f"Pattern {node.id} never used in production")
        
        return {
            "issues": issues,
            "migration_count": len(self.migration_log),
            "valid": len(issues) == 0,
        }
    
    def report(self) -> str:
        """Generate migration report."""
        validation = self.validate_migration()
        
        report = "## TreeOfThoughts Migration Report\n\n"
        report += f"**Status:** {'✅ Valid' if validation['valid'] else '⚠️ Issues found'}\n"
        report += f"**Migrated items:** {validation['migration_count']}\n"
        report += f"**Issues:** {len(validation['issues'])}\n\n"
        
        if validation["issues"]:
            report += "### Issues\n"
            for issue in validation["issues"]:
                report += f"- {issue}\n"
        
        report += "\n### Migration Log\n"
        for line in self.migration_log:
            report += f"{line}\n"
        
        return report
    
    def _extract_title(self, content: str) -> str:
        """Extract title from markdown content."""
        for line in content.split('\n'):
            if line.startswith('# '):
                return line.replace('# ', '').strip()
        return "Untitled"
    
    def _extract_when(self, content: str) -> list[str]:
        """Extract use cases from content."""
        # Simple heuristic: look for "## When" section
        if "## When" in content or "## Context" in content:
            return ["documented in concept"]
        return []
    
    def _extract_anti_when(self, content: str) -> list[str]:
        """Extract anti-patterns from content."""
        if "## When NOT to use" in content or "## Antipattern" in content:
            return ["documented in concept"]
        return []
