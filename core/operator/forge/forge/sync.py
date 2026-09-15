"""Sync promoted forge skills into Claude Code's user skill directory.

Workflow:
  1. ``forge_promote`` writes ``<workspace>/skills/<name>/SKILL.md + impl``
  2. ``forge sync`` copies those into ``~/.claude/skills/<name>/`` so Claude
     Code's ordinary skill-discovery picks them up in *future* sessions —
     even sessions that don't run the forge MCP server.

The sync is idempotent and refuses to overwrite a non-forge skill (we look
for ``promoted_from: forge`` in the frontmatter to be safe).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SyncRecord:
    name: str
    src: Path
    dst: Path
    action: str  # "created" | "updated" | "skipped" | "refused"
    reason: str = ""


def _is_forge_skill(skill_md: Path) -> bool:
    if not skill_md.exists():
        return True  # absent → safe to write
    try:
        head = skill_md.read_text()[:512]
    except OSError:
        return False
    return "promoted_from: forge" in head


def sync(workspace_root: Path,
         *,
         target_root: Path | None = None,
         dry_run: bool = False) -> list[SyncRecord]:
    """Copy ``<workspace>/skills/*`` into ``target_root`` (default
    ``~/.claude/skills/``)."""
    workspace_skills = Path(workspace_root) / "skills"
    if not workspace_skills.exists():
        return []
    if target_root is None:
        target_root = Path.home() / ".claude" / "skills"

    records: list[SyncRecord] = []
    for src in sorted(workspace_skills.iterdir()):
        if not src.is_dir():
            continue
        name = src.name
        dst = Path(target_root) / name
        dst_skill_md = dst / "SKILL.md"

        if dst.exists() and not _is_forge_skill(dst_skill_md):
            records.append(SyncRecord(name, src, dst, "refused",
                                       reason="non-forge skill at target"))
            continue

        action = "updated" if dst.exists() else "created"
        if dry_run:
            records.append(SyncRecord(name, src, dst, "skipped",
                                       reason="dry run"))
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        records.append(SyncRecord(name, src, dst, action))

    return records
