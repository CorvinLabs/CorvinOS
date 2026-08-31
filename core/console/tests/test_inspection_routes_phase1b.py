"""
Tests for Phase 1.2: Inspection API Routes

Tests cover:
- Task inspection (list, detail)
- Skill inspection (list, detail)
- Category inspection (list, detail)
- Tenant isolation
- Pagination and filtering
- Error handling
- GDPR compliance (no PII)
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# Import the inspection module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from corvin_console.routes.inspection import (
    TaskInspector,
    SkillInspector,
    CategoryInspector,
    validate_tenant_id,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_corvin_home(tmp_path):
    """Create a temporary CorvinOS home directory."""
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir()

    # Create tenant structure
    tenant_path = corvin_home / "tenants" / "_default"
    tenant_path.mkdir(parents=True)

    # Create tasks directory
    tasks_path = tenant_path / "tasks"
    tasks_path.mkdir()

    # Create skills directory
    skills_path = tenant_path / "skills"
    skills_path.mkdir()

    return corvin_home, tenant_path


@pytest.fixture
def sample_task_registry(temp_corvin_home):
    """Create a sample task registry."""
    corvin_home, tenant_path = temp_corvin_home
    registry_path = tenant_path / "tasks" / "registry.jsonl"

    tasks = [
        {
            "task_id": "task-001",
            "title": "Implement feature X",
            "status": "running",
            "created_at": "2026-08-27T10:00:00",
            "updated_at": "2026-08-27T11:00:00",
            "parent_task_id": None,
            "phases": {
                "phase-001": {
                    "phase_id": "phase-001",
                    "status": "running",
                    "started_at": "2026-08-27T10:00:00",
                    "completed_at": None,
                    "retry_count": 0,
                    "error": None,
                }
            },
        },
        {
            "task_id": "task-002",
            "title": "Fix bug Y",
            "status": "completed",
            "created_at": "2026-08-26T10:00:00",
            "updated_at": "2026-08-27T09:00:00",
            "parent_task_id": None,
            "phases": {
                "phase-002": {
                    "phase_id": "phase-002",
                    "status": "completed",
                    "started_at": "2026-08-26T10:00:00",
                    "completed_at": "2026-08-27T08:00:00",
                    "retry_count": 1,
                    "error": None,
                }
            },
        },
        {
            "task_id": "task-003",
            "title": "Review docs",
            "status": "paused",
            "created_at": "2026-08-25T10:00:00",
            "updated_at": "2026-08-27T12:00:00",
            "parent_task_id": "task-001",
            "phases": {},
        },
    ]

    with open(registry_path, 'w') as f:
        for task in tasks:
            f.write(json.dumps(task) + '\n')

    return corvin_home, registry_path


@pytest.fixture
def sample_skills_directory(temp_corvin_home):
    """Create a sample skills directory structure."""
    corvin_home, tenant_path = temp_corvin_home
    skills_base = tenant_path / "skills"

    # Create _shared scope
    shared_scope = skills_base / "_shared" / "skills"
    shared_scope.mkdir(parents=True)

    # Create assistant scope
    assistant_scope = skills_base / "assistant" / "skills"
    assistant_scope.mkdir(parents=True)

    # Skill 1: in _shared scope
    skill1_path = shared_scope / "analyze-data"
    skill1_path.mkdir()
    with open(skill1_path / "manifest.json", 'w') as f:
        json.dump({
            "id": "analyze-data",
            "version": "1.0.0",
            "category": "data-analysis",
            "description": "Analyze data with statistical methods",
            "author": "data-team",
            "created_at": "2026-08-01T10:00:00",
            "dependencies": [
                {"id": "pandas-utils", "scope": "_shared", "version": "^1.0"}
            ],
            "tags": ["data", "analysis", "statistical"],
        }, f)

    with open(skill1_path / "config.json", 'w') as f:
        json.dump({"enabled": True, "timeout": 30}, f)

    # Skill 2: in _shared scope
    skill2_path = shared_scope / "pandas-utils"
    skill2_path.mkdir()
    with open(skill2_path / "manifest.json", 'w') as f:
        json.dump({
            "id": "pandas-utils",
            "version": "1.0.0",
            "category": "utilities",
            "description": "Pandas utility functions",
            "dependencies": [],
            "tags": ["utilities", "pandas"],
        }, f)

    with open(skill2_path / "config.json", 'w') as f:
        json.dump({"enabled": True}, f)

    # Skill 3: in assistant scope, disabled
    skill3_path = assistant_scope / "custom-skill"
    skill3_path.mkdir()
    with open(skill3_path / "manifest.json", 'w') as f:
        json.dump({
            "id": "custom-skill",
            "version": "2.0.0",
            "category": "custom",
            "description": "Custom skill for assistant",
            "dependencies": [],
        }, f)

    with open(skill3_path / "config.json", 'w') as f:
        json.dump({"enabled": False, "notes": "In development"}, f)

    return corvin_home, skills_base


# ============================================================================
# TENANT VALIDATION TESTS
# ============================================================================

class TestTenantValidation:
    """Test tenant ID validation."""

    def test_validate_tenant_id_valid(self):
        """Valid tenant IDs should pass."""
        assert validate_tenant_id("_default") is True
        assert validate_tenant_id("tenant-1") is True
        assert validate_tenant_id("tenant_prod_a") is True
        assert validate_tenant_id("abc123") is True

    def test_validate_tenant_id_invalid(self):
        """Invalid tenant IDs should fail."""
        assert validate_tenant_id("") is False
        assert validate_tenant_id(None) is False
        assert validate_tenant_id("tenant@invalid") is False
        assert validate_tenant_id("tenant with spaces") is False
        assert validate_tenant_id("a" * 256) is False


# ============================================================================
# TASK INSPECTION TESTS
# ============================================================================

class TestTaskInspector:
    """Test TaskInspector class."""

    def test_list_tasks_empty(self, temp_corvin_home):
        """List tasks when registry is empty."""
        corvin_home, tenant_path = temp_corvin_home

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")
            tasks, total = inspector.list_tasks()

            assert tasks == []
            assert total == 0

    def test_list_tasks_basic(self, sample_task_registry):
        """List all tasks."""
        corvin_home, registry_path = sample_task_registry

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")
            tasks, total = inspector.list_tasks()

            assert len(tasks) == 3
            assert total == 3
            assert tasks[0]['task_id'] == "task-001"
            assert tasks[0]['title'] == "Implement feature X"
            assert tasks[0]['status'] == "running"
            assert tasks[0]['phase_count'] == 1

    def test_list_tasks_with_status_filter(self, sample_task_registry):
        """Filter tasks by status."""
        corvin_home, registry_path = sample_task_registry

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")
            tasks, total = inspector.list_tasks(status="completed")

            assert len(tasks) == 1
            assert tasks[0]['task_id'] == "task-002"
            assert tasks[0]['status'] == "completed"

    def test_list_tasks_with_pagination(self, sample_task_registry):
        """Test pagination."""
        corvin_home, registry_path = sample_task_registry

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")

            # Get first 2 tasks
            tasks1, total = inspector.list_tasks(limit=2, offset=0)
            assert len(tasks1) == 2
            assert total == 3

            # Get next task
            tasks2, total = inspector.list_tasks(limit=2, offset=2)
            assert len(tasks2) == 1
            assert total == 3

    def test_get_task_not_found(self, temp_corvin_home):
        """Get non-existent task."""
        corvin_home, tenant_path = temp_corvin_home

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")
            task = inspector.get_task("task-invalid")

            assert task is None

    def test_get_task_found(self, sample_task_registry):
        """Get task details."""
        corvin_home, registry_path = sample_task_registry

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")
            task = inspector.get_task("task-001")

            assert task is not None
            assert task['task_id'] == "task-001"
            assert task['title'] == "Implement feature X"
            assert task['status'] == "running"
            assert "phases" in task
            assert len(task['phases']) == 1
            assert task['tenant_id'] == "_default"


# ============================================================================
# SKILL INSPECTION TESTS
# ============================================================================

class TestSkillInspector:
    """Test SkillInspector class."""

    def test_list_skills_empty(self, temp_corvin_home):
        """List skills when directory is empty."""
        corvin_home, tenant_path = temp_corvin_home

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skills, total = inspector.list_skills()

            assert skills == []
            assert total == 0

    def test_list_skills_basic(self, sample_skills_directory):
        """List all skills."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skills, total = inspector.list_skills()

            assert total == 3
            assert len(skills) == 3

            # Check skill info is present
            skill_ids = [s['skill_id'] for s in skills]
            assert "analyze-data" in skill_ids
            assert "pandas-utils" in skill_ids
            assert "custom-skill" in skill_ids

    def test_list_skills_filter_by_scope(self, sample_skills_directory):
        """Filter skills by scope."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skills, total = inspector.list_skills(scope="_shared")

            # Should only get skills from _shared scope
            skill_ids = [s['skill_id'] for s in skills]
            assert "analyze-data" in skill_ids
            assert "pandas-utils" in skill_ids
            assert "custom-skill" not in skill_ids

    def test_list_skills_enabled_only(self, sample_skills_directory):
        """Filter to show only enabled skills."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skills, total = inspector.list_skills(enabled_only=True)

            # custom-skill is disabled, so should not appear
            skill_ids = [s['skill_id'] for s in skills]
            assert "custom-skill" not in skill_ids
            assert len(skills) == 2

    def test_list_skills_with_pagination(self, sample_skills_directory):
        """Test pagination."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")

            # Get first 2 skills
            skills1, total = inspector.list_skills(limit=2, offset=0)
            assert len(skills1) == 2
            assert total == 3

            # Get next skill
            skills2, total = inspector.list_skills(limit=2, offset=2)
            assert len(skills2) == 1

    def test_get_skill_not_found(self, temp_corvin_home):
        """Get non-existent skill."""
        corvin_home, tenant_path = temp_corvin_home

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skill = inspector.get_skill("skill-invalid", "_shared")

            assert skill is None

    def test_get_skill_found(self, sample_skills_directory):
        """Get skill details."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skill = inspector.get_skill("analyze-data", "_shared")

            assert skill is not None
            assert skill['skill_id'] == "analyze-data"
            assert skill['scope'] == "_shared"
            assert skill['version'] == "1.0.0"
            assert skill['enabled'] is True
            assert skill['category'] == "data-analysis"
            assert "dependencies" in skill
            assert len(skill['dependencies']) == 1
            assert "tags" in skill
            assert "data" in skill['tags']

    def test_get_skill_with_dependencies(self, sample_skills_directory):
        """Skill with dependencies should list them."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skill = inspector.get_skill("analyze-data", "_shared")

            assert len(skill['dependencies']) == 1
            dep = skill['dependencies'][0]
            assert dep['id'] == "pandas-utils"
            assert dep['scope'] == "_shared"


# ============================================================================
# CATEGORY INSPECTION TESTS
# ============================================================================

class TestCategoryInspector:
    """Test CategoryInspector class."""

    def test_list_categories_empty(self, temp_corvin_home):
        """List categories when no skills exist."""
        corvin_home, tenant_path = temp_corvin_home

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = CategoryInspector("_default")
            categories = inspector.list_categories()

            assert categories == []

    def test_list_categories_basic(self, sample_skills_directory):
        """List all categories."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = CategoryInspector("_default")
            categories = inspector.list_categories()

            # Should have data-analysis, utilities, and custom categories
            category_ids = [c['category_id'] for c in categories]
            assert "data-analysis" in category_ids
            assert "utilities" in category_ids
            assert "custom" in category_ids

    def test_list_categories_counts(self, sample_skills_directory):
        """Categories should have correct skill counts."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = CategoryInspector("_default")
            categories = inspector.list_categories()

            # Find each category and check counts
            category_map = {c['category_id']: c for c in categories}

            assert category_map['data-analysis']['skill_count'] == 1
            assert "analyze-data" in category_map['data-analysis']['skills']

            assert category_map['utilities']['skill_count'] == 1
            assert "pandas-utils" in category_map['utilities']['skills']

            assert category_map['custom']['skill_count'] == 1
            assert "custom-skill" in category_map['custom']['skills']

    def test_get_category_not_found(self, temp_corvin_home):
        """Get non-existent category."""
        corvin_home, tenant_path = temp_corvin_home

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = CategoryInspector("_default")
            category = inspector.get_category("category-invalid")

            assert category is None

    def test_get_category_found(self, sample_skills_directory):
        """Get category details."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = CategoryInspector("_default")
            category = inspector.get_category("data-analysis")

            assert category is not None
            assert category['category_id'] == "data-analysis"
            assert category['skill_count'] == 1
            assert "analyze-data" in category['skills']


# ============================================================================
# GDPR COMPLIANCE TESTS
# ============================================================================

class TestGDPRCompliance:
    """Test GDPR compliance (no PII in responses)."""

    def test_task_response_no_pii(self, sample_task_registry):
        """Task response should not contain PII."""
        corvin_home, registry_path = sample_task_registry

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")
            tasks, _ = inspector.list_tasks()

            for task in tasks:
                # Should not contain email addresses
                task_str = json.dumps(task)
                # Should not contain obvious email pattern (text@text)
                import re
                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                assert not re.search(email_pattern, task_str.lower())

    def test_skill_response_no_pii(self, sample_skills_directory):
        """Skill response should not contain PII."""
        corvin_home, skills_base = sample_skills_directory

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skills, _ = inspector.list_skills()

            for skill in skills:
                skill_str = json.dumps(skill)
                # Author field should not contain full personal info
                # (only team names or handles are acceptable)
                assert "@" not in skill_str or "team" in skill_str.lower()


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling:
    """Test error handling in inspectors."""

    def test_task_inspector_corrupted_json(self, sample_task_registry):
        """Corrupted JSON in registry should be skipped."""
        corvin_home, registry_path = sample_task_registry

        # Corrupt one line
        with open(registry_path, 'a') as f:
            f.write("{ corrupted json\n")

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = TaskInspector("_default")
            tasks, total = inspector.list_tasks()

            # Should still return valid tasks, skip corrupted ones
            assert len(tasks) == 3
            assert total == 3

    def test_skill_inspector_missing_manifest(self, sample_skills_directory):
        """Skills with missing manifest should be handled gracefully."""
        corvin_home, skills_base = sample_skills_directory

        # Create skill without manifest
        bad_skill_path = (skills_base / "_shared" / "skills" / "no-manifest")
        bad_skill_path.mkdir()

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            inspector = SkillInspector("_default")
            skills, total = inspector.list_skills()

            # Should still list other skills
            assert total >= 3
            skill_ids = [s['skill_id'] for s in skills]
            assert "analyze-data" in skill_ids


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestInspectionIntegration:
    """Integration tests for multiple inspectors."""

    def test_full_inspection_workflow(self, sample_task_registry, sample_skills_directory):
        """Test complete inspection workflow."""
        corvin_home, _ = sample_task_registry

        with patch('corvin_console.routes.inspection.CORVIN_HOME', corvin_home):
            # List tasks
            task_inspector = TaskInspector("_default")
            tasks, task_total = task_inspector.list_tasks()
            assert task_total == 3

            # Get task details
            task = task_inspector.get_task("task-001")
            assert task is not None

            # List skills
            skill_inspector = SkillInspector("_default")
            skills, skill_total = skill_inspector.list_skills()
            assert skill_total == 3

            # Get skill details
            skill = skill_inspector.get_skill("analyze-data", "_shared")
            assert skill is not None

            # List categories
            category_inspector = CategoryInspector("_default")
            categories = category_inspector.list_categories()
            assert len(categories) >= 2

            # Get category details
            category = category_inspector.get_category("data-analysis")
            assert category is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
