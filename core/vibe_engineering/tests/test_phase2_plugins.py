"""Phase 2: Plugin Ecosystem Tests (Coder Persona — Error Handling + Edge Cases)."""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from ..plugin_manager import PluginRegistry, PluginManifest, PluginLoadError, PluginValidationError


@pytest.fixture
def plugin_registry():
    """Create test registry."""
    return PluginRegistry()


@pytest.fixture
def test_plugin_dir():
    """Create temporary test plugin directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create minimal plugin.json
        manifest = {
            "plugin": {
                "id": "test_plugin",
                "version": "1.0.0",
                "author": "test_user",
                "description": "Test plugin",
                "skills": [
                    {
                        "id": "test_skill",
                        "description": "Test skill",
                        "entry_point": "skills:test_skill_func"
                    }
                ]
            }
        }

        with open(tmpdir / "plugin.json", "w") as f:
            json.dump(manifest, f)

        # Create skills module
        skills_code = '''
async def test_skill_func(context):
    return {"result": "test"}
'''
        with open(tmpdir / "skills.py", "w") as f:
            f.write(skills_code)

        yield tmpdir


@pytest.mark.asyncio
async def test_plugin_load_success(plugin_registry, test_plugin_dir):
    """Test: Plugin loads successfully."""
    plugin = await plugin_registry.load_plugin(test_plugin_dir)

    assert plugin is not None
    assert plugin.id == "test_plugin"
    assert plugin.version == "1.0.0"
    assert len(plugin.skills) > 0
    assert "test_skill" in plugin_registry.loaded_skills


@pytest.mark.asyncio
async def test_plugin_load_missing_manifest(plugin_registry, tmp_path):
    """Test: Plugin load fails gracefully if manifest missing."""
    empty_dir = tmp_path / "empty_plugin"
    empty_dir.mkdir()

    plugin = await plugin_registry.load_plugin(empty_dir)

    assert plugin is None
    assert empty_dir.name in plugin_registry.failed_plugins
    assert "No plugin.json" in plugin_registry.failed_plugins[empty_dir.name]


@pytest.mark.asyncio
async def test_plugin_load_invalid_manifest(plugin_registry, tmp_path):
    """Test: Plugin load fails on invalid manifest JSON."""
    bad_dir = tmp_path / "bad_plugin"
    bad_dir.mkdir()

    # Write invalid JSON
    (bad_dir / "plugin.json").write_text("{ invalid json")

    plugin = await plugin_registry.load_plugin(bad_dir)

    assert plugin is None
    assert bad_dir.name in plugin_registry.failed_plugins


@pytest.mark.asyncio
async def test_plugin_skill_load_error_isolation(plugin_registry, tmp_path):
    """Test: Skill load error doesn't prevent plugin load (error isolation)."""
    plugin_dir = tmp_path / "mixed_plugin"
    plugin_dir.mkdir()

    manifest = {
        "plugin": {
            "id": "mixed",
            "version": "1.0.0",
            "author": "test",
            "skills": [
                {
                    "id": "good_skill",
                    "entry_point": "skills:good_skill"
                },
                {
                    "id": "bad_skill",
                    "entry_point": "skills:nonexistent_func"  # This will fail
                },
                {
                    "id": "good_skill2",
                    "entry_point": "skills:good_skill2"
                }
            ]
        }
    }

    with open(plugin_dir / "plugin.json", "w") as f:
        json.dump(manifest, f)

    # Create skills module
    (plugin_dir / "skills.py").write_text('''
async def good_skill(context):
    return {"ok": True}

async def good_skill2(context):
    return {"ok": True}
''')

    plugin = await plugin_registry.load_plugin(plugin_dir)

    # Plugin still loads despite bad_skill error
    assert plugin is not None
    assert "good_skill" in plugin_registry.loaded_skills
    assert "good_skill2" in plugin_registry.loaded_skills
    assert len([s for s in plugin.skills if not s.get("loaded")]) == 1  # 1 failed


@pytest.mark.asyncio
async def test_plugin_unload(plugin_registry, test_plugin_dir):
    """Test: Plugin unload removes from registry."""
    await plugin_registry.load_plugin(test_plugin_dir)
    assert "test_plugin" in plugin_registry.registry

    await plugin_registry.unload_plugin("test_plugin")
    assert "test_plugin" not in plugin_registry.registry


@pytest.mark.asyncio
async def test_list_plugins(plugin_registry, test_plugin_dir):
    """Test: List loaded and failed plugins."""
    await plugin_registry.load_plugin(test_plugin_dir)

    loaded = plugin_registry.list_plugins(loaded_only=True)
    all_plugins = plugin_registry.list_plugins(loaded_only=False)

    assert "test_plugin" in loaded
    assert len(all_plugins) >= len(loaded)


@pytest.mark.asyncio
async def test_plugin_dependencies_check_placeholder(plugin_registry):
    """Test: Plugin dependency checking (placeholder for v1.1)."""
    manifest = PluginManifest(
        id="dep_test",
        version="1.0.0",
        author="test",
        # `description` became required on PluginManifest; this call was never
        # updated, so the test raised TypeError before reaching its assertion.
        description="dependency-resolution placeholder",
        dependencies=[{"id": "nonexistent", "min_version": "1.0"}]
    )

    # v1.1: Add full dependency resolution
    # For now, this is tested in load_plugin integration
    assert manifest.dependencies[0]["id"] == "nonexistent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
