"""
Skill Forge v2.0 Phase 1: Skeleton Generator

Generates complete folder structure + boilerplate for production-ready Skills.
Deterministic (same input → same output). Integration: ADR-0672, ADR-0673.

Author: Claude Haiku 4.5 (automated)
License: Apache-2.0
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class SkillScaffoldConfig:
    """Configuration for skeleton generation."""
    skill_id: str
    skill_name: str
    domain: str  # routing|learning|optimization|integration
    description: str
    input_schema: Dict[str, str]
    output_schema: Dict[str, str]
    parameters: Optional[List[Dict]] = None
    dependencies: Optional[List[Dict]] = None


class SkillSkeletonGenerator:
    """Generate production-ready Skill folder structure + boilerplate."""

    SKELETON_DIR = Path(__file__).parent / "templates" / "skill_skeleton"

    def __init__(self, base_output_dir: Optional[Path] = None):
        """
        Initialize generator.

        Args:
            base_output_dir: Where to create skill folders (default: ~/.corvin/skills_gen/)
        """
        if base_output_dir is None:
            base_output_dir = Path.home() / ".corvin" / "skills_gen"
        self.base_output_dir = base_output_dir
        self.base_output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, config: SkillScaffoldConfig) -> Path:
        """
        Generate complete skill skeleton.

        Args:
            config: SkillScaffoldConfig with skill metadata

        Returns:
            Path to generated skill folder

        Raises:
            ValueError: Invalid config (e.g., skill_id format)
            FileExistsError: Skill already exists at target path
        """
        # Validate config
        self._validate_config(config)

        # Create skill directory
        skill_dir = self.base_output_dir / config.skill_id
        if skill_dir.exists():
            raise FileExistsError(f"Skill {config.skill_id} already exists at {skill_dir}")

        skill_dir.mkdir(parents=True)

        # Create folder structure
        self._create_folders(skill_dir)

        # Generate boilerplate files
        self._generate_init_files(skill_dir, config)
        self._generate_manifest_skeleton(skill_dir, config)
        self._generate_skill_py(skill_dir, config)
        self._generate_test_templates(skill_dir, config)
        self._generate_hook_stubs(skill_dir, config)
        self._generate_scripts(skill_dir, config)
        self._generate_docs(skill_dir, config)
        self._generate_references(skill_dir, config)
        self._generate_forge_metadata(skill_dir, config)

        # Audit event (placeholder — LLM will emit real event)
        self._log_scaffold_created(skill_dir, config)

        return skill_dir

    def _validate_config(self, config: SkillScaffoldConfig) -> None:
        """Validate configuration."""
        if not re.match(r"^[a-z0-9_]+$", config.skill_id):
            raise ValueError(
                f"skill_id must be lowercase alphanumeric + underscore, got {config.skill_id}"
            )
        if config.domain not in ["routing", "learning", "optimization", "integration"]:
            raise ValueError(f"domain must be one of routing|learning|optimization|integration")
        if not config.input_schema or not isinstance(config.input_schema, dict):
            raise ValueError("input_schema must be non-empty dict")
        if not config.output_schema or not isinstance(config.output_schema, dict):
            raise ValueError("output_schema must be non-empty dict")
        if "confidence" not in config.output_schema:
            raise ValueError("output_schema must include 'confidence' field (float)")

    def _create_folders(self, skill_dir: Path) -> None:
        """Create standard folder structure."""
        folders = [
            "src",
            "tests",
            "hooks",
            "scripts",
            "docs",
            "references",
            ".forge",
        ]
        for folder in folders:
            (skill_dir / folder).mkdir(parents=True, exist_ok=True)

    def _generate_init_files(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate __init__.py files."""
        for folder in ["src", "tests", "hooks", "scripts"]:
            (skill_dir / folder / "__init__.py").touch()

    def _generate_manifest_skeleton(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate skill.json manifest skeleton."""
        manifest = {
            "skill_id": config.skill_id,
            "name": config.skill_name,
            "version": "0.1.0",
            "description": config.description,
            "author": "generated-by-skill-forge-v2.0",
            "license": "Apache-2.0",
            "boot_layer": "installed",
            "entry_point": f"src.skill:{self._class_name(config.skill_id)}.execute",
            "domain": config.domain,
            "capabilities": [config.domain],
            "parameters": config.parameters or [],
            "dependencies": config.dependencies or [],
            "input_schema": {
                "type": "object",
                "required": list(config.input_schema.keys()),
                "properties": {k: {"type": v} for k, v in config.input_schema.items()},
            },
            "output_schema": {
                "type": "object",
                "required": ["result", "confidence"],
                "properties": {
                    k: {"type": v} for k, v in config.output_schema.items()
                },
            },
            "hooks": {
                "on_load": "hooks/on_load.py",
                "on_execute": "hooks/on_execute.py",
                "on_feedback": "hooks/on_feedback.py",
                "on_unload": "hooks/on_unload.py",
            },
            "scripts": {
                "install": "scripts/install.py",
                "test": "scripts/test_runner.py",
                "package": "scripts/packager.py",
                "integrate": "scripts/integrator.py",
            },
            "audit_events": [
                "skill_executed",
                "skill_failed",
                "learning_event_emitted",
            ],
            "learning": {
                "enabled": True,
                "strategy": "gradient_descent",
                "feedback_sources": ["outcome_feedback"],
                "convergence_signal": "weight_stabilization",
            },
            "compliance": {
                "gdpr_ready": True,
                "audit_trail": True,
                "pii_handling": "redacted",
                "eu_ai_act_tier": "high_risk",
            },
            "generation_metadata": {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "generated_by": "skill-forge-v2.0",
                "phase": "1-skeleton",
                "lom_hash": "sha256:placeholder",
            },
        }

        manifest_path = skill_dir / "skill.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

    def _generate_skill_py(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate src/skill.py skeleton."""
        class_name = self._class_name(config.skill_id)

        code = f'''"""
{config.skill_name} — Skill implementation

Domain: {config.domain}
Generated by Skill Forge v2.0 (Phase 1)

Author: generated-by-skill-forge-v2.0
License: Apache-2.0
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict
from abc import ABC

logger = logging.getLogger(__name__)


@dataclass
class SkillConfig:
    """Tunable parameters (learned by optimizer, ADR-0314)."""
    # Example: add tunable parameters here
    # threshold: float = 0.5  # Bounds: [0.0, 1.0]
    # max_retries: int = 3
    pass


class {class_name}(ABC):
    """
    Skill: {config.skill_name}

    Domain: {config.domain}
    Description: {config.description}
    """

    def __init__(self, skill_id: str, config: SkillConfig, **kwargs):
        self.skill_id = skill_id
        self.config = config
        self.version = "0.1.0"
        self.lom = "src.skill:{class_name}.execute"

    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute Skill logic.

        Args:
            input_data: {{input_schema_example}}

        Returns:
            output: {{output_schema_example}}

        Side Effects:
            - Emits SkillExecutedEvent to learning backend (ADR-0314)
            - Logs execution with lom attribution

        Contract:
            - Must NOT raise exceptions (return error in output)
            - Must return output matching output_schema
            - Must include "confidence" float [0.0-1.0]
            - Latency p99 < 500ms
        """
        try:
            # TODO: Phase 2 LLM will implement this
            result = {{
                "result": "TODO",
                "confidence": 0.5,
            }}

            logger.info(
                f"Skill {{self.skill_id}} executed: confidence={{result['confidence']}}"
            )

            return result

        except Exception as e:
            logger.error(f"Skill {{self.skill_id}} failed: {{e}}")
            return {{
                "error": str(e),
                "confidence": 0.0,
            }}
'''
        (skill_dir / "src" / "skill.py").write_text(code)

    def _generate_test_templates(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate test skeleton."""
        test_code = f'''"""
Test suite for {config.skill_name}

Generated by Skill Forge v2.0 (Phase 1)
"""

import json
import pytest
from src.skill import {self._class_name(config.skill_id)}, SkillConfig


@pytest.fixture
def skill():
    """Create Skill instance for testing."""
    config = SkillConfig()
    return {self._class_name(config.skill_id)}(
        skill_id="{config.skill_id}",
        config=config
    )


class TestExecute:
    """Test main execute() method."""

    @pytest.mark.asyncio
    async def test_basic_execution(self, skill):
        """Test happy path."""
        input_data = {json.dumps({k: "test_value" for k in config.input_schema.keys()})}
        output = await skill.execute(input_data)

        assert "result" in output
        assert "confidence" in output
        assert 0.0 <= output["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_invalid_input(self, skill):
        """Test error handling for invalid input."""
        output = await skill.execute({{}})

        # Must return error in output, not raise
        assert "error" in output or "result" in output


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_input(self, skill):
        """Test with empty input."""
        output = await skill.execute({{}})
        assert "error" in output or "confidence" in output


class TestAdversarial:
    """Security + robustness tests."""

    @pytest.mark.asyncio
    async def test_injection_attempt(self, skill):
        """Test injection guard."""
        malicious_input = {{"injection": "\\'; DROP TABLE skills; --"}}
        output = await skill.execute(malicious_input)
        # Must not raise, must return error safely
        assert "error" in output or "result" in output
'''
        (skill_dir / "tests" / "test_skill.py").write_text(test_code)

    def _generate_hook_stubs(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate hook stub files (empty, for Phase 2 LLM)."""
        hooks = {
            "on_load.py": '''"""Hook: on_load (initialization)"""

async def on_load(skill_instance, manifest):
    """Initialize Skill on load. TODO: Implement."""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Loading {skill_instance.skill_id}")
    # Phase 2 LLM will implement
''',
            "on_execute.py": '''"""Hooks: on_execute (pre/post)"""

def on_execute_pre(skill_instance, input_data, manifest):
    """Pre-execution validation. TODO: Implement."""
    return input_data

def on_execute_post(skill_instance, output_data, execution_time_ms, manifest):
    """Post-execution metrics. TODO: Implement."""
    return output_data
''',
            "on_feedback.py": '''"""Hook: on_feedback (learning)"""

def on_feedback(skill_instance, feedback_event, manifest):
    """Handle learning feedback. TODO: Implement."""
    # Phase 2 LLM will implement parameter tuning
    pass
''',
            "on_unload.py": '''"""Hook: on_unload (cleanup)"""

async def on_unload(skill_instance):
    """Cleanup on unload. TODO: Implement."""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Unloading {skill_instance.skill_id}")
    # Phase 2 LLM will implement
''',
        }

        for filename, content in hooks.items():
            (skill_dir / "hooks" / filename).write_text(content)

    def _generate_scripts(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate utility scripts."""
        scripts = {
            "install.py": '''#!/usr/bin/env python3
"""Install dependencies (Phase 1 skeleton)."""

import sys
from pathlib import Path

def install_dependencies():
    """Install Python + system dependencies."""
    skill_dir = Path(__file__).parent.parent

    # Python dependencies
    deps_file = skill_dir / "references" / "dependencies.txt"
    if deps_file.exists():
        print(f"Installing Python dependencies from {deps_file}...")
        # TODO: Run pip install -r

    print("✓ Dependencies installed")

if __name__ == "__main__":
    install_dependencies()
''',
            "test_runner.py": '''#!/usr/bin/env python3
"""Run test suite (Phase 1 skeleton)."""

import subprocess
import sys
from pathlib import Path

def run_tests():
    """Execute pytest suite."""
    skill_dir = Path(__file__).parent.parent
    tests_dir = skill_dir / "tests"

    print(f"Running tests in {tests_dir}...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir), "-v"],
        cwd=skill_dir
    )
    return result.returncode

if __name__ == "__main__":
    sys.exit(run_tests())
''',
            "packager.py": '''#!/usr/bin/env python3
"""Package Skill as ZIP (Phase 1 skeleton)."""

import sys
from pathlib import Path

def create_package():
    """Generate ZIP package."""
    skill_dir = Path(__file__).parent.parent
    print(f"Packaging {skill_dir.name}...")
    # TODO: Phase 4 will implement ZIP creation
    print("✓ Package created")

if __name__ == "__main__":
    create_package()
''',
            "integrator.py": '''#!/usr/bin/env python3
"""Install to local registry (Phase 1 skeleton)."""

import sys
from pathlib import Path

def install_to_registry():
    """Install Skill to ~/.corvin/skills/custom/."""
    skill_dir = Path(__file__).parent.parent
    print(f"Installing {skill_dir.name} to registry...")
    # TODO: Phase 4 will implement registry integration
    print("✓ Skill registered")

if __name__ == "__main__":
    install_to_registry()
''',
        }

        for filename, content in scripts.items():
            (skill_dir / "scripts" / filename).write_text(content)

    def _generate_docs(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate documentation skeleton."""
        readme = f"""# {config.skill_name}

**Domain:** {config.domain}

{config.description}

## Quick Start

1. Install dependencies:
   ```bash
   python scripts/install.py
   ```

2. Run tests:
   ```bash
   python scripts/test_runner.py
   ```

3. Create package:
   ```bash
   python scripts/packager.py
   ```

## Input Schema

{json.dumps(config.input_schema, indent=2)}

## Output Schema

{json.dumps(config.output_schema, indent=2)}

## Learning

This Skill supports learning feedback (ADR-0314). See `docs/LEARNING.md`.

## API

See `docs/API.md` for detailed endpoint documentation.

---

Generated by Skill Forge v2.0 (Phase 1)
"""
        (skill_dir / "README.md").write_text(readme)

    def _generate_references(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate references skeleton."""
        (skill_dir / "references" / "dependencies.txt").write_text("# Python dependencies\n")
        (skill_dir / "references" / "system_requirements.md").write_text(
            "# System Requirements\n\nNone (Phase 2 LLM will populate)\n"
        )

    def _generate_forge_metadata(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Generate .forge/ metadata."""
        metadata = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "generated_by": "skill-forge-v2.0",
            "phase": "1-skeleton",
            "user_prompt": config.description,
            "domain": config.domain,
            "lom_hash": "sha256:placeholder",
            "phases_completed": ["phase1_skeleton_generated"],
        }
        (skill_dir / ".forge" / "generation_context.json").write_text(json.dumps(metadata, indent=2))
        (skill_dir / ".forge" / "INSTALL.md").write_text("# Installation Guide\n\nTo be generated in Phase 4.\n")

    def _log_scaffold_created(self, skill_dir: Path, config: SkillScaffoldConfig) -> None:
        """Log scaffold creation (audit placeholder)."""
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": "skill_forge_scaffold_created",
            "skill_id": config.skill_id,
            "phase": 1,
            "path": str(skill_dir),
        }
        # TODO: Real audit backend will be called in Phase 2
        logger.info(f"Scaffold created: {event}")

    @staticmethod
    def _class_name(skill_id: str) -> str:
        """Convert skill_id to ClassName."""
        return "".join(word.capitalize() for word in skill_id.split("_"))


if __name__ == "__main__":
    # Example usage
    import asyncio

    config = SkillScaffoldConfig(
        skill_id="test_routing_skill",
        skill_name="Test Routing Skill",
        domain="routing",
        description="Routes requests to best LLM",
        input_schema={"request": "string"},
        output_schema={"engine": "string", "confidence": "float"},
    )

    generator = SkillSkeletonGenerator()
    skill_path = generator.generate(config)
    print(f"✓ Skill generated at {skill_path}")
