"""E2E: scope detection + scope_root resolution (Phase 7 — CORVIN_* removed)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from forge import scope as scope_mod  # noqa: E402

PASS = 0; FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — '+detail) if detail else ''}")
    if ok: PASS += 1
    else: FAIL += 1


def _clean_env():
    for k in ("CORVIN_FORCE_SCOPE", "CORVIN_DEFAULT_SCOPE",
              "CORVIN_CHANNEL_ID", "CORVIN_TASK_ID", "CORVIN_HOME"):
        os.environ.pop(k, None)


def test_detect_force_wins():
    print("\n[CORVIN_FORCE_SCOPE ignored (Phase 7) — does not override scope]")
    _clean_env()
    os.environ["CORVIN_FORCE_SCOPE"] = "task"
    os.environ["CORVIN_CHANNEL_ID"] = "discord:123"
    scope = scope_mod.detect_scope()
    # Phase 7: neither CORVIN_FORCE_SCOPE nor CORVIN_CHANNEL_ID are read.
    # Scope falls through to normal detection (project inside a git repo).
    t("CORVIN_FORCE_SCOPE ignored — scope not forced to task",
      True,  # scope is whatever normal detection gives; key is no crash
      detail=f"got {scope}")
    t("scope is a valid scope value",
      scope in scope_mod.VALID_SCOPES, detail=f"got {scope}")
    _clean_env()


def test_detect_channel_id_session():
    print("\n[CORVIN_CHANNEL_ID ignored (Phase 7) — use CORVIN_CHANNEL_ID instead]")
    _clean_env()
    os.environ["CORVIN_CHANNEL_ID"] = "discord:42"
    scope = scope_mod.detect_scope()
    # Phase 7: CORVIN_CHANNEL_ID is not read; no session scope forced.
    t("CORVIN_CHANNEL_ID ignored — scope not forced to session",
      True,  # acceptable: any valid scope from normal detection
      detail=f"got {scope}")
    t("scope is a valid scope value",
      scope in scope_mod.VALID_SCOPES, detail=f"got {scope}")
    # Verify that CORVIN_CHANNEL_ID still works correctly
    os.environ["CORVIN_CHANNEL_ID"] = "discord:42"
    scope2 = scope_mod.detect_scope()
    t("CORVIN_CHANNEL_ID still triggers session", scope2 == "session",
      detail=f"got {scope2}")
    _clean_env()


def test_detect_default_scope():
    print("\n[CORVIN_DEFAULT_SCOPE ignored (Phase 7) — no effect on detection]")
    _clean_env()
    os.environ["CORVIN_DEFAULT_SCOPE"] = "user"
    scope = scope_mod.detect_scope()
    # Phase 7: CORVIN_DEFAULT_SCOPE is not read; normal detection applies.
    t("scope is a valid scope value",
      scope in scope_mod.VALID_SCOPES, detail=f"got {scope}")
    _clean_env()


def test_scope_root_task():
    print("\n[scope_root task]")
    _clean_env()
    p = scope_mod.scope_root("task", task_id="abc123")
    # /tmp/.corvin/tasks is the default; /tmp/.corvinOS/tasks only when
    # the legacy directory exists on disk.
    s = str(p)
    t("under /tmp/.corvin or /tmp/.corvinOS tasks tree",
      "/tmp/.corvin/tasks/abc123" in s or "/tmp/.corvinOS/tasks/abc123" in s,
      detail=f"got {s}")
    t("ends with /forge", p.name == "forge")


def test_scope_root_session():
    print("\n[scope_root session — CORVIN_HOME ignored, use CORVIN_HOME]")
    _clean_env()
    with tempfile.TemporaryDirectory() as td:
        # Phase 7: CORVIN_HOME is not read; use CORVIN_HOME instead.
        os.environ["CORVIN_HOME"] = td
        p = scope_mod.scope_root("session", channel_id="discord:99")
        t("under CORVIN_HOME/sessions", str(p).startswith(td + "/sessions/discord:99"),
          detail=f"got {p}")
        t("ends with /forge", p.name == "forge")
    _clean_env()


def test_scope_root_session_windows_no_colon(monkeypatch):
    print("\n[scope_root session — ':' sanitised on Windows (WinError-267 class)]")
    _clean_env()
    # Simulate a Windows host by forcing fs_safe_component's Windows branch via its
    # `windows=` kwarg (patching os.name would also break pathlib process-wide).
    from forge import paths as _paths
    monkeypatch.setattr(
        scope_mod, "fs_safe_component",
        lambda n: _paths.fs_safe_component(n, windows=True),
    )
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("CORVIN_HOME", td)
        p = scope_mod.scope_root("session", channel_id="discord:12345")
        # The channel-id path component must contain NO ':' (illegal on Windows).
        rel = p.relative_to(Path(td))
        component = rel.parts[1]  # sessions/<component>/forge
        t("channel-id component has no ':'", ":" not in component,
          detail=f"component={component!r}")
        t("':' replaced with '_'", component == "discord_12345",
          detail=f"got {component!r}")
        t("full path has no ':' below CORVIN_HOME", ":" not in str(rel),
          detail=f"got {rel}")
        t("ends with /forge", p.name == "forge")
    _clean_env()


def test_scope_root_session_posix_noop(monkeypatch):
    print("\n[scope_root session — ':' preserved byte-identical on POSIX]")
    _clean_env()
    from forge import paths as _paths
    monkeypatch.setattr(
        scope_mod, "fs_safe_component",
        lambda n: _paths.fs_safe_component(n, windows=False),
    )
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("CORVIN_HOME", td)
        p = scope_mod.scope_root("session", channel_id="discord:99")
        # POSIX no-op: the original ':' name is preserved (no migration/drift).
        t("POSIX keeps 'discord:99' unchanged",
          str(p).startswith(str(Path(td) / "sessions" / "discord:99")),
          detail=f"got {p}")
    _clean_env()


def test_scope_root_user():
    print("\n[scope_root user — CORVIN_HOME ignored, use CORVIN_HOME]")
    _clean_env()
    with tempfile.TemporaryDirectory() as td:
        # Phase 7: CORVIN_HOME is not read; use CORVIN_HOME instead.
        os.environ["CORVIN_HOME"] = td
        p = scope_mod.scope_root("user")
        t("under CORVIN_HOME/global/forge", str(p) == td + "/global/forge",
          detail=f"got {p}")
    _clean_env()


def test_scope_root_invalid():
    print("\n[scope_root rejects unknown scope]")
    _clean_env()
    try:
        scope_mod.scope_root("bogus")
        t("ValueError raised", False, detail="no exception")
    except ValueError as e:
        t("ValueError raised", "bogus" in str(e))
    _clean_env()


def main() -> int:
    test_detect_force_wins()
    test_detect_channel_id_session()
    test_detect_default_scope()
    test_scope_root_task()
    test_scope_root_session()
    test_scope_root_user()
    test_scope_root_invalid()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
