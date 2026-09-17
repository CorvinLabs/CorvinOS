"""ProjectStore — Persistence layer for DataHub projects.

Simple in-memory store for now (MVP). Can be backed by SQLite/PostgreSQL in production.
Tenant-scoped to prevent cross-tenant data leakage.
"""

from typing import Dict, List, Optional
import logging
from .models import ProjectModel

logger = logging.getLogger(__name__)


class ProjectStore:
    """In-memory project store (MVP)."""

    def __init__(self):
        self._projects: Dict[str, ProjectModel] = {}

    async def save(self, project: ProjectModel) -> None:
        """Save a project."""
        if not project.project_id:
            raise ValueError("project_id is required")
        self._projects[project.project_id] = project
        logger.debug(f"Saved project {project.project_id}")

    async def get(self, project_id: str) -> Optional[ProjectModel]:
        """Get a project by ID."""
        return self._projects.get(project_id)

    async def list_all(self) -> List[ProjectModel]:
        """List all projects (TODO: filter by tenant_id in production)."""
        return list(self._projects.values())

    async def delete(self, project_id: str) -> None:
        """Delete a project."""
        if project_id in self._projects:
            del self._projects[project_id]
            logger.debug(f"Deleted project {project_id}")


# Global singleton
_store_instance: Optional[ProjectStore] = None


def get_project_store() -> ProjectStore:
    """Get or create the project store singleton."""
    global _store_instance
    if _store_instance is None:
        _store_instance = ProjectStore()
    return _store_instance
