"""Phase 1.3 unit tests: Dependency Resolver."""

import pytest
import json
from pathlib import Path
from datetime import datetime

from core.skill_management.resolver import SkillDependencyResolver, resolve_dependencies


@pytest.fixture
def temp_tenant_with_skills(tmp_path, monkeypatch):
    """Create tenant with test skills."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tenant_path = tmp_path / ".corvin" / "tenants" / "_default"

    for scope in ["_platform", "_shared", "_local"]:
        (tenant_path / scope / "skills").mkdir(parents=True)

    # Create test skills
    skills = {
        "skill-a": {"dependencies": [{"id": "skill-b", "scope": "_shared"}]},
        "skill-b": {"dependencies": [{"id": "skill-c", "scope": "_shared"}]},
        "skill-c": {"dependencies": []},
    }

    for skill_id, deps in skills.items():
        skill_dir = tenant_path / "_shared" / "skills" / skill_id
        skill_dir.mkdir(parents=True)
        metadata = {
            "id": skill_id,
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": deps.get("dependencies", [])
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(metadata, f)

    return tenant_path


class TestDependencyResolver:
    def test_resolve_no_deps(self, temp_tenant_with_skills):
        """Resolve skill with no dependencies."""
        resolver = SkillDependencyResolver()
        result = resolver.resolve("skill-c", "_shared")

        assert result.root_skill == "skill-c"
        assert len(result.resolved_skills) == 1
        assert result.resolved_skills[0].id == "skill-c"
        assert not result.missing_skills

    def test_resolve_single_dep(self, temp_tenant_with_skills):
        """Resolve skill with one dependency."""
        resolver = SkillDependencyResolver()
        result = resolver.resolve("skill-b", "_shared")

        assert result.root_skill == "skill-b"
        assert len(result.resolved_skills) == 2
        assert {s.id for s in result.resolved_skills} == {"skill-b", "skill-c"}

    def test_resolve_transitive_deps(self, temp_tenant_with_skills):
        """Resolve transitive dependency chain A->B->C."""
        resolver = SkillDependencyResolver()
        result = resolver.resolve("skill-a", "_shared")

        assert len(result.resolved_skills) == 3
        assert {s.id for s in result.resolved_skills} == {"skill-a", "skill-b", "skill-c"}

    def test_resolve_missing_dep(self, temp_tenant_with_skills):
        """Resolve skill with missing dependency."""
        # Create skill-a that depends on nonexistent skill-x
        skill_dir = Path.home() / ".corvin" / "tenants" / "_default" / "_shared" / "skills" / "skill-x"
        skill_dir.mkdir(parents=True)
        metadata = {
            "id": "skill-x",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": [{"id": "nonexistent", "scope": "_shared"}]
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(metadata, f)

        resolver = SkillDependencyResolver()
        result = resolver.resolve("skill-x", "_shared")

        assert result.error is not None
        assert "nonexistent" in result.missing_skills

    def test_resolve_with_versions(self, temp_tenant_with_skills):
        """Resolve and get version mapping."""
        resolver = SkillDependencyResolver()
        versions = resolver.resolve_with_versions("skill-a", "_shared")

        assert versions["skill-a"] == "1.0.0"
        assert versions["skill-b"] == "1.0.0"
        assert versions["skill-c"] == "1.0.0"

    def test_detect_simple_cycle(self, tmp_path, monkeypatch):
        """Detect simple circular dependency A->B->A."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
        (tenant_path / "_shared" / "skills").mkdir(parents=True)

        # A -> B
        skill_a_dir = tenant_path / "_shared" / "skills" / "skill-a"
        skill_a_dir.mkdir(parents=True)
        meta_a = {
            "id": "skill-a",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": [{"id": "skill-b", "scope": "_shared"}]
        }
        with open(skill_a_dir / "meta.json", "w") as f:
            json.dump(meta_a, f)

        # B -> A (cycle)
        skill_b_dir = tenant_path / "_shared" / "skills" / "skill-b"
        skill_b_dir.mkdir(parents=True)
        meta_b = {
            "id": "skill-b",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": [{"id": "skill-a", "scope": "_shared"}]
        }
        with open(skill_b_dir / "meta.json", "w") as f:
            json.dump(meta_b, f)

        resolver = SkillDependencyResolver()
        cycles = resolver.check_circular_with_scope("_shared")

        assert len(cycles) > 0, "Should detect cycle"
        assert any("skill-a" in cycle and "skill-b" in cycle for cycle in cycles)

    def test_detect_complex_cycle(self, tmp_path, monkeypatch):
        """Detect complex circular dependency A->B->C->A."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
        (tenant_path / "_shared" / "skills").mkdir(parents=True)

        skills_data = {
            "skill-a": [{"id": "skill-b", "scope": "_shared"}],
            "skill-b": [{"id": "skill-c", "scope": "_shared"}],
            "skill-c": [{"id": "skill-a", "scope": "_shared"}],
        }

        for skill_id, deps in skills_data.items():
            skill_dir = tenant_path / "_shared" / "skills" / skill_id
            skill_dir.mkdir(parents=True)
            metadata = {
                "id": skill_id,
                "version": "1.0.0",
                "scope": "_shared",
                "created": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat(),
                "dependencies": deps
            }
            with open(skill_dir / "meta.json", "w") as f:
                json.dump(metadata, f)

        resolver = SkillDependencyResolver()
        cycles = resolver.check_circular_with_scope("_shared")

        assert len(cycles) > 0

    def test_detect_no_cycle(self, temp_tenant_with_skills):
        """Acyclic dependencies not flagged as cycles."""
        resolver = SkillDependencyResolver()
        cycles = resolver.check_circular_with_scope("_shared")

        assert len(cycles) == 0

    def test_build_dependency_graph_json(self, temp_tenant_with_skills):
        """Build dependency graph JSON for visualization."""
        resolver = SkillDependencyResolver()
        graph = resolver.build_dependency_graph_json("_shared")

        assert "nodes" in graph
        assert "links" in graph
        assert "metadata" in graph
        assert len(graph["nodes"]) == 3
        assert len(graph["links"]) == 2

    def test_cache_manifests(self, temp_tenant_with_skills):
        """Resolver caches loaded manifests."""
        resolver = SkillDependencyResolver()

        # Load skill-c multiple times
        resolver._load_skill_manifest("skill-c", "_shared")
        resolver._load_skill_manifest("skill-c", "_shared")

        # Cache should have entry
        assert f"_shared/skill-c" in resolver._skill_cache

        # Clear cache
        resolver.clear_cache()
        assert len(resolver._skill_cache) == 0

    def test_public_api_resolve_dependencies(self, temp_tenant_with_skills):
        """Test public API function."""
        deps = resolve_dependencies("skill-a")

        assert len(deps) == 3
        assert {s.id for s in deps} == {"skill-a", "skill-b", "skill-c"}

    def test_diamond_dependencies(self, tmp_path, monkeypatch):
        """Handle diamond dependency pattern: A->[B,C], B->D, C->D."""
        monkeypatch.setenv("HOME", str(tmp_path))
        tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
        (tenant_path / "_shared" / "skills").mkdir(parents=True)

        skills_data = {
            "skill-a": [{"id": "skill-b", "scope": "_shared"}, {"id": "skill-c", "scope": "_shared"}],
            "skill-b": [{"id": "skill-d", "scope": "_shared"}],
            "skill-c": [{"id": "skill-d", "scope": "_shared"}],
            "skill-d": [],
        }

        for skill_id, deps in skills_data.items():
            skill_dir = tenant_path / "_shared" / "skills" / skill_id
            skill_dir.mkdir(parents=True)
            metadata = {
                "id": skill_id,
                "version": "1.0.0",
                "scope": "_shared",
                "created": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat(),
                "dependencies": deps
            }
            with open(skill_dir / "meta.json", "w") as f:
                json.dump(metadata, f)

        resolver = SkillDependencyResolver()
        result = resolver.resolve("skill-a", "_shared")

        # Should resolve to 4 skills (no duplicates)
        assert len(result.resolved_skills) == 4
        assert {s.id for s in result.resolved_skills} == {"skill-a", "skill-b", "skill-c", "skill-d"}
