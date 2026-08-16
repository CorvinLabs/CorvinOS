"""
Drawer I/O: Immutable artifact storage (MemPalace-inspired)

Each artifact is a markdown file with YAML frontmatter.
Drawers are type-specific subdirectories: ideas/, concepts/, adrs/, implementation-plans/
"""

import os
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import yaml

# Status enum will be imported in list_by_type to avoid circular imports


class Drawer:
    """Immutable markdown artifact with YAML frontmatter."""

    def __init__(self, artifact_type: str, artifact_id: str, content: str, metadata: Dict[str, Any]):
        """
        artifact_type: 'idea' | 'concept' | 'adr' | 'implementation-plan'
        artifact_id: IDEA-0042 | CONCEPT-0008 | ADR-0321 | IMPL-0001
        content: Raw markdown body (verbatim, never summarized)
        metadata: YAML frontmatter dict
        """
        self.type = artifact_type
        self.id = artifact_id
        self.content = content
        self.metadata = metadata

        # Validate
        assert self.type in ['idea', 'concept', 'adr', 'implementation-plan'], f"Invalid type: {self.type}"
        assert self.metadata.get('status') in ['draft', 'proposed', 'approved', 'active', 'superseded', 'archived']
        assert isinstance(self.metadata.get('tags', []), list)

    def to_markdown(self) -> str:
        """Serialize to markdown with YAML frontmatter."""
        frontmatter = yaml.dump(self.metadata, default_flow_style=False, sort_keys=False)
        return f"---\n{frontmatter}---\n\n{self.content}"

    @classmethod
    def from_markdown(cls, text: str, artifact_type: str) -> 'Drawer':
        """Parse markdown with YAML frontmatter."""
        # Extract frontmatter
        match = re.match(r'^---\n(.*?)\n---\n(.*)', text, re.DOTALL)
        if not match:
            raise ValueError("Invalid markdown format: missing YAML frontmatter")

        frontmatter_str, content = match.groups()
        metadata = yaml.safe_load(frontmatter_str)
        artifact_id = metadata.get('id')

        return cls(artifact_type, artifact_id, content, metadata)


class DrawerManager:
    """Manage drawers (artifacts) in a Wing."""

    def __init__(self, wing_path: Path):
        """
        wing_path: Path to Wing root (e.g., ~/.corvin/tenants/_default/idea-pipeline/)
        """
        self.wing_path = wing_path
        self.wing_path.mkdir(parents=True, exist_ok=True)

        # Drawer directories
        self.drawers = {
            'idea': self.wing_path / 'ideas',
            'concept': self.wing_path / 'concepts',
            'adr': self.wing_path / 'adrs',
            'implementation-plan': self.wing_path / 'implementation-plans',
        }
        for drawer_dir in self.drawers.values():
            drawer_dir.mkdir(exist_ok=True)

    def save(self, drawer: Drawer) -> Path:
        """Save drawer to filesystem (immutable once written)."""
        drawer_dir = self.drawers[drawer.type]

        # Filename: {id}-{slug}.md
        slug = drawer.metadata.get('name', 'untitled').replace(' ', '-').lower()
        filename = f"{drawer.id.lower()}-{slug}.md"
        filepath = drawer_dir / filename

        # Prevent overwrite (immutable)
        if filepath.exists():
            raise FileExistsError(f"Drawer already exists: {filepath}. Drawers are immutable.")

        # Write
        filepath.write_text(drawer.to_markdown(), encoding='utf-8')

        # Update index
        self._update_index(drawer)

        return filepath

    def load(self, artifact_type: str, artifact_id: str) -> Optional[Drawer]:
        """Load drawer by ID."""
        drawer_dir = self.drawers[artifact_type]

        # Find file matching artifact_id
        for file in drawer_dir.glob(f"{artifact_id.lower()}-*.md"):
            text = file.read_text(encoding='utf-8')
            return Drawer.from_markdown(text, artifact_type)

        return None

    def list_by_type(self, artifact_type: str) -> List:
        """List all artifacts of a type (as Artifact models)."""
        from ..models.artifact import ARTIFACT_CLASSES, ArtifactType, Status
        from datetime import datetime

        drawer_dir = self.drawers[artifact_type]
        artifacts = []
        artifact_class = ARTIFACT_CLASSES.get(ArtifactType(artifact_type))

        for file in drawer_dir.glob("*.md"):
            text = file.read_text(encoding='utf-8')
            drawer = Drawer.from_markdown(text, artifact_type)
            metadata = drawer.metadata

            # Parse dates
            created_at = metadata.get('created_at')
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            approved_at = metadata.get('approved_at')
            if isinstance(approved_at, str):
                approved_at = datetime.fromisoformat(approved_at)

            # Convert status to enum
            status = metadata.get('status')
            if isinstance(status, str):
                status = Status(status)

            # Create artifact
            artifact = artifact_class(
                id=metadata.get('id'),
                name=metadata.get('name'),
                room=metadata.get('room', ''),
                wing=metadata.get('wing', ''),
                status=status,
                created_at=created_at,
                approved_at=approved_at,
                upstream=metadata.get('upstream'),
                downstream=metadata.get('downstream', []),
                tags=metadata.get('tags', []),
            )
            artifacts.append(artifact)

        return sorted(artifacts, key=lambda a: getattr(a, 'name', ''))

    def list_all(self) -> Dict[str, List]:
        """List all artifacts organized by type."""
        return {
            'ideas': self.list_by_type('idea'),
            'concepts': self.list_by_type('concept'),
            'adrs': self.list_by_type('adr'),
            'plans': self.list_by_type('implementation-plan'),
        }

    def _update_index(self, drawer: Drawer) -> None:
        """Update metadata.jsonl index for fast lookups."""
        index_file = self.wing_path / 'metadata.jsonl'

        # Read existing
        existing = {}
        if index_file.exists():
            for line in index_file.read_text().strip().split('\n'):
                if line:
                    entry = json.loads(line)
                    existing[entry['id']] = entry

        # Add/update
        existing[drawer.id] = {
            'id': drawer.id,
            'type': drawer.type,
            'name': drawer.metadata.get('name'),
            'status': drawer.metadata.get('status'),
            'created_at': drawer.metadata.get('created_at'),
            'upstream': drawer.metadata.get('upstream'),
            'downstream': drawer.metadata.get('downstream', []),
            'tags': drawer.metadata.get('tags', []),
        }

        # Write back (append-only semantics)
        with open(index_file, 'w') as f:
            for entry in existing.values():
                f.write(json.dumps(entry) + '\n')

    def search_by_index(self, query: str) -> List[Dict[str, Any]]:
        """Fast search using metadata.jsonl index."""
        index_file = self.wing_path / 'metadata.jsonl'
        if not index_file.exists():
            return []

        results = []
        query_lower = query.lower()

        for line in index_file.read_text().strip().split('\n'):
            if not line:
                continue
            entry = json.loads(line)

            # Match name, tags, or id
            if (query_lower in entry.get('name', '').lower() or
                query_lower in str(entry.get('tags', [])).lower() or
                query_lower in entry.get('id', '').lower()):
                results.append(entry)

        return results


# Testing tier-0: Schema validation
def validate_metadata_schema(metadata: Dict[str, Any]) -> List[str]:
    """Validate metadata schema. Returns list of errors (empty = valid)."""
    errors = []

    required = ['id', 'type', 'status', 'created_at']
    for field in required:
        if field not in metadata:
            errors.append(f"Missing required field: {field}")

    if metadata.get('status') not in ['draft', 'proposed', 'approved', 'active', 'superseded', 'archived']:
        errors.append(f"Invalid status: {metadata.get('status')}")

    if not isinstance(metadata.get('tags', []), list):
        errors.append("tags must be a list")

    if metadata.get('downstream') is not None and not isinstance(metadata.get('downstream'), list):
        errors.append("downstream must be a list or null")

    return errors
