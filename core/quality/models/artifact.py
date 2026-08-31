"""
Artifact models for Idea-to-Implementation Pipeline.

Four types: Idea, Concept, ADR, ImplementationPlan
Each has upstream/downstream lineage.
"""

from datetime import datetime
from typing import Optional, List
from enum import Enum


class ArtifactType(str, Enum):
    """Artifact types in the pipeline."""
    IDEA = "idea"
    CONCEPT = "concept"
    ADR = "adr"
    IMPLEMENTATION_PLAN = "implementation-plan"


class Status(str, Enum):
    """Artifact lifecycle status."""
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class Artifact:
    """Base artifact model."""

    def __init__(
        self,
        id: str,
        type: ArtifactType,
        name: str,
        room: str,
        wing: str,
        status: Status,
        created_at: datetime,
        approved_at: Optional[datetime] = None,
        upstream: Optional[str] = None,
        downstream: List[str] = None,
        tags: List[str] = None,
        related_to: List[str] = None,
        references: List[str] = None,
        validity_window: Optional[tuple] = None,
        generated_from: Optional[str] = None,
        inspiration_context: Optional[str] = None,
    ):
        # Identity
        self.id = id
        self.type = type
        self.name = name
        self.room = room
        self.wing = wing

        # Lifecycle
        self.status = status
        self.created_at = created_at
        self.approved_at = approved_at

        # Lineage
        self.upstream = upstream
        self.downstream = downstream or []

        # Metadata
        self.tags = tags or []
        self.related_to = related_to or []
        self.references = references or []
        self.validity_window = validity_window

        # Genealogy
        self.generated_from = generated_from
        self.inspiration_context = inspiration_context

        # Validate
        self._validate()

    def _validate(self):
        """Validate on creation."""
        assert self.status in Status.__members__.values(), f"Invalid status: {self.status}"
        if self.upstream:
            assert isinstance(self.upstream, str), "upstream must be a string or None"
        assert isinstance(self.downstream, list), "downstream must be a list"
        assert isinstance(self.tags, list), "tags must be a list"

    def to_dict(self) -> dict:
        """Convert to dictionary (for YAML serialization)."""
        return {
            'id': self.id,
            'type': self.type.value,
            'name': self.name,
            'room': self.room,
            'wing': self.wing,
            'status': self.status.value,
            'created_at': self.created_at.isoformat(),
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'upstream': self.upstream,
            'downstream': self.downstream,
            'tags': self.tags,
            'related_to': self.related_to,
            'references': self.references,
            'validity_window': self.validity_window,
            'generated_from': self.generated_from,
            'inspiration_context': self.inspiration_context,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Artifact':
        """Create from dictionary (YAML deserialization)."""
        data_copy = data.copy()

        # Convert strings to enums
        data_copy['type'] = ArtifactType(data_copy['type'])
        data_copy['status'] = Status(data_copy['status'])

        # Parse datetimes
        if isinstance(data_copy['created_at'], str):
            data_copy['created_at'] = datetime.fromisoformat(data_copy['created_at'])
        if data_copy.get('approved_at') and isinstance(data_copy['approved_at'], str):
            data_copy['approved_at'] = datetime.fromisoformat(data_copy['approved_at'])

        return cls(**data_copy)


class Idea(Artifact):
    """Raw insight or problem statement."""
    def __init__(self, **kwargs):
        kwargs.setdefault('type', ArtifactType.IDEA)
        super().__init__(**kwargs)


class Concept(Artifact):
    """Reusable pattern (must have idea upstream)."""
    def __init__(self, **kwargs):
        kwargs.setdefault('type', ArtifactType.CONCEPT)
        super().__init__(**kwargs)

    def __post_init__(self):
        super().__post_init__()
        # Note: upstream validation happens in gates, not here


class ADR(Artifact):
    """Architecture Decision Record (must have concept upstream)."""
    def __init__(self, adr_number: Optional[int] = None, **kwargs):
        kwargs.setdefault('type', ArtifactType.ADR)
        super().__init__(**kwargs)
        self.adr_number = adr_number
        if self.id.startswith('ADR-'):
            try:
                self.adr_number = int(self.id.split('-')[1])
            except (ValueError, IndexError):
                # Malformed ID; leave adr_number as provided (or None)
                pass


class ImplementationPlan(Artifact):
    """Deployment steps (must have ADR upstream)."""
    def __init__(
        self,
        deployment_steps: List[str] = None,
        rollback_procedure: Optional[str] = None,
        success_criteria: Optional[str] = None,
        rollout_sequence: Optional[str] = None,
        **kwargs
    ):
        kwargs.setdefault('type', ArtifactType.IMPLEMENTATION_PLAN)
        super().__init__(**kwargs)
        self.deployment_steps = deployment_steps or []
        self.rollback_procedure = rollback_procedure
        self.success_criteria = success_criteria
        self.rollout_sequence = rollout_sequence

        if self.status not in [Status.DRAFT, Status.PROPOSED, Status.APPROVED]:
            raise ValueError(f"ImplementationPlan cannot be {self.status}")


# Type mapping
ARTIFACT_CLASSES = {
    ArtifactType.IDEA: Idea,
    ArtifactType.CONCEPT: Concept,
    ArtifactType.ADR: ADR,
    ArtifactType.IMPLEMENTATION_PLAN: ImplementationPlan,
}
