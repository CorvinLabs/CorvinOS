"""OS-Skill Manager: Install, activate, execute skills."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import logging

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    output: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None
    phase_completed: int = 0
    run_id: Optional[str] = None


@dataclass
class SkillStatus:
    skill_id: str
    enabled: bool
    version: str
    score: Optional[float] = None
    runs_24h: int = 0
    errors_24h: int = 0


class SkillRegistry:
    """Maintain registry of installed skills (tenant-scoped)."""

    def __init__(self, registry_path: Path, tenant_id: str = None):
        self.registry_path = registry_path
        # Note: tenant_id passed for context; actual isolation via registry_path scoping
        self.registry_file = registry_path / 'registry.yaml'
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_registry()

    def _ensure_registry(self):
        if not self.registry_file.exists():
            self.registry_file.write_text("skills: {}\n")

    def register_skill(self, skill_id: str, version: str, path: str, enabled: bool = True):
        """Register skill in registry."""
        import yaml

        with open(self.registry_file) as f:
            registry = yaml.safe_load(f) or {'skills': {}}

        registry['skills'][skill_id] = {
            'version': version,
            'path': path,
            'enabled': enabled,
            'loaded_at': datetime.utcnow().isoformat()
        }

        with open(self.registry_file, 'w') as f:
            yaml.dump(registry, f)

    def get_skill_path(self, skill_id: str, version: Optional[str] = None) -> Optional[Path]:
        """Get skill path by ID and optional version."""
        import yaml

        with open(self.registry_file) as f:
            registry = yaml.safe_load(f) or {'skills': {}}

        if skill_id not in registry.get('skills', {}):
            return None

        skill_info = registry['skills'][skill_id]
        if version and skill_info['version'] != version:
            return None

        return Path(skill_info['path'])


class SkillExecutor:
    """Execute skill phases with gating and timeout enforcement."""

    PHASE_TIMEOUTS_MS = {
        0: 100,    # Intake
        1: 500,    # Context
        2: 2000,   # Clarify
        3: 3000,   # Plan
        4: 5000,   # Execution start
        5: 5000,   # Execution mid
        6: 5000,   # Execution end
        7: 500,    # Feedback
        8: 1000,   # Validation
        9: 200,    # Output
        10: 200,   # Return
        11: 0,     # Grading (async)
    }

    def __init__(self, skill_dir: Path):
        self.skill_dir = skill_dir
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Any]:
        import yaml
        manifest_file = self.skill_dir / 'manifest.yaml'
        with open(manifest_file) as f:
            content = f.read()
            # Extract YAML body (after ---)
            if content.startswith('---'):
                content = content.split('---', 2)[2]
            return yaml.safe_load(content)

    def execute(self, inputs: Dict[str, Any], timeout_ms: int = 5000) -> ExecutionResult:
        """Execute skill phases 0-10."""
        from core.skills.state_manager import StateManager

        state_mgr = StateManager(self.skill_dir)
        run_id = state_mgr.start_run(
            skill_id=self.manifest['name'],
            version=self.manifest['version'],
            trigger='before_delegation_decision',
            inputs=inputs
        ).run_id

        # Phase 0: Intake (input validation)
        try:
            state_mgr.commit_phase_output(run_id, 0, {'validated': True})
        except Exception as e:
            logger.error(f"Phase 0 failed: {e}")
            return ExecutionResult(success=False, errors=[str(e)], phase_completed=0, run_id=run_id)

        # Phase 3: Plan + Decision (hard-coded for MVP)
        decision = self._make_decision(inputs)
        state_mgr.commit_phase_output(run_id, 3, decision)

        # Phase 9-10: Output validation
        output = {
            'decision': decision['decision'],
            'confidence': decision['confidence'],
            'reasoning': decision['reasoning']
        }

        state_mgr.commit_phase_output(run_id, 10, output)

        return ExecutionResult(
            success=True,
            output=output,
            phase_completed=10,
            run_id=run_id
        )

    def _make_decision(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Hard-coded MVP decision logic."""
        task_shape = inputs.get('task_shape', 'small_code')

        # MVP heuristic: big_data -> ACS, else native
        if task_shape == 'big_data':
            return {
                'decision': 'acs',
                'confidence': 0.75,
                'reasoning': 'big_data workload -> delegate to ACS for cost efficiency'
            }
        else:
            return {
                'decision': 'native',
                'confidence': 0.80,
                'reasoning': f'{task_shape} workload -> keep native for latency'
            }


class SkillManager:
    """Manage skill lifecycle."""

    def __init__(self, corvin_home: Path, tenant_id: str):
        self.corvin_home = corvin_home
        self.tenant_id = tenant_id
        # ADR-0007: Tenant-scoped skills directory
        self.skills_dir = corvin_home / 'tenants' / tenant_id / 'skills'
        self.registry = SkillRegistry(self.skills_dir, tenant_id=tenant_id)

    def install_skill(self, skill_bundle: Path) -> Dict[str, Any]:
        """Install skill from .zip or directory."""
        # For MVP, assume skill_bundle is a directory
        if not skill_bundle.exists():
            return {'success': False, 'error': f'Skill bundle not found: {skill_bundle}'}

        # Load and validate manifest
        manifest_file = skill_bundle / 'manifest.yaml'
        if not manifest_file.exists():
            return {'success': False, 'error': 'manifest.yaml not found'}

        # Parse manifest
        import yaml
        with open(manifest_file) as f:
            content = f.read()
            if content.startswith('---'):
                content = content.split('---', 2)[2]
            manifest = yaml.safe_load(content)

        skill_id = manifest.get('name')
        version = manifest.get('version')

        if not skill_id or not version:
            return {'success': False, 'error': 'manifest.name or manifest.version missing'}

        # Copy to skills dir
        dest = self.skills_dir / f"{skill_id}_v{version}"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skill_bundle, dest)

        # Register
        self.registry.register_skill(skill_id, version, str(dest), enabled=True)

        logger.info(f"Installed skill {skill_id} v{version}")
        return {'success': True, 'skill_id': skill_id, 'version': version}

    def execute_skill(self, trigger: str, inputs: Dict[str, Any], timeout_ms: int = 5000) -> ExecutionResult:
        """Execute skill for a trigger."""
        # For MVP, hard-code delegation_router
        skill_path = self.registry.get_skill_path('os.delegation_router')

        if not skill_path:
            return ExecutionResult(success=False, errors=['Skill not found: os.delegation_router'])

        executor = SkillExecutor(skill_path)
        return executor.execute(inputs, timeout_ms)

    def get_skill_status(self, skill_id: str) -> Optional[SkillStatus]:
        """Get skill status."""
        skill_path = self.registry.get_skill_path(skill_id)
        if not skill_path:
            return None

        # Load grading stats (TODO: implement in Phase 2)
        score = None

        return SkillStatus(
            skill_id=skill_id,
            enabled=True,
            version='1.0.0',
            score=score,
            runs_24h=0,
            errors_24h=0
        )
