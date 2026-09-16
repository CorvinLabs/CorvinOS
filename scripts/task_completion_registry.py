#!/usr/bin/env python3
"""
Task Completion Registry — Single Source of Truth for Initiative Status.

Scans ADR registry, Git commits, and Memory files to build a canonical
task registry. Runs daily to keep status synchronized.

Sources (Priority Order):
1. ADR Status (`status: ACCEPTED` in ADR frontmatter) — **CANONICAL**
2. Git commits (date, author, message pattern `[COMPLETE]` or `[WIP]`)
3. Memory registry (MEMORY.md and topic files)
4. Implementation plans (docs/implementation-plans/)

Output: ~/.corvin/task_registry.json (machine-readable, for context pipeline)
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import subprocess


@dataclass
class TaskStatus:
    """Canonical task status record."""
    task_id: str                    # e.g., "skill-forge-v2.0", "adr-0672"
    title: str
    category: str                  # "skill", "adr", "feature", "blocker"
    status: str                    # "ACCEPTED", "IN_PROGRESS", "BLOCKED", "BACKLOG"
    completion_date: Optional[str] = None  # ISO 8601 when marked ACCEPTED
    adr_id: Optional[str] = None           # e.g., "ADR-0672"
    adr_status: Optional[str] = None       # from ADR frontmatter
    commit_hash: Optional[str] = None      # last commit mentioning task
    commit_date: Optional[str] = None
    memory_file: Optional[str] = None      # MEMORY.md entry
    depends_on: List[str] = None           # other task_ids
    notes: str = ""

    def __post_init__(self):
        if self.depends_on is None:
            self.depends_on = []


class TaskRegistry:
    """Builds and maintains canonical task registry."""

    def __init__(self, corvin_home: str = None):
        self.corvin_home = Path(corvin_home or Path.home() / ".corvin")
        self.registry_file = self.corvin_home / "task_registry.json"
        self.adr_root = Path.home() / "projects" / "Corvin-ADR" / "decisions"
        self.repo_root = Path.home() / "projects" / "CorvinOS"
        self.memory_root = self.repo_root / ".claude" / "projects" / "-home-shumway-projects-CorvinOS" / "memory"

    def scan_adr_registry(self) -> Dict[str, TaskStatus]:
        """Scan Corvin-ADR/decisions/ for task definitions and status."""
        tasks = {}

        if not self.adr_root.exists():
            print(f"⚠️  ADR root not found: {self.adr_root}")
            return tasks

        for adr_file in sorted(self.adr_root.glob("ADR-*.md")):
            # Parse frontmatter
            try:
                with open(adr_file) as f:
                    content = f.read()

                match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
                if not match:
                    continue

                frontmatter = match.group(1)

                # Extract fields
                id_match = re.search(r'^id:\s*(\S+)', frontmatter, re.MULTILINE)
                status_match = re.search(r'^status:\s*(\S+)', frontmatter, re.MULTILINE)

                if not id_match:
                    continue

                adr_id = id_match.group(1)
                adr_status = status_match.group(1) if status_match else "UNKNOWN"

                # Convert ADR status to task status
                if adr_status == "ACCEPTED":
                    task_status = "ACCEPTED"
                elif adr_status == "PROPOSED":
                    task_status = "IN_PROGRESS"
                elif adr_status == "SUPERSEDED":
                    task_status = "ARCHIVED"
                else:
                    task_status = "UNKNOWN"

                # Extract title (first heading after frontmatter)
                title_match = re.search(r'^# (.+)$', content[match.end():], re.MULTILINE)
                title = title_match.group(1) if title_match else adr_id

                # Create task entry
                task_id = adr_id.lower().replace("-", "_")
                tasks[task_id] = TaskStatus(
                    task_id=task_id,
                    title=title,
                    category="adr",
                    status=task_status,
                    adr_id=adr_id,
                    adr_status=adr_status,
                    completion_date=datetime.now(timezone.utc).isoformat() if adr_status == "ACCEPTED" else None,
                )

            except Exception as e:
                print(f"⚠️  Error parsing {adr_file}: {e}")

        return tasks

    def scan_git_commits(self) -> Dict[str, TaskStatus]:
        """Scan Git history for commit messages with task markers."""
        tasks = {}

        if not self.repo_root.exists():
            print(f"⚠️  Repo root not found: {self.repo_root}")
            return tasks

        try:
            # Get last 100 commits with task markers
            result = subprocess.run(
                ["git", "log", "--oneline", "-100"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=5
            )

            for line in result.stdout.split("\n"):
                if not line.strip():
                    continue

                # Parse: "hash message"
                parts = line.split(" ", 1)
                if len(parts) < 2:
                    continue

                commit_hash = parts[0]
                message = parts[1]

                # Look for patterns: [COMPLETE], [DONE], [WIP], ADR-XXXX
                if "[COMPLETE]" in message or "[DONE]" in message:
                    # Extract task name from message
                    task_match = re.search(r'(ADR-\d+|[\w-]+).*?\[(COMPLETE|DONE)\]', message)
                    if task_match:
                        task_name = task_match.group(1).lower().replace("-", "_")
                        if task_name not in tasks:
                            tasks[task_name] = TaskStatus(
                                task_id=task_name,
                                title=message[:60],
                                category="feature",
                                status="ACCEPTED",
                                commit_hash=commit_hash,
                            )

        except Exception as e:
            print(f"⚠️  Error scanning Git: {e}")

        return tasks

    def scan_memory_registry(self) -> Dict[str, TaskStatus]:
        """Scan MEMORY.md and topic files for task status."""
        tasks = {}

        if not self.memory_root.exists():
            print(f"⚠️  Memory root not found: {self.memory_root}")
            return tasks

        # Scan MEMORY.md
        memory_file = self.memory_root / "MEMORY.md"
        if memory_file.exists():
            try:
                with open(memory_file) as f:
                    content = f.read()

                # Look for task entries with status markers
                # Pattern: `- [✅/🟢 status] Task Name`
                for match in re.finditer(r'- (\[✅\]|\[🟢\]|✅|🟢)(.+?)(?:\((.+?)\))?$', content, re.MULTILINE):
                    task_name = match.group(2).strip()
                    task_id = task_name.lower().replace(" ", "_").replace(":", "")

                    if task_id not in tasks:
                        tasks[task_id] = TaskStatus(
                            task_id=task_id,
                            title=task_name,
                            category="initiative",
                            status="ACCEPTED",
                            memory_file="MEMORY.md",
                        )

            except Exception as e:
                print(f"⚠️  Error parsing MEMORY.md: {e}")

        return tasks

    def build_registry(self) -> Dict[str, TaskStatus]:
        """Build canonical registry from all sources."""
        print("🔄 Scanning task sources...")

        # Scan all sources
        adr_tasks = self.scan_adr_registry()
        print(f"   ✅ ADR registry: {len(adr_tasks)} tasks")

        git_tasks = self.scan_git_commits()
        print(f"   ✅ Git commits: {len(git_tasks)} tasks")

        memory_tasks = self.scan_memory_registry()
        print(f"   ✅ Memory: {len(memory_tasks)} tasks")

        # Merge: ADR > Git > Memory (priority order)
        tasks = {}
        tasks.update(memory_tasks)      # Base
        tasks.update(git_tasks)         # Override with Git
        tasks.update(adr_tasks)         # Override with ADR (canonical)

        print(f"🎯 Canonical registry: {len(tasks)} unique tasks")
        return tasks

    def save_registry(self, tasks: Dict[str, TaskStatus]):
        """Save registry to JSON file."""
        self.corvin_home.mkdir(parents=True, exist_ok=True)

        registry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_count": len(tasks),
            "tasks": {k: asdict(v) for k, v in tasks.items()},
        }

        with open(self.registry_file, "w") as f:
            json.dump(registry, f, indent=2)

        print(f"✅ Registry saved: {self.registry_file}")

    def generate_report(self, tasks: Dict[str, TaskStatus]) -> str:
        """Generate human-readable status report."""
        report = []
        report.append("# Task Completion Registry Report\n")
        report.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n")

        # Group by status
        by_status = {}
        for task in tasks.values():
            status = task.status
            if status not in by_status:
                by_status[status] = []
            by_status[status].append(task)

        for status in ["ACCEPTED", "IN_PROGRESS", "BLOCKED", "BACKLOG", "UNKNOWN"]:
            if status not in by_status:
                continue

            task_list = by_status[status]
            report.append(f"\n## {status} ({len(task_list)} tasks)\n")

            for task in sorted(task_list, key=lambda t: t.title):
                marker = "✅" if status == "ACCEPTED" else "🟡" if status == "IN_PROGRESS" else "❌"
                report.append(f"{marker} **{task.title}** ({task.task_id})")
                if task.adr_id:
                    report.append(f"   - ADR: {task.adr_id}")
                if task.completion_date:
                    report.append(f"   - Completed: {task.completion_date[:10]}")
                report.append("")

        return "\n".join(report)


def main():
    """Run the registry builder."""
    registry = TaskRegistry()
    tasks = registry.build_registry()
    registry.save_registry(tasks)

    # Generate report
    report = registry.generate_report(tasks)
    print("\n" + report)

    # Show ACCEPTED tasks (ready for next phase)
    accepted = [t for t in tasks.values() if t.status == "ACCEPTED"]
    print(f"\n🎯 READY FOR NEXT PHASE ({len(accepted)} tasks):")
    for task in sorted(accepted, key=lambda t: t.title)[:10]:
        print(f"   ✅ {task.title}")

    if len(accepted) > 10:
        print(f"   ... and {len(accepted) - 10} more")


if __name__ == "__main__":
    main()
