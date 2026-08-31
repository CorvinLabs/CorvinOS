"""
Phase 1.2: Inspection API Routes

Provides REST endpoints for inspecting and querying tasks, skills, and skill categories.
Built on top of Phase 1 Query Engine framework (TaskGraphQuery, SkillToolQuery, CategoryQuery).

Endpoints:
- GET /api/inspection/tasks — List all tasks with filtering/pagination
- GET /api/inspection/tasks/{task_id} — Get task details
- GET /api/inspection/skills — List all skills with filtering/pagination
- GET /api/inspection/skills/{skill_id} — Get skill details
- GET /api/inspection/categories — List all categories
- GET /api/inspection/categories/{category_id} — Get category details

Tenant-scoped, audit-logged, GDPR-compliant (no PII).

Query Engines Used:
- TaskGraphQuery: List and query tasks from registry
- SkillToolQuery: List and query skills from skill directories
- CategoryQuery: Aggregate events and compute health metrics
"""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime
import json
import logging
from typing import Dict, List, Optional, Tuple

# Import Phase 1 Query Engines
from ..query_engines import TaskGraphQuery, SkillToolQuery, CategoryQuery
from ..inspection_models import TaskStatus

# Initialize logger
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inspection", tags=["inspection"])

# Tenant path resolution
CORVIN_HOME = Path.home() / '.corvin'


def validate_tenant_id(tenant_id: str) -> bool:
    """Validate tenant ID format."""
    if not tenant_id or len(tenant_id) > 255:
        return False
    # Allowed: alphanumeric, underscore, hyphen
    return all(c.isalnum() or c in ('_', '-') for c in tenant_id)


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class TaskResponse(BaseModel):
    """Task summary in list view."""
    task_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    phase_count: int


class TaskDetailResponse(BaseModel):
    """Task detail with all phases."""
    task_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    parent_task_id: Optional[str]
    phases: Dict
    tenant_id: str
    generated_at: str


class SkillResponse(BaseModel):
    """Skill summary in list view."""
    skill_id: str
    scope: str
    version: Optional[str]
    enabled: bool
    category: Optional[str]
    description: str = ""


class SkillDetailResponse(BaseModel):
    """Skill detail with config and dependencies."""
    skill_id: str
    scope: str
    version: Optional[str]
    enabled: bool
    category: Optional[str]
    description: str = ""
    dependencies: List[Dict] = []
    tags: List[str] = []
    author: Optional[str] = None
    created_at: Optional[str] = None
    config: Dict = {}
    generated_at: str


class CategoryResponse(BaseModel):
    """Category summary."""
    category_id: str
    name: str
    skill_count: int
    skills: List[str]


class TasksListResponse(BaseModel):
    """List of tasks with pagination info."""
    tasks: List[TaskResponse]
    total: int
    limit: int
    offset: int
    generated_at: str


class SkillsListResponse(BaseModel):
    """List of skills with pagination info."""
    skills: List[SkillResponse]
    total: int
    limit: int
    offset: int
    generated_at: str


class CategoriesListResponse(BaseModel):
    """List of categories."""
    categories: List[CategoryResponse]
    total: int
    generated_at: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str
    version: str
    timestamp: str


# ============================================================================
# TASK INSPECTION ENDPOINTS
# ============================================================================

class TaskInspector:
    """Inspect task registry and metadata using TaskGraphQuery engine."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.query_engine = TaskGraphQuery(tenant_id=tenant_id)

    def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict], int]:
        """
        List tasks from registry using TaskGraphQuery engine.

        Uses Phase 1 Query Engine for tenant-isolated, GDPR-compliant access.
        """
        # Convert string status to TaskStatus enum if provided
        task_status = None
        if status:
            try:
                task_status = TaskStatus[status.upper()]
            except KeyError:
                pass

        # Use QueryEngine to list tasks
        task_nodes, total = self.query_engine.list_tasks(
            status=task_status,
            limit=limit,
            offset=offset,
        )

        # Convert TaskNode objects to dictionary format for API response
        tasks = []
        for node in task_nodes:
            tasks.append({
                'task_id': node.task_id,
                'title': node.name,
                'status': node.status.value,
                'created_at': node.created_at.isoformat() if node.created_at else None,
                'updated_at': node.completed_at.isoformat() if node.completed_at else None,
                'phase_count': 1,  # Single phase per task in current model
            })

        return tasks, total

    def get_task(self, task_id: str) -> Optional[Dict]:
        """
        Get detailed task metadata using TaskGraphQuery engine.

        Uses Phase 1 Query Engine for tenant-isolated access.
        """
        task_node = self.query_engine.get_task(task_id)

        if not task_node:
            return None

        return {
            'task_id': task_node.task_id,
            'title': task_node.name,
            'status': task_node.status.value,
            'created_at': task_node.created_at.isoformat() if task_node.created_at else None,
            'updated_at': task_node.completed_at.isoformat() if task_node.completed_at else None,
            'parent_task_id': task_node.parent_id,
            'phases': {},  # Placeholder; will be populated from task phases
            'tenant_id': self.tenant_id,
        }


@router.get("/tasks", response_model=TasksListResponse)
async def list_tasks(
    tenant_id: str = Query("_default"),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> TasksListResponse:
    """
    List all tasks with optional filtering and pagination.

    Query parameters:
    - tenant_id: Tenant scope (default: _default)
    - status: Filter by status (running, paused, completed, failed)
    - limit: Max tasks to return (default: 50, max: 500)
    - offset: Pagination offset (default: 0)
    """
    if not validate_tenant_id(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    inspector = TaskInspector(tenant_id)
    tasks, total = inspector.list_tasks(status=status, limit=limit, offset=offset)

    return TasksListResponse(
        tasks=[TaskResponse(**t) for t in tasks],
        total=total,
        limit=limit,
        offset=offset,
        generated_at=datetime.utcnow().isoformat(),
    )


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task_detail(
    task_id: str,
    tenant_id: str = Query("_default"),
) -> TaskDetailResponse:
    """Get detailed task metadata including phases and status."""
    if not validate_tenant_id(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    inspector = TaskInspector(tenant_id)
    task = inspector.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    task['generated_at'] = datetime.utcnow().isoformat()
    return TaskDetailResponse(**task)


# ============================================================================
# SKILL INSPECTION ENDPOINTS
# ============================================================================

class SkillInspector:
    """Inspect skill registry and metadata using SkillToolQuery engine."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.query_engine = SkillToolQuery(tenant_id=tenant_id)

    def list_skills(
        self,
        scope: Optional[str] = None,
        enabled_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict], int]:
        """
        List skills using SkillToolQuery engine.

        Uses Phase 1 Query Engine for tenant-isolated, GDPR-compliant access.
        """
        skill_nodes, total = self.query_engine.list_skills(
            scope=scope,
            enabled_only=enabled_only,
            limit=limit,
            offset=offset,
        )

        # Convert SkillMetadata objects to dictionary format for API response
        skills = []
        for node in skill_nodes:
            skills.append({
                'skill_id': node.skill_id,
                'scope': scope or '_shared',
                'version': node.version,
                'enabled': True,  # Could be enhanced to read from config
                'category': None,
                'description': node.description,
            })

        return skills, total

    def get_skill(self, skill_id: str, scope: str = "_shared") -> Optional[Dict]:
        """
        Get detailed skill metadata using SkillToolQuery engine.

        Uses Phase 1 Query Engine for tenant-isolated access.
        """
        skill_node = self.query_engine.get_skill(skill_id, scope)

        if not skill_node:
            return None

        return {
            'skill_id': skill_node.skill_id,
            'scope': scope,
            'version': skill_node.version,
            'enabled': True,
            'category': None,
            'description': skill_node.description,
            'dependencies': skill_node.depends_on_tools,
            'tags': skill_node.tags,
            'author': skill_node.owner,
            'created_at': skill_node.created_at.isoformat() if skill_node.created_at else None,
            'config': {},
        }


@router.get("/skills", response_model=SkillsListResponse)
async def list_skills(
    tenant_id: str = Query("_default"),
    scope: Optional[str] = Query(None),
    enabled_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SkillsListResponse:
    """
    List all skills with optional filtering and pagination.

    Query parameters:
    - tenant_id: Tenant scope (default: _default)
    - scope: Filter by skill scope (default: all scopes)
    - enabled_only: Return only enabled skills (default: false)
    - limit: Max skills to return (default: 50, max: 500)
    - offset: Pagination offset (default: 0)
    """
    if not validate_tenant_id(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    inspector = SkillInspector(tenant_id)
    skills, total = inspector.list_skills(
        scope=scope,
        enabled_only=enabled_only,
        limit=limit,
        offset=offset,
    )

    return SkillsListResponse(
        skills=[SkillResponse(**s) for s in skills],
        total=total,
        limit=limit,
        offset=offset,
        generated_at=datetime.utcnow().isoformat(),
    )


@router.get("/skills/{skill_id}", response_model=SkillDetailResponse)
async def get_skill_detail(
    skill_id: str,
    tenant_id: str = Query("_default"),
    scope: str = Query("_shared"),
) -> SkillDetailResponse:
    """Get detailed skill metadata including configuration and dependencies."""
    if not validate_tenant_id(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    inspector = SkillInspector(tenant_id)
    skill = inspector.get_skill(skill_id, scope)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")

    skill['generated_at'] = datetime.utcnow().isoformat()
    return SkillDetailResponse(**skill)


# ============================================================================
# CATEGORY INSPECTION ENDPOINTS
# ============================================================================

class CategoryInspector:
    """Inspect skill categories and health using CategoryQuery engine."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.query_engine = CategoryQuery(tenant_id=tenant_id)

    def list_categories(self) -> List[Dict]:
        """
        List unique skill categories using CategoryQuery engine.

        Uses Phase 1 Query Engine for tenant-isolated, GDPR-compliant access.
        """
        categories_list = self.query_engine.list_categories()

        # Convert to dictionary format for API response
        categories = []
        for cat in categories_list:
            categories.append({
                'category_id': cat,
                'name': cat,
                'skill_count': 0,  # Could be enhanced with actual skill count
                'skills': [],
            })

        return categories

    def get_category(self, category_id: str) -> Optional[Dict]:
        """
        Get category details using CategoryQuery engine.

        Uses Phase 1 Query Engine for tenant-isolated access.
        """
        # Verify category exists
        categories = self.list_categories()
        for cat in categories:
            if cat['category_id'] == category_id:
                return cat

        return None


@router.get("/categories", response_model=CategoriesListResponse)
async def list_categories(
    tenant_id: str = Query("_default"),
) -> CategoriesListResponse:
    """
    List all skill categories.

    Query parameters:
    - tenant_id: Tenant scope (default: _default)
    """
    if not validate_tenant_id(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    inspector = CategoryInspector(tenant_id)
    categories = inspector.list_categories()

    return CategoriesListResponse(
        categories=[CategoryResponse(**c) for c in categories],
        total=len(categories),
        generated_at=datetime.utcnow().isoformat(),
    )


@router.get("/categories/{category_id}", response_model=CategoryResponse)
async def get_category_detail(
    category_id: str,
    tenant_id: str = Query("_default"),
) -> CategoryResponse:
    """Get category details with all associated skills."""
    if not validate_tenant_id(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    inspector = CategoryInspector(tenant_id)
    category = inspector.get_category(category_id)

    if not category:
        raise HTTPException(status_code=404, detail=f"Category {category_id} not found")

    return CategoryResponse(**category)


# ============================================================================
# HEALTH CHECK
# ============================================================================

@router.get("/health", response_model=HealthResponse)
async def inspection_health() -> HealthResponse:
    """Health check endpoint for inspection API."""
    return HealthResponse(
        status="ok",
        service="inspection-api",
        version="1.2.0",
        timestamp=datetime.utcnow().isoformat(),
    )
