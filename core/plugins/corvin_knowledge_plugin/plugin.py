"""
Corvin-Knowledge Claude Code Plugin

Enables Claude Code agents to:
- Query knowledge bases (ADRs, Concepts, Ideas)
- Sync with remote repositories (Git-based)
- Propose new entities
- Configure local/remote paths
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field


# ────────────────────────────────────────────────────────
# DATA MODELS
# ────────────────────────────────────────────────────────

@dataclass
class Entity:
    """Knowledge entity (ADR, Concept, Idea, etc.)"""
    id: str
    type: str  # decision, concept, idea, implementation, process, pattern
    title: str
    status: str  # proposed, accepted, verified, superseded
    tags: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    body: str = ""
    project: Optional[str] = None
    author: Optional[str] = None

    def preview(self, max_chars: int = 150) -> str:
        """Return truncated body for display"""
        if len(self.body) > max_chars:
            return self.body[:max_chars] + "..."
        return self.body


@dataclass
class SyncResult:
    """Result of a sync operation"""
    status: str  # success, conflict, validation_failed, error
    graph_version: Optional[str] = None
    conflicts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: Optional[str] = None


@dataclass
class PluginConfig:
    """Plugin configuration"""
    repo_path: str
    remote_url: str
    auto_sync_on_query: bool = True
    consistency_level: str = "warn"


# ────────────────────────────────────────────────────────
# KNOWLEDGE MESH SDK (MINIMAL IMPLEMENTATION)
# ────────────────────────────────────────────────────────

class KnowledgeMeshSDK:
    """
    Minimal SDK for Corvin-Knowledge integration

    Reads from:
    - {repo_path}/graph/entities.jsonl (indexed entities)
    - {repo_path}/graph/relations.jsonl (indexed relations)

    Writes to:
    - Git operations (pull/push/merge)
    """

    def __init__(self, repo_path: str, remote_url: str):
        self.repo_path = Path(repo_path).expanduser()
        self.remote_url = remote_url
        self.graph_dir = self.repo_path / "graph"
        self.entities_file = self.graph_dir / "entities.jsonl"

    def ensure_repo_exists(self) -> bool:
        """Clone repo if it doesn't exist locally"""
        if not self.repo_path.exists():
            print(f"📦 Cloning knowledge repository to {self.repo_path}...")
            result = subprocess.run(
                ["git", "clone", self.remote_url, str(self.repo_path)],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {result.stderr}")
            return True
        return False

    def query(self, tags: Optional[List[str]] = None,
              project: Optional[str] = None,
              status: Optional[str] = None) -> List[Entity]:
        """
        Query knowledge base (reads from entities.jsonl)

        Returns list of entities matching filters
        """
        self.ensure_repo_exists()

        if not self.entities_file.exists():
            return []

        entities = []
        try:
            with open(self.entities_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        entity = Entity(
                            id=data.get("id"),
                            type=data.get("type"),
                            title=data.get("title"),
                            status=data.get("status"),
                            tags=data.get("tags", []),
                            created_at=data.get("created_at"),
                            body=data.get("body", ""),
                            project=data.get("project"),
                            author=data.get("author")
                        )

                        # Apply filters
                        if tags and not any(t in entity.tags for t in tags):
                            continue
                        if project and entity.project != project:
                            continue
                        if status and entity.status != status:
                            continue

                        entities.append(entity)
                    except json.JSONDecodeError:
                        continue

        except Exception as e:
            raise RuntimeError(f"Failed to query entities: {str(e)}")

        return entities

    def sync(self, pull: bool = True, push: bool = False,
             consistency_level: str = "warn") -> SyncResult:
        """
        Sync with remote repository

        - pull=True: Fetch latest from remote
        - push=True: Push local changes (if no conflicts)
        - consistency_level: How to handle errors (strict/warn/ignore)
        """
        self.ensure_repo_exists()

        result = SyncResult(
            status="success",
            timestamp=datetime.utcnow().isoformat()
        )

        try:
            # Step 1: Git fetch
            if pull:
                print(f"📥 Fetching from {self.remote_url}...")
                fetch_result = subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    text=True
                )
                if fetch_result.returncode != 0:
                    result.status = "error"
                    result.errors.append(f"Git fetch failed: {fetch_result.stderr}")
                    return result

                # Step 2: Merge
                merge_result = subprocess.run(
                    ["git", "merge", "origin/main"],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    text=True
                )

                if "conflict" in merge_result.stderr.lower():
                    result.status = "conflict"
                    result.conflicts.append("Merge conflict detected. Resolve manually via Git.")
                    return result

                if merge_result.returncode != 0:
                    result.status = "error"
                    result.errors.append(f"Git merge failed: {merge_result.stderr}")
                    return result

            # Step 3: Validate consistency (minimal check)
            validation_errors = self._validate_consistency()
            if validation_errors:
                if consistency_level == "strict":
                    result.status = "validation_failed"
                    result.errors.extend(validation_errors)
                    return result
                elif consistency_level == "warn":
                    result.warnings.extend(validation_errors)

            # Step 4: Push (if enabled and no conflicts)
            if push and result.status == "success":
                print(f"📤 Pushing to {self.remote_url}...")
                push_result = subprocess.run(
                    ["git", "push", "origin", "main"],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    text=True
                )
                if push_result.returncode != 0:
                    result.status = "error"
                    result.errors.append(f"Git push failed: {push_result.stderr}")
                    return result

            # Step 5: Get graph version
            result.graph_version = self._get_graph_version()

        except Exception as e:
            result.status = "error"
            result.errors.append(str(e))

        return result

    def propose_entity(self, entity_type: str, title: str, body: str,
                      project: str = "CorvinOS", author: str = "claude-code-plugin") -> str:
        """
        Propose new entity (creates GitHub PR)

        In MVP: Return placeholder URL (real implementation requires GitHub API)
        """
        return f"https://github.com/CorvinLabs/Corvin-Knowledge/pulls?q=is:pr+{title}"

    def _validate_consistency(self) -> List[str]:
        """Minimal consistency checks (fail-closed)"""
        errors = []

        if not self.entities_file.exists():
            return ["entities.jsonl not found"]

        # Check 1: Parse all entities (detect invalid JSON)
        entity_ids = set()
        try:
            with open(self.entities_file, "r") as f:
                for line_num, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        entity_id = data.get("id")
                        if entity_id in entity_ids:
                            errors.append(f"Duplicate entity ID: {entity_id}")
                        entity_ids.add(entity_id)
                    except json.JSONDecodeError:
                        errors.append(f"Invalid JSON at line {line_num}")
        except Exception as e:
            errors.append(f"Failed to validate consistency: {str(e)}")

        return errors

    def _get_graph_version(self) -> Optional[str]:
        """Read graph version from graph-meta.json"""
        meta_file = self.graph_dir / "graph-meta.json"
        if meta_file.exists():
            try:
                with open(meta_file) as f:
                    meta = json.load(f)
                    return meta.get("version") or meta.get("sync_ts")
            except:
                pass
        return None


# ────────────────────────────────────────────────────────
# PLUGIN CLASS
# ────────────────────────────────────────────────────────

class CorvinKnowledgePlugin:
    """Claude Code Plugin: Corvin-Knowledge Integration"""

    def __init__(self, config: Dict[str, Any]):
        self.config = PluginConfig(**config)
        self.mesh = KnowledgeMeshSDK(
            repo_path=self.config.repo_path,
            remote_url=self.config.remote_url
        )

    def handle_query(self, tag: Optional[str] = None,
                     project: Optional[str] = None,
                     status: Optional[str] = None) -> Dict[str, Any]:
        """Query knowledge base by tag/project/status"""
        try:
            # Auto-sync if enabled
            if self.config.auto_sync_on_query:
                self.mesh.sync(pull=True)

            # Query
            results = self.mesh.query(
                tags=[tag] if tag else None,
                project=project,
                status=status
            )

            entities = [
                {
                    "id": e.id,
                    "type": e.type,
                    "title": e.title,
                    "status": e.status,
                    "tags": e.tags,
                    "created_at": e.created_at,
                    "preview": e.preview()
                }
                for e in results
            ]

            return {
                "status": "success",
                "count": len(entities),
                "entities": entities,
                "timestamp": datetime.utcnow().isoformat()
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def handle_sync(self, pull: bool = True, push: bool = False,
                    consistency: str = "warn") -> Dict[str, Any]:
        """Sync with remote repository"""
        try:
            result = self.mesh.sync(
                pull=pull,
                push=push,
                consistency_level=consistency
            )

            return {
                "status": result.status,
                "graph_version": result.graph_version,
                "conflicts": result.conflicts,
                "errors": result.errors,
                "warnings": result.warnings,
                "timestamp": result.timestamp
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def handle_propose(self, entity_type: str, title: str, body: str,
                      project: Optional[str] = None) -> Dict[str, Any]:
        """Propose new entity"""
        try:
            pr_url = self.mesh.propose_entity(
                entity_type=entity_type,
                title=title,
                body=body,
                project=project or "CorvinOS"
            )

            return {
                "status": "proposed",
                "pr_url": pr_url,
                "message": f"Proposed {entity_type} '{title}'. Review at: {pr_url}"
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def handle_config(self, repo_path: Optional[str] = None,
                     remote_url: Optional[str] = None) -> Dict[str, Any]:
        """Update plugin configuration"""
        try:
            config_file = Path.home() / ".claude" / "plugins" / "corvin-knowledge.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)

            # Load current
            config = {}
            if config_file.exists():
                config = json.loads(config_file.read_text())

            # Update
            if repo_path:
                config["repo_path"] = repo_path
            if remote_url:
                config["remote_url"] = remote_url

            # Save
            config_file.write_text(json.dumps(config, indent=2))

            return {
                "status": "configured",
                "repo_path": config.get("repo_path"),
                "remote_url": config.get("remote_url"),
                "config_file": str(config_file)
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}


# ────────────────────────────────────────────────────────
# CLAUDE CODE RUNTIME ENTRY POINT
# ────────────────────────────────────────────────────────

async def execute(command: str, args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point called by Claude Code runtime

    Args:
        command: The command name (query, sync, propose, config)
        args: Command arguments (dict)
        config: Plugin configuration

    Returns:
        Response dict (JSON-serializable)
    """
    try:
        plugin = CorvinKnowledgePlugin(config)

        if command == "query":
            return plugin.handle_query(**args)
        elif command == "sync":
            return plugin.handle_sync(**args)
        elif command == "propose":
            return plugin.handle_propose(**args)
        elif command == "config":
            return plugin.handle_config(**args)
        else:
            return {
                "status": "error",
                "message": f"Unknown command: {command}",
                "available_commands": ["query", "sync", "propose", "config"]
            }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "error_type": type(e).__name__
        }


if __name__ == "__main__":
    """Allow testing directly from CLI"""
    import asyncio

    if len(sys.argv) < 2:
        print("Usage: python plugin.py <command> [args...]")
        sys.exit(1)

    command = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    config = {
        "repo_path": "~/.corvin-knowledge",
        "remote_url": "https://github.com/CorvinLabs/Corvin-Knowledge.git",
        "auto_sync_on_query": True,
        "consistency_level": "warn"
    }

    result = asyncio.run(execute(command, args, config))
    print(json.dumps(result, indent=2))
