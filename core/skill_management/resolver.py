"""Dependency resolver for skills — transitive resolution + circular detection."""

import json
from pathlib import Path
from typing import List, Dict, Set, Tuple
from dataclasses import dataclass

from core.skill_management.tenant_validator import validate_tenant_id  # TENANT-002


@dataclass
class SkillInfo:
    """Skill information from metadata."""
    id: str
    version: str
    scope: str
    dependencies: List[Dict]

    @classmethod
    def from_manifest(cls, manifest: dict):
        return cls(
            id=manifest["id"],
            version=manifest["version"],
            scope=manifest["scope"],
            dependencies=manifest.get("dependencies", [])
        )


@dataclass
class ResolutionResult:
    root_skill: str
    resolved_skills: List[SkillInfo]
    missing_skills: List[str]
    error: str = None


class SkillDependencyResolver:
    """Resolve skill dependencies transitively."""

    def __init__(self, tenant_id: str = "_default"):
        validate_tenant_id(tenant_id)  # TENANT-002
        self.tenant_id = tenant_id
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id
        self._skill_cache = {}  # Cache loaded manifests

    def resolve(self, skill_id: str, scope: str = "_shared") -> ResolutionResult:
        """Resolve all transitive dependencies for a skill."""
        visited = set()
        resolved = []
        missing = []
        stack = [(skill_id, scope)]

        while stack:
            sid, sscope = stack.pop(0)
            full_id = f"{sscope}/{sid}"

            if full_id in visited:
                continue

            visited.add(full_id)

            # Load skill manifest
            skill = self._load_skill_manifest(sid, sscope)
            if not skill:
                missing.append(full_id)
                continue

            resolved.append(skill)

            # Add dependencies to stack
            for dep in skill.dependencies:
                dep_full_id = f"{dep['scope']}/{dep['id']}"
                if dep_full_id not in visited:
                    stack.append((dep["id"], dep["scope"]))

        return ResolutionResult(
            root_skill=skill_id,
            resolved_skills=resolved,
            missing_skills=missing,
            error=f"Missing skills: {missing}" if missing else None
        )

    def resolve_with_versions(self, skill_id: str, scope: str = "_shared") -> Dict[str, str]:
        """Resolve dependencies and return {skill_id: version} mapping."""
        result = self.resolve(skill_id, scope)
        return {skill.id: skill.version for skill in result.resolved_skills}

    def check_circular_dependencies(self) -> List[List[str]]:
        """Detect all circular dependency chains in tenant."""
        graph = self._build_dependency_graph()
        cycles = []

        def dfs(node, visited, rec_stack, path):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, visited, rec_stack, path)
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    if cycle not in cycles:
                        cycles.append(cycle)

            path.pop()
            rec_stack.remove(node)

        visited = set()
        for skill_id in graph:
            if skill_id not in visited:
                dfs(skill_id, visited, set(), [])

        return cycles

    def check_circular_with_scope(self, scope: str = "_shared") -> List[List[str]]:
        """Detect circular deps within a specific scope."""
        graph = {}

        # Build graph for this scope only
        skills_dir = self.base_path / scope / "skills"
        if not skills_dir.exists():
            return []

        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue

            skill = self._load_skill_manifest(skill_dir.name, scope)
            if skill:
                # Only include deps within same scope
                same_scope_deps = [
                    dep["id"] for dep in skill.dependencies
                    if dep.get("scope") == scope
                ]
                graph[skill.id] = same_scope_deps

        # DFS for cycles
        cycles = []

        def dfs(node, visited, rec_stack, path):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, visited, rec_stack, path)
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    if cycle not in cycles:
                        cycles.append(cycle)

            path.pop()
            rec_stack.remove(node)

        visited = set()
        for skill_id in graph:
            if skill_id not in visited:
                dfs(skill_id, visited, set(), [])

        return cycles

    def build_dependency_graph_json(self, scope: str = "_shared") -> dict:
        """Export dependency graph as JSON (for UI visualization)."""
        graph = {}
        nodes = []
        links = []

        skills_dir = self.base_path / scope / "skills"
        if not skills_dir.exists():
            return {"nodes": [], "links": []}

        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                continue

            skill = self._load_skill_manifest(skill_dir.name, scope)
            if skill:
                nodes.append({
                    "id": skill.id,
                    "label": skill.id,
                    "version": skill.version,
                    "scope": scope
                })

                for dep in skill.dependencies:
                    links.append({
                        "source": skill.id,
                        "target": dep["id"],
                        "label": f"≥{dep.get('min_version', '0.0.0')}"
                    })

        return {
            "nodes": nodes,
            "links": links,
            "metadata": {
                "scope": scope,
                "tenant_id": self.tenant_id,
                "total_skills": len(nodes),
                "total_dependencies": len(links)
            }
        }

    def _load_skill_manifest(self, skill_id: str, scope: str) -> SkillInfo:
        """Load skill metadata from disk."""
        # Check cache
        cache_key = f"{scope}/{skill_id}"
        if cache_key in self._skill_cache:
            return self._skill_cache[cache_key]

        # Load from disk
        meta_path = self.base_path / scope / "skills" / skill_id / "meta.json"
        if not meta_path.exists():
            return None

        try:
            with open(meta_path) as f:
                manifest = json.load(f)
            skill = SkillInfo.from_manifest(manifest)
            self._skill_cache[cache_key] = skill
            return skill
        except (json.JSONDecodeError, KeyError):
            return None

    def _build_dependency_graph(self) -> Dict[str, List[str]]:
        """Build full dependency graph (all scopes)."""
        graph = {}

        for scope in ["_platform", "_shared", "_local"]:
            skills_dir = self.base_path / scope / "skills"
            if not skills_dir.exists():
                continue

            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir() or skill_dir.name.startswith('.'):
                    continue

                skill = self._load_skill_manifest(skill_dir.name, scope)
                if skill:
                    full_id = f"{scope}/{skill.id}"
                    dep_ids = [f"{dep['scope']}/{dep['id']}" for dep in skill.dependencies]
                    graph[full_id] = dep_ids

        return graph

    def clear_cache(self):
        """Clear the manifest cache."""
        self._skill_cache.clear()


def resolve_dependencies(skill_id: str, tenant_id: str = "_default", scope: str = "_shared") -> List[SkillInfo]:
    """Public API: Resolve all dependencies for a skill."""
    resolver = SkillDependencyResolver(tenant_id)
    result = resolver.resolve(skill_id, scope)

    if result.error:
        raise ValueError(result.error)

    return result.resolved_skills
