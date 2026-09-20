"""
Mock Skill Package for E2E Testing

Provides realistic skill fixtures with proper structure:
- manifest.json
- src/handler.py
- tests/test_handler.py
- hooks/
"""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any
import json


@dataclass
class SkillManifest:
    """Skill manifest (matches real ADR-0533 schema)."""
    skill_id: str
    version: str
    category: str = "routing"
    handler: str = "src.handler:handle"
    confidence_target: float = 0.75
    dependencies: list = None
    required_checks: list = None
    boot_layer: str = "bundled"

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []
        if self.required_checks is None:
            self.required_checks = ["house_rules", "consent", "data_flow"]


@dataclass
class MockSkillPackage:
    """Mock skill package for testing."""
    skill_id: str
    version: str
    confidence: float
    manifest: SkillManifest
    path: Optional[Path] = None

    @property
    def manifest_dict(self) -> Dict[str, Any]:
        """Get manifest as dict."""
        return asdict(self.manifest)


def create_mock_skill(
    name: str = "test.skill",
    version: str = "1.0.0",
    confidence: float = 0.75,
    category: str = "routing",
) -> MockSkillPackage:
    """
    Create a mock skill package.

    Args:
        name: Skill ID (e.g., "test.skill")
        version: Semantic version (e.g., "1.2.3")
        confidence: Current confidence score (0.0–1.0)
        category: Skill category (routing, learning, security, data)

    Returns:
        MockSkillPackage instance with proper structure.
    """
    manifest = SkillManifest(
        skill_id=name,
        version=version,
        category=category,
        confidence_target=max(confidence + 0.05, 0.80),
    )

    skill = MockSkillPackage(
        skill_id=name,
        version=version,
        confidence=confidence,
        manifest=manifest,
    )

    return skill


def create_mock_skill_with_path(
    name: str = "test.skill",
    version: str = "1.0.0",
    confidence: float = 0.75,
    temp_dir: Path = None,
) -> MockSkillPackage:
    """
    Create a mock skill with files written to disk.

    Args:
        name: Skill ID
        version: Version string
        confidence: Confidence score
        temp_dir: Temporary directory to write files to

    Returns:
        MockSkillPackage with files created.
    """
    skill = create_mock_skill(name, version, confidence)

    if temp_dir is None:
        from tempfile import TemporaryDirectory
        temp_dir = Path(TemporaryDirectory().name)

    skill_dir = temp_dir / f"skill-{version}"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill.path = skill_dir

    # Write manifest
    manifest_file = skill_dir / "manifest.json"
    manifest_file.write_text(json.dumps(asdict(skill.manifest), indent=2))

    # Write source
    src_dir = skill_dir / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "__init__.py").write_text("")
    (src_dir / "handler.py").write_text(
        '''"""Skill handler."""

async def handle(input_data):
    """Process skill input and return result."""
    return {"result": "processed", "input": input_data}
'''
    )

    # Write tests
    tests_dir = skill_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_handler.py").write_text(
        '''"""Tests for skill handler."""
import pytest
from src.handler import handle

@pytest.mark.asyncio
async def test_handle_processes_input():
    """Test that handle processes input correctly."""
    result = await handle({"test": "input"})
    assert result["result"] == "processed"

@pytest.mark.asyncio
async def test_handle_returns_dict():
    """Test that handle returns a dict."""
    result = await handle({})
    assert isinstance(result, dict)
    assert "result" in result
'''
    )

    # Write hooks (optional)
    hooks_dir = skill_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "__init__.py").write_text("")
    (hooks_dir / "pre_execute.py").write_text(
        '''"""Pre-execution hook."""

async def pre_execute(input_data):
    """Pre-execution validation."""
    return True
'''
    )

    return skill


def create_mock_bad_skill(
    name: str = "bad.skill",
    version: str = "1.0.0",
    temp_dir: Path = None,
) -> MockSkillPackage:
    """
    Create a mock skill with missing tests (fails Layer 2 validation).

    Args:
        name: Skill ID
        version: Version string
        temp_dir: Directory to write files to

    Returns:
        MockSkillPackage with no tests directory.
    """
    skill = create_mock_skill(name, version)

    if temp_dir is None:
        from tempfile import TemporaryDirectory
        temp_dir = Path(TemporaryDirectory().name)

    skill_dir = temp_dir / f"skill-{version}"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill.path = skill_dir

    # Write manifest only
    manifest_file = skill_dir / "manifest.json"
    manifest_file.write_text(json.dumps(asdict(skill.manifest), indent=2))

    # Write source (but NO tests)
    src_dir = skill_dir / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "handler.py").write_text("# Handler code\n")

    # DON'T create tests directory — will fail validation

    return skill
