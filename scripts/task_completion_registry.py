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

Output: $CORVIN_HOME/task_registry.json (default ~/.corvin; machine-readable, for
context pipeline). Paths are overridable: --corvin-home, --adr-root, --repo-root,
--memory-root.
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import os
import subprocess
import argparse


# Status vocabulary — kept identical to
# core/console/corvin_console/task_tracking_git_sync.py (_DONE / _ARCHIVED), so the
# daily registry and the task-tracking sync pick the same sibling for a number.
_DONE = {"ACCEPTED", "IMPLEMENTED", "DEPLOYED", "COMPLETE", "COMPLETED", "DONE", "LIVE", "SHIPPED"}
_ARCHIVED = {"REJECTED", "SUPERSEDED", "DEPRECATED", "WITHDRAWN", "OBSOLETE", "ABANDONED"}

# A decision's number is the one its FILE NAME carries, exactly as
# ``task_tracking_git_sync.adr_meta`` (globs ``ADR-NNNN-*.md``, ``ADR-NNNN.md``,
# ``NNNN-*.md``, ``NNNN.md``) and ``context_engineering.adr_loader`` key it:
# four digits, optionally behind an upper-case ``ADR-``, then a hyphen or the end
# of the stem. Renumbering moved ~57 files to new numbers but left their OLD id in
# the frontmatter (``ADR-0800-0472-…`` says ``id: ADR-0472``), so the file name
# is the current number and the frontmatter id is only a fallback.
_FILENAME_NUM_RE = re.compile(r"^(?:ADR-)?(\d{4})(?:-|$)")
_FM_ID_RE = re.compile(r"^(?:ADR-)?(\d{4})(?!\d)")
# Same block delimiter as core.quality_gates.artifacts.parse_frontmatter, which
# is what adr_meta reads a record's status through.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover — the shipped venv carries PyYAML
    yaml = None


def _status_word(status: Optional[str]) -> str:
    return (str(status or "").strip().split() or [""])[0].upper().strip(".,;:()")


def _fm_value(frontmatter: str, key: str) -> Optional[str]:
    m = re.search(rf"^{key}:[ \t]*(.*?)[ \t]*$", frontmatter, re.MULTILINE)
    if not m:
        return None
    v = m.group(1).split(" #", 1)[0].strip().strip("'\"").strip()
    return v or None


def _parse_frontmatter(text: str) -> tuple:
    """``(fields, body)`` read the way adr_meta reads them: YAML, and a block
    that does not parse to a mapping counts as no frontmatter. Without PyYAML
    the ``key: value`` lines are read directly."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    if yaml is None:
        return {k: _fm_value(m.group(1), k) for k in ("id", "status")}, body
    try:
        data = yaml.safe_load(m.group(1))
    except Exception:  # noqa: BLE001 — a broken block is "no frontmatter", as in adr_meta
        return {}, body
    return (data if isinstance(data, dict) else {}), body


def _filename_number(filename: str) -> Optional[str]:
    """The four-digit number a decision file's name carries, or None."""
    m = _FILENAME_NUM_RE.match(Path(filename).stem)
    if m is None or m.group(1) == "0000":
        return None
    return m.group(1)


def _fallback_number(raw_id: Optional[str], filename: str) -> Optional[str]:
    """The frontmatter id's number, for a file whose name carries none.

    ``DOC-*`` reports and placeholder ids (``ADR-0000``, ``ADR-0XXX``) are not
    decisions."""
    if filename.startswith("DOC-"):
        return None
    m = _FM_ID_RE.match(str(raw_id or ""))
    if m is None or m.group(1) == "0000":
        return None
    return m.group(1)


def _read_adr_record(path: Path) -> Optional[dict]:
    content = path.read_text(encoding="utf-8", errors="replace")
    fields, body = _parse_frontmatter(content)
    file_num = _filename_number(path.name)
    num = file_num or _fallback_number(fields.get("id"), path.name)
    if num is None:
        return None
    title_match = re.search(r"^# (.+)$", body, re.MULTILINE)
    return {
        "adr_id": f"ADR-{num}",
        "by_filename": file_num is not None,
        "status": str(fields.get("status") or "").strip() or None,
        "title": title_match.group(1).strip() if title_match else None,
        "file": path.name,
    }


def _file_order(rec: dict) -> tuple:
    """adr_meta's file order for one number: ``ADR-NNNN-*.md`` (sorted),
    ``ADR-NNNN.md``, ``NNNN-*.md`` (sorted), ``NNNN.md``."""
    stem = Path(rec["file"]).stem
    return (not stem.startswith("ADR-"), stem == rec["adr_id"] or stem == rec["adr_id"][4:], rec["file"])


def _sibling_rank(i_rec: tuple) -> tuple:
    """Lowest rank decides: a live record before an archived one, an open one
    before a done one, then file order (mirrors task_tracking_git_sync.adr_meta)."""
    i, rec = i_rec
    word = _status_word(rec["status"])
    return (word in _ARCHIVED, word in _DONE, i)


def _task_status(adr_status: Optional[str]) -> str:
    """Registry state of a record status. Every word adr_meta reads as done
    (``status_for`` → complete) is ACCEPTED here — not only the literal word —
    so the registry and the sync never disagree on whether a number is done."""
    word = _status_word(adr_status)
    if word in _DONE:
        return "ACCEPTED"
    if word in _ARCHIVED:
        return "ARCHIVED"
    if word == "PROPOSED":
        return "IN_PROGRESS"
    return "UNKNOWN"


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

    def __init__(self, corvin_home: str = None, adr_root: str = None,
                 repo_root: str = None, memory_root: str = None):
        # CORVIN_HOME is honoured (the systemd unit sets it); never hard-wire ~/.corvin.
        self.corvin_home = Path(corvin_home or os.environ.get("CORVIN_HOME") or Path.home() / ".corvin")
        self.registry_file = self.corvin_home / "task_registry.json"
        self.adr_root = Path(adr_root) if adr_root else Path.home() / "projects" / "Corvin-ADR" / "decisions"
        self.repo_root = Path(repo_root) if repo_root else Path.home() / "projects" / "CorvinOS"
        self.memory_root = (Path(memory_root) if memory_root else
                            self.repo_root / ".claude" / "projects" / "-home-shumway-projects-CorvinOS" / "memory")

    def scan_adr_registry(self) -> Dict[str, TaskStatus]:
        """Scan Corvin-ADR/decisions/ for task definitions and status.

        Every ``*.md`` file is considered, not only ``ADR-*.md``: the record repo
        carries two naming schemes side by side (``ADR-NNNN-slug.md`` and the
        older ``NNNN-slug.md``). A file is keyed by the number its FILE NAME
        carries — the same key ``corvin_console.task_tracking_git_sync.adr_meta``
        and ``context_engineering.adr_loader`` use (four digits, ``ADR-`` prefix
        optional, then ``-`` or the end of the stem; ``ADR-0800-0472-…`` is
        ADR-0800, whatever its stale frontmatter id says). A file whose name
        carries no number (``adr_0801_system.md``, ``ADR-001-…``) falls back to
        its frontmatter id, and only for a number no file name carries — so it
        can never outvote the files adr_meta reads. ``DOC-*``, ``README`` and
        placeholder ids (``ADR-0000`` / ``ADR-0XXX``) are not decisions.

        One number can be carried by several files. The record that decides is
        chosen exactly as ``adr_meta`` chooses it (same file order, same rank),
        so a stale sibling can never mark work done: superseded / rejected
        siblings are ignored while a live one exists, and among live siblings
        the one that is NOT done wins — two live records that disagree read as
        open. Every status ``adr_meta`` reads as done maps to ACCEPTED.
        """
        tasks: Dict[str, TaskStatus] = {}

        if not self.adr_root.exists():
            print(f"⚠️  ADR root not found: {self.adr_root}")
            return tasks

        records: Dict[str, List[dict]] = {}
        for adr_file in sorted(self.adr_root.glob("*.md")):
            try:
                rec = _read_adr_record(adr_file)
            except Exception as e:  # noqa: BLE001 — one unreadable file never stops the scan
                print(f"⚠️  Error parsing {adr_file}: {e}")
                continue
            if rec is not None:
                records.setdefault(rec["adr_id"], []).append(rec)

        for adr_id, recs in records.items():
            by_name = [r for r in recs if r["by_filename"]]
            recs = sorted(by_name or recs, key=_file_order)
            chosen = min(enumerate(recs), key=_sibling_rank)[1]
            adr_status = chosen["status"] or "UNKNOWN"
            task_status = _task_status(chosen["status"])

            notes = f"status read from {chosen['file']}"
            if len(recs) > 1:
                notes += (f"; {len(recs)} files carry this number "
                          f"({', '.join(r['file'] for r in recs)})")

            task_id = adr_id.lower().replace("-", "_")
            tasks[task_id] = TaskStatus(
                task_id=task_id,
                title=chosen["title"] or adr_id,
                category="adr",
                status=task_status,
                adr_id=adr_id,
                adr_status=adr_status,
                completion_date=datetime.now(timezone.utc).isoformat() if task_status == "ACCEPTED" else None,
                notes=notes,
            )

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


def main(argv: Optional[List[str]] = None):
    """Run the registry builder."""
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--corvin-home", help="runtime root (default: $CORVIN_HOME, else ~/.corvin)")
    ap.add_argument("--adr-root", help="decisions/ directory to scan")
    ap.add_argument("--repo-root", help="CorvinOS checkout for the git scan")
    ap.add_argument("--memory-root", help="memory directory holding MEMORY.md")
    args = ap.parse_args(argv)
    registry = TaskRegistry(corvin_home=args.corvin_home, adr_root=args.adr_root,
                            repo_root=args.repo_root, memory_root=args.memory_root)
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
