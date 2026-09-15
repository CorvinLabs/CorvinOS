"""Workspace-scope detection for forge artifacts — TENANT-NATIVE (ADR-0433).

Four scopes determine where a forged tool lives:

  task     <tmpdir>/.corvin/tasks/<task-id>/forge/    (one Q&A turn; platform temp)
  session  ~/.corvin/tenants/<tenant_id>/sessions/<channel-id>/forge/      (one bridge channel)
  project  <repo-root>/.corvin/forge/                  (one git repo)
  user     ~/.corvin/tenants/<tenant_id>/forge/       (permanent, tenant-scoped)

Detection precedence (when caller does not pass an explicit scope):

  1. CORVIN_FORCE_SCOPE   env var
  2. CORVIN_DEFAULT_SCOPE env var
  3. CORVIN_CHANNEL_ID    env var → session
  4. cwd is inside a git repo        → project
  5. fallback                        → user

TENANT-NATIVE CHANGE (Phase B):
- scope_root() now requires mandatory tenant_id parameter
- All session/user scopes return tenant-scoped paths
- Task scope ignores tenant_id (uses temp dir; v0.3 will fix)
- Project scope ignores tenant_id (uses repo root; tenant-per-repo later)

Migration note (Phase 7): CORVIN_* env-var aliases are no longer read.
On-disk, <repo>/.corvin/ is the project root (no legacy .corvinOS fallback — hard cut).
"""
from __future__ import annotations

import tempfile
import os
import subprocess
from pathlib import Path

from .paths import corvin_home, fs_safe_component, tenant_home, _validate_tenant_id

VALID_SCOPES = ("task", "session", "project", "user")


def _resolve_repo_workspace(repo: Path) -> Path:
    """Return `repo/.corvin`.

    Hard cut (CLAUDE.md rebrand): the legacy `.corvinOS/` fallback was removed —
    it diverged from forge/paths.py::corvin_home() (which I also cut) and the
    other resolvers, all `.corvin`-only. Migration is corvin_migrate.py's job,
    not a hot-path fallback (path-audit 2026-06-25). Creates no directories."""
    return repo / ".corvin"


def _resolve_tmp_tasks_root() -> Path:
    """Return `<tmpdir>/.corvin/tasks` — uses the platform temp dir so it resolves
    on Windows too (was hardcoded POSIX `/tmp`, path-audit #MEDIUM8)."""
    return Path(tempfile.gettempdir()) / ".corvin" / "tasks"


def detect_scope() -> str:
    forced = os.environ.get("CORVIN_FORCE_SCOPE")
    if forced in VALID_SCOPES:
        return forced
    default = os.environ.get("CORVIN_DEFAULT_SCOPE")
    if default in VALID_SCOPES:
        return default
    if os.environ.get("CORVIN_CHANNEL_ID"):
        return "session"
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return "project"
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return "user"


def scope_root(scope: str, *,
               tenant_id: str,  # NEW: mandatory, tenant-aware (Phase B)
               channel_id: str | None = None,
               task_id: str | None = None,
               project_root: Path | None = None) -> Path:
    """Resolve the workspace directory for a given scope (TENANT-NATIVE).

    Args:
        scope: "task", "session", "project", or "user"
        tenant_id: Required. Tenant context for session/user scopes. Validated fail-closed.
        channel_id: For scope="session" (optional; defaults to env CORVIN_CHANNEL_ID)
        task_id: For scope="task" (optional; defaults to env CORVIN_TASK_ID)
        project_root: For scope="project" (optional; auto-detects via git)

    Returns:
        Path to the workspace root for the given scope and tenant.

    Raises:
        ValueError: If tenant_id is invalid or required args missing.
    """
    # Validate tenant_id fail-closed (ADR-0433 compliance)
    _validate_tenant_id(tenant_id)

    if scope == "task":
        # Task scope ignores tenant_id (uses platform temp dir)
        # TODO: v0.3 will make task scope tenant-scoped too
        tid = (
            task_id
            or os.environ.get("CORVIN_TASK_ID")
            or "default"
        )
        return _resolve_tmp_tasks_root() / tid / "forge"

    if scope == "session":
        # Session scope: ~/.corvin/tenants/<tenant_id>/sessions/<channel_id>/forge
        cid = (
            channel_id
            or os.environ.get("CORVIN_CHANNEL_ID")
            or "default"
        )
        # CORVIN_CHANNEL_ID is "<bridge>:<chat_key>" — the ':' is legal in a
        # POSIX path component but ILLEGAL in a Windows one (drive separator), so
        # mkdir(parents=True) on this root raised NotADirectoryError [WinError 267]
        # on the first session-scoped Forge tool creation (the 0.9.49 WinError-267
        # class). fs_safe_component neutralises ':' → '_' on Windows and is a
        # byte-for-byte no-op on POSIX (only '/' and NUL), so existing Linux/macOS
        # session workdirs stay identical — mirrors _workdir / session_artifacts_dir.
        return tenant_home(tenant_id) / "sessions" / fs_safe_component(cid) / "forge"

    if scope == "project":
        # Project scope: <repo>/.corvin/forge (ignores tenant_id today; v0.3 fix)
        # TODO: v0.3 will gate project scope per-tenant once repo multi-tenancy is designed
        if project_root is not None:
            return _resolve_repo_workspace(project_root) / "forge"
        # Allow tests to suppress git-based project-root discovery by setting
        # CORVIN_PROJECT_ROOT="" — returns user-scope fallback instead.
        _pr_env = os.environ.get("CORVIN_PROJECT_ROOT", None)
        if _pr_env is not None:
            if _pr_env.strip():
                return _resolve_repo_workspace(Path(_pr_env.strip())) / "forge"
            return tenant_home(tenant_id) / "forge"
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                return _resolve_repo_workspace(Path(r.stdout.strip())) / "forge"
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        return tenant_home(tenant_id) / "forge"

    if scope == "user":
        # User scope: ~/.corvin/tenants/<tenant_id>/forge (tenant-native)
        return tenant_home(tenant_id) / "forge"

    raise ValueError(f"unknown scope: {scope!r} (valid: {VALID_SCOPES})")
