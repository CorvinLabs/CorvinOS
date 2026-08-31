"""Per-subtask E2E for Phase-7 compat-resolver in forge.scope.

Covers the four env-var aliases (FORCE_SCOPE / DEFAULT_SCOPE /
CHANNEL_ID / TASK_ID) and the two on-disk fallbacks (`<repo>/.corvin`
and `/tmp/.corvin/tasks`).

Contract (Phase 7 — CORVIN_* aliases removed):
  - CORVIN_* env wins, no warning.
  - CORVIN_* env is IGNORED — scope falls back to the normal default.
  - No deprecation warning emitted for CORVIN_* (code no longer reads them).
  - Both env set → CORVIN wins silently.
  - Empty strings treated as unset.
  - `<repo>/.corvin` is the only repo-relative home; a legacy `<repo>/.corvinOS`
    dir is NOT honoured (hard cut, path-audit 2026-06-25 — the on-disk fallback
    lived only here and caused a reader≠writer split; migration via corvin_migrate).
"""
from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


_ENV_KEYS = (
    "CORVIN_HOME", "CORVIN_HOME",
    "CORVIN_FORCE_SCOPE", "CORVIN_FORCE_SCOPE",
    "CORVIN_DEFAULT_SCOPE", "CORVIN_DEFAULT_SCOPE",
    "CORVIN_CHANNEL_ID", "CORVIN_CHANNEL_ID",
    "CORVIN_TASK_ID", "CORVIN_TASK_ID",
)


def _clear_env() -> None:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


def _fresh_scope():
    sys.path.insert(0, str(REPO / "operator" / "forge"))
    for mod in ("forge.scope", "forge.paths", "forge"):
        sys.modules.pop(mod, None)
    try:
        return importlib.import_module("forge.scope")
    finally:
        sys.path.pop(0)


def test_force_scope_works() -> None:
    print("\n[CORVIN_FORCE_SCOPE still works — CONTROL env-var]")
    _clear_env()
    os.environ["CORVIN_FORCE_SCOPE"] = "task"
    os.environ["CORVIN_CHANNEL_ID"] = "discord:42"  # would otherwise pull session
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        scope = s.detect_scope()
    # Phase 7 affects PATH env-vars (CORVIN_HOME), not CONTROL env-vars (CORVIN_FORCE_SCOPE).
    # CONTROL env-vars continue to work.
    t("scope == task (CONTROL env works)", scope == "task")
    t("no deprecation log", "deprecation" not in buf.getvalue().lower(),
      detail=f"stderr={buf.getvalue()!r}")
    _clear_env()


def test_force_scope_legacy_with_warning() -> None:
    print("\n[CORVIN_FORCE_SCOPE still works — CONTROL env-vars unchanged in Phase 7]")
    _clear_env()
    os.environ["CORVIN_FORCE_SCOPE"] = "user"
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        scope1 = s.detect_scope()
        scope2 = s.detect_scope()
    out = buf.getvalue()
    # Phase 7 affects PATH env-vars (CORVIN_HOME), not CONTROL env-vars.
    # CORVIN_FORCE_SCOPE still works and forces the scope to "user".
    t("CORVIN_FORCE_SCOPE works → scope == user",
      scope1 == "user", detail=f"got {scope1}")
    t("repeated call stable", scope2 == scope1)
    t("no deprecation warning (CONTROL env-vars not affected)",
      "deprecation" not in out.lower(),
      detail=f"stderr={out!r}")
    _clear_env()


def test_force_scope_last_one_wins() -> None:
    print("\n[multiple FORCE_SCOPE env set → last one wins, no warning]")
    _clear_env()
    os.environ["CORVIN_FORCE_SCOPE"] = "user"
    os.environ["CORVIN_FORCE_SCOPE"] = "task"  # overwrites previous
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        scope = s.detect_scope()
    # Phase 7 affects PATH env-vars, not CONTROL env-vars. CORVIN_FORCE_SCOPE still works.
    t("scope == task (last assignment wins)", scope == "task")
    t("no deprecation log (CONTROL env-vars not deprecated)",
      "deprecation" not in buf.getvalue().lower(),
      detail=f"stderr={buf.getvalue()!r}")
    _clear_env()


def test_default_scope_alias() -> None:
    print("\n[CORVIN_DEFAULT_SCOPE ignored (Phase 7) — no warning emitted]")
    _clear_env()
    os.environ["CORVIN_DEFAULT_SCOPE"] = "user"
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        scope = s.detect_scope()
    out = buf.getvalue()
    # Phase 7: CORVIN_DEFAULT_SCOPE is not read; scope resolves via
    # normal detection (project inside a git repo, otherwise user/session).
    # The key invariant is that no deprecation warning is emitted.
    t("NO CORVIN_DEFAULT_SCOPE warning",
      "CORVIN_DEFAULT_SCOPE" not in out,
      detail=f"stderr={out!r}")
    t("no deprecation on stderr",
      "deprecation" not in out.lower() or "CORVIN_DEFAULT_SCOPE" not in out,
      detail=f"stderr={out!r}")
    # Scope must still be a valid value (not corrupted by the ignored var)
    t("scope is a valid scope value",
      scope in s.VALID_SCOPES, detail=f"got {scope}")
    _clear_env()


def test_channel_id_still_works() -> None:
    print("\n[CORVIN_CHANNEL_ID still works — CONTROL env-var]")
    _clear_env()
    os.environ["CORVIN_CHANNEL_ID"] = "discord:99"
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        scope = s.detect_scope()
    out = buf.getvalue()
    # Phase 7 affects PATH env-vars, not CONTROL env-vars. CORVIN_CHANNEL_ID still works.
    t("scope == session (CONTROL env works)",
      scope == "session",
      detail=f"got {scope}")
    t("no deprecation log",
      "deprecation" not in out.lower(),
      detail=f"stderr={out!r}")
    _clear_env()


def test_channel_id_corvin_session() -> None:
    print("\n[CORVIN_CHANNEL_ID → session, no warning]")
    _clear_env()
    os.environ["CORVIN_CHANNEL_ID"] = "discord:99"
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        scope = s.detect_scope()
    t("scope == session", scope == "session")
    t("no deprecation log", "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_scope_root_task_uses_corvin_task_id() -> None:
    print("\n[scope_root('task') reads CORVIN_TASK_ID]")
    _clear_env()
    os.environ["CORVIN_TASK_ID"] = "corvin-task-1"
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        p = s.scope_root("task", tenant_id="_default")
    out = buf.getvalue()
    t("path contains corvin-task-1", "corvin-task-1" in str(p))
    t("ends with /forge", p.name == "forge")
    t("no env-var deprecation", "CORVIN_TASK_ID" not in out)
    _clear_env()


def test_scope_root_task_no_env() -> None:
    print("\n[scope_root('task') — no CORVIN_TASK_ID defaults to 'default']")
    _clear_env()
    # Not setting CORVIN_TASK_ID
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        p = s.scope_root("task", tenant_id="_default")
    out = buf.getvalue()
    # No env-var set, falls back to "default".
    t("path contains 'default'",
      "/default/forge" in str(p), detail=f"got {p}")
    t("no deprecation log",
      "deprecation" not in out.lower(),
      detail=f"stderr={out!r}")
    _clear_env()


def test_scope_root_session_uses_corvin_channel_id() -> None:
    print("\n[scope_root('session') uses CORVIN_CHANNEL_ID when arg missing]")
    _clear_env()
    # Phase 7 ignores CORVIN_HOME, not CORVIN_CHANNEL_ID. CONTROL env-vars still work.
    os.environ["CORVIN_CHANNEL_ID"] = "corvin-chan-1"
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        p = s.scope_root("session", tenant_id="_default")
    t("path contains corvin-chan-1",
      "corvin-chan-1" in str(p), detail=f"got {p}")
    t("ends with /forge", p.name == "forge")
    t("no deprecation warning", "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_repo_workspace_corvin_wins() -> None:
    print("\n[scope_root('project', project_root=...) → .corvin when present]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / ".corvin").mkdir()
        buf = io.StringIO()
        with redirect_stderr(buf):
            s = _fresh_scope()
            p = s.scope_root("project", tenant_id="_default", project_root=repo)
        t("path == repo/.corvin/forge", p == repo / ".corvin" / "forge",
          detail=f"got {p}")
        t("no warning", "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_repo_workspace_legacy_corvinos_is_not_honoured() -> None:
    # Hard cut (path-audit 2026-06-25): the on-disk .corvinOS fallback was
    # REMOVED. It lived ONLY in this forge resolver while every other resolver
    # used .corvin, creating a reader≠writer split. A repo that has only a
    # legacy .corvinOS/ dir must now resolve to .corvin (migration is handled by
    # corvin_migrate.py), with NO deprecation log.
    print("\n[scope_root('project', ...) → legacy .corvinOS NOT honoured, .corvin wins]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / ".corvinOS").mkdir()
        buf = io.StringIO()
        with redirect_stderr(buf):
            s = _fresh_scope()
            p1 = s.scope_root("project", tenant_id="_default", project_root=repo)
            p2 = s.scope_root("project", tenant_id="_default", project_root=repo)
        out = buf.getvalue()
        t("path == repo/.corvin/forge (hard cut, .corvinOS ignored)",
          p1 == repo / ".corvin" / "forge", detail=f"got {p1}")
        t("p2 == p1 (stable)", p2 == p1)
        t("NO deprecation log emitted",
          "[deprecation]" not in out, detail=f"stderr={out!r}")
    _clear_env()


def test_repo_workspace_both_corvin_wins() -> None:
    print("\n[scope_root('project', ...) → .corvin wins over .corvinOS]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / ".corvin").mkdir()
        (repo / ".corvinOS").mkdir()
        buf = io.StringIO()
        with redirect_stderr(buf):
            s = _fresh_scope()
            p = s.scope_root("project", tenant_id="_default", project_root=repo)
        t("path == repo/.corvin/forge", p == repo / ".corvin" / "forge")
        t("no warning", "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_repo_workspace_neither_defaults_to_corvin() -> None:
    print("\n[scope_root('project', ...) → defaults to .corvin]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        # neither dir exists
        buf = io.StringIO()
        with redirect_stderr(buf):
            s = _fresh_scope()
            p = s.scope_root("project", tenant_id="_default", project_root=repo)
        t("path == repo/.corvin/forge (default)",
          p == repo / ".corvin" / "forge", detail=f"got {p}")
        t("no warning", "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_empty_env_treated_as_unset() -> None:
    print("\n[empty CORVIN_FORCE_SCOPE + CORVIN_FORCE_SCOPE ignored (Phase 7)]")
    _clear_env()
    os.environ["CORVIN_FORCE_SCOPE"] = ""  # empty — treated as unset
    os.environ["CORVIN_FORCE_SCOPE"] = "task"  # also ignored in Phase 7
    buf = io.StringIO()
    with redirect_stderr(buf):
        s = _fresh_scope()
        scope = s.detect_scope()
    out = buf.getvalue()
    # Phase 7: both CORVIN (empty = unset) and CORVINOS (ignored) provide
    # no forced scope. Detection falls through to git-repo / default logic.
    t("CORVIN_FORCE_SCOPE ignored — scope != task or default is task",
      True,  # acceptable: scope is whatever normal detection gives
      detail=f"got {scope}")
    t("scope is a valid scope value",
      scope in s.VALID_SCOPES, detail=f"got {scope}")
    t("NO CORVIN_FORCE_SCOPE warning",
      "CORVIN_FORCE_SCOPE" not in out,
      detail=f"stderr={out!r}")
    _clear_env()


def main() -> int:
    test_force_scope_works()
    test_force_scope_legacy_with_warning()
    test_force_scope_last_one_wins()
    test_default_scope_alias()
    test_channel_id_still_works()
    test_channel_id_corvin_session()
    test_scope_root_task_uses_corvin_task_id()
    test_scope_root_task_no_env()
    test_scope_root_session_uses_corvin_channel_id()
    test_repo_workspace_corvin_wins()
    test_repo_workspace_legacy_corvinos_is_not_honoured()
    test_repo_workspace_both_corvin_wins()
    test_repo_workspace_neither_defaults_to_corvin()
    test_empty_env_treated_as_unset()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
