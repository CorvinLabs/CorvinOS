"""Learning Loop KG MCP Indexer — ADR-0906 + ADR-0907 (minimal k=4)

Index learning loops into Knowledge Graph for operator discovery.
"""

from typing import List, Dict, Any
from core.learning.learning_loop_manifest import LearningLoop


class LearningLoopKGIndexer:
    """Index learning loops into KG MCP for operator visibility."""

    @staticmethod
    def build_kg_index(loops: List[LearningLoop]) -> List[Dict[str, Any]]:
        """Convert learning loops to KG entities for indexing."""
        entities = []

        for loop in loops:
            entity = {
                "entity_type": "learning_loop",
                "id": loop.loop_id,
                "properties": {
                    "plugin_id": loop.plugin_id,
                    "description": loop.description,
                    "event_source": loop.event_source,
                    "owner_skill": loop.owner_skill,
                    "status": loop.status.value if loop.status else "unknown",
                    "health_score": loop.health_score,
                    "health_threshold": loop.health_threshold,
                },
                "labels": [
                    "LearningLoop",
                    f"plugin:{loop.plugin_id}",
                    f"skill:{loop.owner_skill}" if loop.owner_skill else None,
                    f"status:{loop.status.value}" if loop.status else None,
                ],
                "relationships": [
                    {
                        "type": "owned_by",
                        "target": loop.plugin_id,
                        "target_type": "plugin"
                    }
                ] + (
                    [{"type": "driven_by", "target": loop.owner_skill, "target_type": "skill"}]
                    if loop.owner_skill else []
                ),
            }
            entities.append(entity)

        return entities
