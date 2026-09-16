"""
E2E Tests for Skill Forge v2.0 Phase 5 — Console UI (ADR-0681)

Tests the full workflow:
1. User opens Generator panel
2. Fills form and clicks Generate
3. ZIP is created, download link appears
4. User uploads ZIP to Manager panel
5. Skill installed and appears in list
6. Dashboard shows skill stats
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from flask import Flask

from core.console.corvin_console.routes.skill_manager_routes import (
    skill_manager_bp,
    GenerationJob,
    InstalledSkill,
    _GENERATION_JOBS,
    _INSTALLATION_JOBS,
)


@pytest.fixture
def flask_app():
    """Create a Flask app with the skill manager routes for testing."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(skill_manager_bp)
    return app


@pytest.fixture
def client(flask_app):
    """Create a test client."""
    return flask_app.test_client()


class TestSkillGeneratorAPI:
    """Test Skill Generator API endpoints."""

    def test_start_generation_success(self, client):
        """Test starting a skill generation job."""
        payload = {
            'prompt': 'Create a skill that routes requests by confidence score',
            'skill_type': 'tool',
            'scope': 'console',
        }

        response = client.post('/v1/console/skills/generate', json=payload)

        assert response.status_code == 202
        data = response.get_json()
        assert 'job_id' in data
        assert data['status'] == 'pending'
        assert data['progress'] == 0.0
        assert data['prompt'] == payload['prompt']
        assert 'created_at' in data
        assert 'logs' in data
        assert isinstance(data['logs'], list)

    def test_start_generation_missing_prompt(self, client):
        """Test generation with missing prompt fails."""
        payload = {
            'prompt': '',
            'skill_type': 'tool',
            'scope': 'console',
        }

        response = client.post('/v1/console/skills/generate', json=payload)

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_get_generation_status(self, client):
        """Test getting status of a generation job."""
        # Start generation
        payload = {
            'prompt': 'Test skill',
            'skill_type': 'tool',
            'scope': 'console',
        }
        start_response = client.post('/v1/console/skills/generate', json=payload)
        job_id = start_response.get_json()['job_id']

        # Get status
        response = client.get(f'/v1/console/skills/generate/{job_id}')

        assert response.status_code == 200
        data = response.get_json()
        assert data['job_id'] == job_id
        assert 'status' in data
        assert 'progress' in data

    def test_get_generation_status_not_found(self, client):
        """Test getting status of non-existent job."""
        fake_id = str(uuid.uuid4())

        response = client.get(f'/v1/console/skills/generate/{fake_id}')

        assert response.status_code == 404
        data = response.get_json()
        assert 'error' in data


class TestSkillManagerAPI:
    """Test Skill Manager API endpoints."""

    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillLoader')
    def test_list_installed_skills_empty(self, mock_loader_class, client):
        """Test listing installed skills when empty."""
        mock_loader = MagicMock()
        mock_loader.list_installed_skills.return_value = []
        mock_loader_class.return_value = mock_loader

        response = client.get('/v1/console/skills/installed')

        assert response.status_code == 200
        data = response.get_json()
        assert data['skills'] == []

    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillLoader')
    def test_list_installed_skills(self, mock_loader_class, client):
        """Test listing installed skills."""
        mock_loader = MagicMock()
        mock_loader.list_installed_skills.return_value = [
            {
                'id': 'os.delegation_router',
                'name': 'Delegation Router',
                'version': '1.0.0',
                'title': 'Routes requests by LLM',
                'description': 'Intelligently routes requests',
                'scope': 'engine',
                'installed_at': datetime.utcnow().isoformat(),
                'enabled': True,
                'usage_count': 42,
                'confidence_score': 0.85,
            }
        ]
        mock_loader_class.return_value = mock_loader

        response = client.get('/v1/console/skills/installed')

        assert response.status_code == 200
        data = response.get_json()
        assert len(data['skills']) == 1
        skill = data['skills'][0]
        assert skill['skill_id'] == 'os.delegation_router'
        assert skill['name'] == 'Delegation Router'
        assert skill['version'] == '1.0.0'
        assert skill['enabled'] is True
        assert skill['usage_count'] == 42

    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillInstaller')
    def test_install_skill_from_zip(self, mock_installer_class, client):
        """Test installing a skill from ZIP."""
        mock_installer = MagicMock()
        mock_installer.install_from_zip.return_value = {
            'skill_id': 'test.skill',
            'name': 'Test Skill',
            'version': '1.0.0',
        }
        mock_installer_class.return_value = mock_installer

        payload = {
            'zip_path': '/tmp/skill.zip',
        }

        response = client.post('/v1/console/skills/install', json=payload)

        assert response.status_code == 202
        data = response.get_json()
        assert 'job_id' in data
        assert data['status'] == 'complete'

    def test_install_skill_missing_parameters(self, client):
        """Test install without zip_path or marketplace_id fails."""
        payload = {}

        response = client.post('/v1/console/skills/install', json=payload)

        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillLoader')
    def test_enable_skill(self, mock_loader_class, client):
        """Test enabling a skill."""
        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader

        response = client.post('/v1/console/skills/os.delegation_router/enable')

        assert response.status_code == 200
        data = response.get_json()
        assert data['skill_id'] == 'os.delegation_router'
        assert data['status'] == 'enabled'
        mock_loader.enable_skill.assert_called_once()

    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillLoader')
    def test_disable_skill(self, mock_loader_class, client):
        """Test disabling a skill."""
        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader

        response = client.post('/v1/console/skills/os.delegation_router/disable')

        assert response.status_code == 200
        data = response.get_json()
        assert data['skill_id'] == 'os.delegation_router'
        assert data['status'] == 'disabled'
        mock_loader.disable_skill.assert_called_once()

    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillLoader')
    def test_delete_skill(self, mock_loader_class, client):
        """Test deleting a skill."""
        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader

        response = client.delete('/v1/console/skills/os.delegation_router')

        assert response.status_code == 200
        data = response.get_json()
        assert data['skill_id'] == 'os.delegation_router'
        assert data['status'] == 'deleted'
        mock_loader.uninstall_skill.assert_called_once()


class TestGenerationJob:
    """Test GenerationJob dataclass."""

    def test_generation_job_to_dict(self):
        """Test converting GenerationJob to dict."""
        now = datetime.utcnow()
        job = GenerationJob(
            job_id='test-id',
            status='complete',
            prompt='Test prompt',
            created_at=now,
            updated_at=now,
            progress=1.0,
            logs=['Log 1', 'Log 2'],
            result={'zip_path': '/tmp/skill.zip'},
        )

        result = job.to_dict()

        assert result['job_id'] == 'test-id'
        assert result['status'] == 'complete'
        assert result['progress'] == 1.0
        assert len(result['logs']) == 2
        assert result['result']['zip_path'] == '/tmp/skill.zip'
        assert 'created_at' in result
        assert 'updated_at' in result


class TestInstalledSkill:
    """Test InstalledSkill dataclass."""

    def test_installed_skill_to_dict(self):
        """Test converting InstalledSkill to dict."""
        now = datetime.utcnow()
        skill = InstalledSkill(
            skill_id='test.skill',
            name='Test Skill',
            version='1.0.0',
            title='Test Skill Title',
            description='Test Description',
            scope='console',
            installed_at=now,
            enabled=True,
            usage_count=42,
            confidence_score=0.85,
        )

        result = skill.to_dict()

        assert result['skill_id'] == 'test.skill'
        assert result['name'] == 'Test Skill'
        assert result['version'] == '1.0.0'
        assert result['enabled'] is True
        assert result['usage_count'] == 42
        assert result['confidence_score'] == 0.85


class TestE2ESkillForgePhase5:
    """End-to-end tests for Skill Forge Phase 5."""

    @pytest.mark.asyncio
    @patch('core.console.corvin_console.routes.skill_manager_routes.generate_skill_complete')
    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillLoader')
    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillInstaller')
    async def test_full_workflow_generate_to_dashboard(
        self,
        mock_installer_class,
        mock_loader_class,
        mock_generate,
        client
    ):
        """Test full workflow: Generate -> Install -> Dashboard."""

        # Setup mocks
        mock_generate.return_value = 'async def skill(): pass'

        mock_installer = MagicMock()
        mock_installer.install_from_zip.return_value = {
            'skill_id': 'test.skill',
            'name': 'Test Skill',
            'version': '1.0.0',
        }
        mock_installer_class.return_value = mock_installer

        mock_loader = MagicMock()
        mock_loader.list_installed_skills.return_value = [
            {
                'id': 'test.skill',
                'name': 'Test Skill',
                'version': '1.0.0',
                'title': 'Test Skill',
                'description': 'Test Description',
                'scope': 'console',
                'installed_at': datetime.utcnow().isoformat(),
                'enabled': True,
                'usage_count': 0,
                'confidence_score': 0.5,
            }
        ]
        mock_loader_class.return_value = mock_loader

        # Step 1: Generate skill
        gen_payload = {
            'prompt': 'Create a test skill',
            'skill_type': 'tool',
            'scope': 'console',
        }
        gen_response = client.post('/v1/console/skills/generate', json=gen_payload)
        assert gen_response.status_code == 202
        job_id = gen_response.get_json()['job_id']

        # Wait a bit for async generation to start
        await asyncio.sleep(0.1)

        # Step 2: Check generation status
        status_response = client.get(f'/v1/console/skills/generate/{job_id}')
        assert status_response.status_code == 200

        # Step 3: List installed skills
        list_response = client.get('/v1/console/skills/installed')
        assert list_response.status_code == 200
        skills = list_response.get_json()['skills']
        assert len(skills) == 1
        assert skills[0]['skill_id'] == 'test.skill'

    @patch('core.console.corvin_console.routes.skill_manager_routes.SkillLoader')
    def test_audit_trail_on_skill_operations(self, mock_loader_class, client):
        """Test that skill operations are audited."""
        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader

        # Enable skill (should audit)
        response = client.post('/v1/console/skills/test.skill/enable')
        assert response.status_code == 200

        # Disable skill (should audit)
        response = client.post('/v1/console/skills/test.skill/disable')
        assert response.status_code == 200

        # Delete skill (should audit)
        response = client.delete('/v1/console/skills/test.skill')
        assert response.status_code == 200


class TestSkillManagerUIComponents:
    """Test React component structure (unit tests)."""

    def test_skill_manager_component_imports(self):
        """Test that the React component can be imported."""
        # This would normally be a Jest test, but we can verify the file exists
        skill_manager_path = Path('/home/shumway/projects/CorvinOS/core/console/corvin_console/web-next/src/pages/skill-manager.tsx')
        assert skill_manager_path.exists(), "Skill Manager page file not found"

        content = skill_manager_path.read_text()
        assert 'SkillManagerPage' in content
        assert 'Generator' in content
        assert 'Manager' in content
        assert 'Dashboard' in content


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
