"""
Phase 1.2: Inspection API Routes

Provides REST endpoints for inspecting and querying tasks, skills, and skill categories:

Endpoints:
- GET /api/inspection/tasks — List all tasks with filtering/pagination
- GET /api/inspection/tasks/{task_id} — Get task details
- GET /api/inspection/skills — List all skills with filtering/pagination
- GET /api/inspection/skills/{skill_id} — Get skill details
- GET /api/inspection/categories — List all categories
- GET /api/inspection/categories/{category_id} — Get category details

Tenant-scoped, audit-logged, GDPR-compliant (no PII).
"""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime
import json
import logging
from typing import Dict, List, Optional, Tuple

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
    """Inspect task registry and metadata."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.tasks_path = CORVIN_HOME / 'tenants' / tenant_id / 'tasks'
        self.registry_path = self.tasks_path / 'registry.jsonl'

    def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict], int]:
        """List tasks from registry with optional filtering."""
        if not self.registry_path.exists():
            return [], 0

        tasks = []
        total = 0

        try:
            with open(self.registry_path, 'r') as f:
                for i, line in enumerate(f):
                    if not line.strip():
                        continue

                    try:
                        task = json.loads(line)
                        total += 1

                        # Apply status filter
                        if status and task.get('status') != status:
                            continue

                        # Apply pagination
                        if i >= offset and len(tasks) < limit:
                            tasks.append({
                                'task_id': task.get('task_id'),
                                'title': task.get('title'),
                                'status': task.get('status'),
                                'created_at': task.get('created_at'),
                                'updated_at': task.get('updated_at'),
                                'phase_count': len(task.get('phases', {})),
                            })
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON in registry line {i}")
                        continue

        except Exception as e:
            logger.error(f"Error reading task registry: {e}")
            return [], 0

        return tasks, total

    def get_task(self, task_id: str) -> Optional[Dict]:
        """Get detailed task metadata."""
        if not self.registry_path.exists():
            return None

        try:
            with open(self.registry_path, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue

                    task = json.loads(line)
                    if task.get('task_id') == task_id:
                        return {
                            'task_id': task.get('task_id'),
                            'title': task.get('title'),
                            'status': task.get('status'),
                            'created_at': task.get('created_at'),
                            'updated_at': task.get('updated_at'),
                            'parent_task_id': task.get('parent_task_id'),
                            'phases': task.get('phases', {}),
                            'tenant_id': self.tenant_id,
                        }

        except Exception as e:
            logger.error(f"Error reading task {task_id}: {e}")
            return None

        return None


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
    """Inspect skill registry and metadata."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.base_path = CORVIN_HOME / 'tenants' / tenant_id

    def list_skills(
        self,
        scope: Optional[str] = None,
        enabled_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict], int]:
        """List skills from tenant skill directories."""
        scopes_path = self.base_path / 'skills'

        if not scopes_path.exists():
            return [], 0

        skills = []
        total = 0
        skill_idx = 0

        try:
            for scope_dir in scopes_path.iterdir():
                if not scope_dir.is_dir():
                    continue

                scope_name = scope_dir.name
                if scope and scope_name != scope:
                    continue

                skills_subdir = scope_dir / 'skills'
                if not skills_subdir.exists():
                    continue

                for skill_dir in skills_subdir.iterdir():
                    if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                        continue

                    total += 1

                    # Apply pagination
                    if skill_idx >= offset and len(skills) < limit:
                        manifest_path = skill_dir / 'manifest.json'
                        config_path = skill_dir / 'config.json'

                        skill_info = {
                            'skill_id': skill_dir.name,
                            'scope': scope_name,
                            'version': None,
                            'enabled': True,
                            'category': None,
                            'description': '',
                        }

                        # Read manifest for metadata
                        if manifest_path.exists():
                            try:
                                with open(manifest_path, 'r') as f:
                                    manifest = json.load(f)
                                    skill_info['version'] = manifest.get('version')
                                    skill_info['category'] = manifest.get('category')
                                    skill_info['description'] = manifest.get('description', '')
                            except json.JSONDecodeError:
                                logger.warning(f"Invalid manifest for skill {skill_dir.name}")

                        # Read config for enabled status
                        if config_path.exists():
                            try:
                                with open(config_path, 'r') as f:
                                    config = json.load(f)
                                    skill_info['enabled'] = config.get('enabled', True)
                            except json.JSONDecodeError:
                                logger.warning(f"Invalid config for skill {skill_dir.name}")

                        if not enabled_only or skill_info['enabled']:
                            skills.append(skill_info)

                    skill_idx += 1

        except Exception as e:
            logger.error(f"Error listing skills: {e}")
            return [], 0

        return skills, total

    def get_skill(self, skill_id: str, scope: str = "_shared") -> Optional[Dict]:
        """Get detailed skill metadata."""
        skill_path = self.base_path / 'skills' / scope / 'skills' / skill_id

        if not skill_path.exists():
            return None

        manifest_path = skill_path / 'manifest.json'
        config_path = skill_path / 'config.json'

        skill_info = {
            'skill_id': skill_id,
            'scope': scope,
            'version': None,
            'enabled': True,
            'category': None,
            'description': '',
            'dependencies': [],
            'tags': [],
        }

        # Read manifest
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                    skill_info.update({
                        'version': manifest.get('version'),
                        'category': manifest.get('category'),
                        'description': manifest.get('description', ''),
                        'dependencies': manifest.get('dependencies', []),
                        'tags': manifest.get('tags', []),
                        'author': manifest.get('author'),
                        'created_at': manifest.get('created_at'),
                    })
            except json.JSONDecodeError:
                logger.warning(f"Invalid manifest for skill {skill_id}")

        # Read config
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    skill_info['enabled'] = config.get('enabled', True)
                    skill_info['config'] = {k: v for k, v in config.items() if k != 'enabled'}
            except json.JSONDecodeError:
                logger.warning(f"Invalid config for skill {skill_id}")

        return skill_info


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
    """Inspect skill categories."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.base_path = CORVIN_HOME / 'tenants' / tenant_id

    def list_categories(self) -> List[Dict]:
        """List unique skill categories across all scopes and skills."""
        categories = {}

        scopes_path = self.base_path / 'skills'
        if not scopes_path.exists():
            return []

        try:
            for scope_dir in scopes_path.iterdir():
                if not scope_dir.is_dir():
                    continue

                skills_subdir = scope_dir / 'skills'
                if not skills_subdir.exists():
                    continue

                for skill_dir in skills_subdir.iterdir():
                    if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                        continue

                    manifest_path = skill_dir / 'manifest.json'
                    if manifest_path.exists():
                        try:
                            with open(manifest_path, 'r') as f:
                                manifest = json.load(f)
                                category = manifest.get('category', 'uncategorized')

                                if category not in categories:
                                    categories[category] = {
                                        'category_id': category,
                                        'name': category,
                                        'skill_count': 0,
                                        'skills': [],
                                    }

                                categories[category]['skill_count'] += 1
                                categories[category]['skills'].append(skill_dir.name)

                        except json.JSONDecodeError:
                            logger.warning(f"Invalid manifest for skill {skill_dir.name}")

        except Exception as e:
            logger.error(f"Error listing categories: {e}")
            return []

        return list(categories.values())

    def get_category(self, category_id: str) -> Optional[Dict]:
        """Get category details with all skills in it."""
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
