"""
Bidirectional upstream backfill: Auto-generate missing upstream artifacts.

When creating Concept without upstream Idea:
  System: "Upstream Idea missing. Generate placeholder? [Y/n]"
  → Creates Idea-XXXX with default content
  → Links bidirectionally (Idea.downstream ← Concept, Concept.upstream → Idea)

Same for ADR (Concept upstream) and Plan (ADR upstream).

Phase 4 enhancement: Semantic generation via AI (infer Idea from Concept, etc.)
"""

from datetime import datetime
from typing import Optional, Tuple

from .palace import IdeaPalace
from .palace.drawer import Drawer
from .models.artifact import Idea, Concept, ADR, ImplementationPlan, Status


class UpstreamBackfill:
    """Generate missing upstream artifacts."""

    def __init__(self, palace: IdeaPalace):
        self.palace = palace

    def ensure_idea_upstream(self, concept_id: str, concept: Concept, auto_generate: bool = False) -> Optional[str]:
        """
        Ensure Concept has upstream Idea.

        Args:
            concept_id: ID of Concept being created
            concept: Concept object
            auto_generate: If True, create missing Idea (else warn)

        Returns:
            Upstream Idea ID (existing or newly created)
        """
        if concept.upstream:
            return concept.upstream

        # Upstream missing
        if not auto_generate:
            return None

        # Generate placeholder Idea
        wing_obj = self.palace.create_wing(concept.wing)
        room_obj = wing_obj.create_room(concept.room)

        idea_id = f"IDEA-{self._next_id(room_obj, 'idea'):04d}"

        idea = Idea(
            id=idea_id,
            name=f"[Generated] Root concept for {concept.name}",
            room=concept.room,
            wing=concept.wing,
            status=Status.DRAFT,
            created_at=datetime.now(),
            tags=['auto-generated'],
            inspiration_context=f"Generated as upstream for {concept_id}",
            downstream=[concept_id],  # Bidirectional link
        )

        # Save Idea
        idea_drawer = Drawer('idea', idea.id, f"# {idea.name}\n\n(Auto-generated)\n", idea.to_dict())
        room_obj.drawer_manager.save(idea_drawer)

        return idea_id

    def ensure_concept_upstream(self, adr_id: str, adr: ADR, auto_generate: bool = False) -> Optional[str]:
        """Ensure ADR has upstream Concept."""
        if adr.upstream:
            return adr.upstream

        if not auto_generate:
            return None

        # Generate placeholder Concept
        wing_obj = self.palace.create_wing(adr.wing)
        room_obj = wing_obj.create_room(adr.room)

        concept_id = f"CONCEPT-{self._next_id(room_obj, 'concept'):04d}"

        concept = Concept(
            id=concept_id,
            name=f"[Generated] Pattern for {adr.name}",
            room=adr.room,
            wing=adr.wing,
            status=Status.DRAFT,
            created_at=datetime.now(),
            tags=['auto-generated'],
            downstream=[adr_id],  # Bidirectional link
        )

        # Save Concept
        concept_drawer = Drawer('concept', concept.id, f"# {concept.name}\n\n(Auto-generated)\n", concept.to_dict())
        room_obj.drawer_manager.save(concept_drawer)

        return concept_id

    def ensure_adr_upstream(self, plan_id: str, plan: ImplementationPlan, auto_generate: bool = False) -> Optional[str]:
        """Ensure Plan has upstream ADR."""
        if plan.upstream:
            return plan.upstream

        if not auto_generate:
            return None

        # Generate placeholder ADR
        wing_obj = self.palace.create_wing(plan.wing)
        room_obj = wing_obj.create_room(plan.room)

        adr_id = f"ADR-{self._next_id(room_obj, 'adr'):04d}"

        adr = ADR(
            id=adr_id,
            name=f"[Generated] Decision for {plan.name}",
            room=plan.room,
            wing=plan.wing,
            status=Status.PROPOSED,
            created_at=datetime.now(),
            tags=['auto-generated'],
            downstream=[plan_id],  # Bidirectional link
        )

        # Save ADR
        adr_drawer = Drawer('adr', adr.id, f"# {adr.name}\n\n(Auto-generated)\n", adr.to_dict())
        room_obj.drawer_manager.save(adr_drawer)

        return adr_id

    def _next_id(self, room_obj, artifact_type: str) -> int:
        """Get next ID number for artifact type."""
        all_artifacts = room_obj.drawer_manager.list_all()
        key = artifact_type + 's'
        artifacts = all_artifacts.get(key, [])
        return len(artifacts) + 1

    def backfill_lineage(self, wing_name: str, auto_generate: bool = False) -> dict:
        """
        Audit and backfill missing upstreams in a wing.

        Returns: {'filled': count, 'warnings': []}
        """
        wing = self.palace.get_wing(wing_name)
        if not wing:
            return {'filled': 0, 'warnings': [f"Wing '{wing_name}' not found"]}

        filled = 0
        warnings = []

        for room_name, room in wing.rooms.items():
            all_artifacts = room.drawer_manager.list_all()

            # Check Concepts for missing Ideas
            for concept in all_artifacts['concepts']:
                if not concept.upstream:
                    if auto_generate:
                        idea_id = self.ensure_idea_upstream(concept.id, concept, auto_generate=True)
                        if idea_id:
                            filled += 1
                    else:
                        warnings.append(f"{concept.id}: missing upstream Idea")

            # Check ADRs for missing Concepts
            for adr in all_artifacts['adrs']:
                if not adr.upstream:
                    if auto_generate:
                        concept_id = self.ensure_concept_upstream(adr.id, adr, auto_generate=True)
                        if concept_id:
                            filled += 1
                    else:
                        warnings.append(f"{adr.id}: missing upstream Concept")

            # Check Plans for missing ADRs
            for plan in all_artifacts['plans']:
                if not plan.upstream:
                    if auto_generate:
                        adr_id = self.ensure_adr_upstream(plan.id, plan, auto_generate=True)
                        if adr_id:
                            filled += 1
                    else:
                        warnings.append(f"{plan.id}: missing upstream ADR")

        return {'filled': filled, 'warnings': warnings}
