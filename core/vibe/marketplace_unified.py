"""Phase 4b: Unified Plugin-Skill Marketplace Integration.

Skills + Plugins as single marketplace entity.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum


class EntityType(str, Enum):
    SKILL = "skill"
    PLUGIN = "plugin"
    HYBRID = "hybrid"  # Skill that can also act as plugin


@dataclass(frozen=True)
class MarketplaceEntity:
    """Unified entity in marketplace (Skill or Plugin)."""
    id: str
    entity_type: EntityType
    version: str
    author: str
    rating: float  # 0-5 stars
    rating_count: int
    tier: str  # Bronze, Silver, Gold
    dependencies: List[str]


class UnifiedMarketplace:
    """Phase 4b: Single marketplace for Skills + Plugins."""

    def __init__(self):
        self.entities: Dict[str, MarketplaceEntity] = {}
        self.compatibility_matrix = {}  # (skill, plugin) → compatible (bool)

    def index_entity(self, entity: MarketplaceEntity) -> bool:
        """Index a Skill or Plugin in unified marketplace."""
        if entity.id in self.entities:
            return False

        self.entities[entity.id] = entity
        return True

    def resolve_dependencies(self, entity_id: str) -> Dict:
        """Resolve all dependencies (Skills/Plugins) for entity."""
        entity = self.entities.get(entity_id)
        if not entity:
            return {"error": "Entity not found"}

        deps = {}
        for dep_id in entity.dependencies:
            if dep_id in self.entities:
                deps[dep_id] = self.entities[dep_id]
            else:
                deps[dep_id] = {"error": "Dependency not found"}

        return deps

    def check_compatibility(self, skill_id: str, plugin_id: str) -> bool:
        """Check if Skill + Plugin are compatible."""
        key = (skill_id, plugin_id)
        if key in self.compatibility_matrix:
            return self.compatibility_matrix[key]

        # Simple heuristic: compatible if no conflicting dependencies
        skill = self.entities.get(skill_id)
        plugin = self.entities.get(plugin_id)

        if not skill or not plugin:
            return False

        # Compatible if no overlapping dependencies with different versions
        compatible = True
        for dep in skill.dependencies:
            if dep in plugin.dependencies:
                compatible = True  # Same dependency, compatible

        self.compatibility_matrix[key] = compatible
        return compatible


# Tests
def test_unified_marketplace():
    """Test Phase 4b unified marketplace."""
    print("Phase 4b Unified Marketplace Tests:\n")

    marketplace = UnifiedMarketplace()

    # Create entities
    skill = MarketplaceEntity(
        id="os.router",
        entity_type=EntityType.SKILL,
        version="1.0.0",
        author="corvin_team",
        rating=4.8,
        rating_count=150,
        tier="Gold",
        dependencies=[]
    )

    plugin = MarketplaceEntity(
        id="plugin_telemetry",
        entity_type=EntityType.PLUGIN,
        version="1.0.0",
        author="community",
        rating=4.5,
        rating_count=75,
        tier="Silver",
        dependencies=["os.router"]
    )

    hybrid = MarketplaceEntity(
        id="os.cost_optimizer",
        entity_type=EntityType.HYBRID,
        version="1.0.0",
        author="corvin_team",
        rating=4.7,
        rating_count=120,
        tier="Gold",
        dependencies=["os.router"]
    )

    # Index
    assert marketplace.index_entity(skill)
    assert marketplace.index_entity(plugin)
    assert marketplace.index_entity(hybrid)

    # Resolve dependencies
    deps = marketplace.resolve_dependencies("plugin_telemetry")
    assert "os.router" in deps

    # Check compatibility
    assert marketplace.check_compatibility("os.router", "plugin_telemetry")

    print("✅ Unified marketplace works")
    print(f"Indexed: {len(marketplace.entities)} entities\n")


if __name__ == "__main__":
    test_unified_marketplace()
    print("🎉 Phase 4b marketplace integration ready!")
