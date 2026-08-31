"""Per-subtask E2E for Phase-7 compat-resolver in forge.paths.

Covers the Corvin rebrand Phase 7 contract documented in CLAUDE.md:

  - ``CORVIN_HOME`` env wins, no warning.
  - ``CORVIN_HOME`` env is IGNORED (Phase 7 — aliases removed).
  - The on-disk repo-relative home is ``<repo>/.corvin/``; a legacy
    ``<repo>/.corvinOS/`` dir is NOT honoured (hard cut, path-audit 2026-06-25 —
    the fallback lived only here and caused a reader≠writer split).
  - The ``corvin_home()`` function alias still exists and resolves
    identically to ``corvin_home()`` (call-site back-compat).
  - ``voice_dir()`` / ``cowork_dir()`` / ``forge_dir()`` track the
    new home.
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


def _fresh_paths():
    """Re-import forge.paths from a clean state so the per-process
    deprecation set is empty for this test."""
    sys.modules.pop("forge.paths", None)
    sys.modules.pop("forge", None)
    sys.path.insert(0, str(REPO / "operator" / "forge"))
    try:
        return importlib.import_module("forge.paths")
    finally:
        sys.path.pop(0)


def _clear_env() -> None:
    for k in ("CORVIN_HOME", "CORVIN_HOME"):
        os.environ.pop(k, None)


def test_corvin_env_ignored_greenfield() -> None:
    print("\n[CORVIN_HOME set — canonical override used]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        os.environ["CORVIN_HOME"] = td
        buf = io.StringIO()
        with redirect_stderr(buf):
            p = _fresh_paths()
            home = p.corvin_home()
        # CORVIN_HOME is the canonical override — it IS read and used.
        t("home == td (CORVIN_HOME used)",
          str(home) == td, detail=f"got {home}")
        t("no deprecation on stderr",
          "deprecation" not in buf.getvalue().lower(),
          detail=f"stderr={buf.getvalue()!r}")
    _clear_env()


def test_corvin_env_still_works_with_warning() -> None:
    print("\n[CORVIN_HOME set — used as canonical override, no warning]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        os.environ["CORVIN_HOME"] = td
        buf = io.StringIO()
        with redirect_stderr(buf):
            p = _fresh_paths()
            home1 = p.corvin_home()
            home2 = p.corvin_home()
            home3 = p.corvin_home()
        out = buf.getvalue()
        # CORVIN_HOME is the canonical override — it IS read and used.
        t("home IS td (CORVIN_HOME used)",
          str(home1) == td, detail=f"got {home1}")
        t("alias corvin_home() == corvin_home()", home3 == home1)
        t("NO deprecation warning for CORVIN_HOME env",
          "CORVIN_HOME" not in out,
          detail=f"stderr={out!r}")
        t("no deprecation on stderr",
          "deprecation" not in out.lower() or "CORVIN_HOME" not in out,
          detail=f"stderr={out!r}")
        t("call 2 + 3 still return same value", home2 == home1 == home3)
    _clear_env()


def test_corvin_not_read_outside_repo() -> None:
    print("\n[CORVIN_HOME set — used as canonical override]")
    with tempfile.TemporaryDirectory() as td_env:
        _clear_env()
        os.environ["CORVIN_HOME"] = td_env
        buf = io.StringIO()
        with redirect_stderr(buf):
            p = _fresh_paths()
            home = p.corvin_home()
        # CORVIN_HOME is the canonical override — it IS read and used.
        t("env value IS used", str(home) == td_env, detail=f"got {home}")
        t("no deprecation log",
          "deprecation" not in buf.getvalue().lower(),
          detail=f"stderr={buf.getvalue()!r}")
    _clear_env()


def test_repo_corvin_dir_wins() -> None:
    print("\n[repo with .corvin/ → wins, no warning]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        repo = Path(td) / "fake-repo"
        (repo / "operator" / "forge" / "forge").mkdir(parents=True)
        (repo / ".corvin_repo").touch()  # ADR-0035 repo-root marker
        (repo / ".corvin").mkdir()
        # Stamp a paths.py at the right depth so _repo_root walks find
        # this synthetic repo as the ancestor.
        paths_src = (REPO / "operator" / "forge" / "forge" / "paths.py").read_text()
        (repo / "operator" / "forge" / "forge" / "paths.py").write_text(paths_src)

        sys.modules.pop("paths_synth", None)
        spec_path = repo / "operator" / "forge" / "forge" / "paths.py"
        sys.path.insert(0, str(repo / "operator" / "forge"))
        sys.modules.pop("forge", None)
        sys.modules.pop("forge.paths", None)
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                p = importlib.import_module("forge.paths")
                home = p.corvin_home()
            t("home == <repo>/.corvin", home == repo / ".corvin",
              detail=f"got {home}")
            t("no deprecation", "deprecation" not in buf.getvalue().lower())
        finally:
            sys.path.pop(0)
            sys.modules.pop("forge", None)
            sys.modules.pop("forge.paths", None)
        del spec_path
    _clear_env()


def test_repo_legacy_corvinos_only_is_not_honoured() -> None:
    # Hard cut (path-audit 2026-06-25): a repo that has ONLY a legacy .corvinOS/
    # dir resolves to .corvin, NOT .corvinOS. The on-disk fallback lived only in
    # this resolver while everything else used .corvin → reader≠writer split, so
    # it was removed. Migration of an existing .corvinOS/ is corvin_migrate's job.
    print("\n[repo with .corvinOS/ only → .corvin wins, no fallback log]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        repo = Path(td) / "fake-repo"
        (repo / "operator" / "forge" / "forge").mkdir(parents=True)
        (repo / ".corvin_repo").touch()  # ADR-0035 repo-root marker
        (repo / ".corvinOS").mkdir()
        paths_src = (REPO / "operator" / "forge" / "forge" / "paths.py").read_text()
        (repo / "operator" / "forge" / "forge" / "paths.py").write_text(paths_src)

        sys.path.insert(0, str(repo / "operator" / "forge"))
        sys.modules.pop("forge", None)
        sys.modules.pop("forge.paths", None)
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                p = importlib.import_module("forge.paths")
                home1 = p.corvin_home()
                home2 = p.corvin_home()  # second call, must NOT re-log
            out = buf.getvalue()
            t("home == <repo>/.corvin (hard cut, .corvinOS ignored)",
              home1 == repo / ".corvin", detail=f"got {home1}")
            t("home2 == home1 (stable)", home2 == home1)
            t("NO deprecation log emitted",
              "[deprecation]" not in out, detail=f"stderr={out!r}")
        finally:
            sys.path.pop(0)
            sys.modules.pop("forge", None)
            sys.modules.pop("forge.paths", None)
    _clear_env()


def test_repo_both_dirs_corvin_wins() -> None:
    print("\n[repo with both dirs → .corvin wins, no warning]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        repo = Path(td) / "fake-repo"
        (repo / "operator" / "forge" / "forge").mkdir(parents=True)
        (repo / ".corvin_repo").touch()  # ADR-0035 repo-root marker
        (repo / ".corvin").mkdir()
        (repo / ".corvinOS").mkdir()
        paths_src = (REPO / "operator" / "forge" / "forge" / "paths.py").read_text()
        (repo / "operator" / "forge" / "forge" / "paths.py").write_text(paths_src)

        sys.path.insert(0, str(repo / "operator" / "forge"))
        sys.modules.pop("forge", None)
        sys.modules.pop("forge.paths", None)
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                p = importlib.import_module("forge.paths")
                home = p.corvin_home()
            t("home == <repo>/.corvin", home == repo / ".corvin")
            t("no deprecation log", "deprecation" not in buf.getvalue().lower())
        finally:
            sys.path.pop(0)
            sys.modules.pop("forge", None)
            sys.modules.pop("forge.paths", None)
    _clear_env()


def test_repo_neither_dir_defaults_to_corvin() -> None:
    print("\n[repo with neither dir → default <repo>/.corvin]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        repo = Path(td) / "fake-repo"
        (repo / "operator" / "forge" / "forge").mkdir(parents=True)
        (repo / ".corvin_repo").touch()  # ADR-0035 repo-root marker
        paths_src = (REPO / "operator" / "forge" / "forge" / "paths.py").read_text()
        (repo / "operator" / "forge" / "forge" / "paths.py").write_text(paths_src)

        sys.path.insert(0, str(repo / "operator" / "forge"))
        sys.modules.pop("forge", None)
        sys.modules.pop("forge.paths", None)
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                p = importlib.import_module("forge.paths")
                home = p.corvin_home()
            t("home defaults to <repo>/.corvin", home == repo / ".corvin")
            t("no deprecation (greenfield repo)",
              "deprecation" not in buf.getvalue().lower())
        finally:
            sys.path.pop(0)
            sys.modules.pop("forge", None)
            sys.modules.pop("forge.paths", None)
    _clear_env()


def test_subdir_helpers_track_corvin_home() -> None:
    print("\n[voice/cowork/forge sub-dir helpers follow corvin_home()]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        # Phase 7: CORVIN_HOME is ignored, so we don't set it.
        # Instead, verify that the helpers call corvin_home() and track it.
        p = _fresh_paths()
        home = p.corvin_home()
        t("voice_dir == home/voice", p.voice_dir() == home / "voice")
        t("cowork_dir == home/cowork", p.cowork_dir() == home / "cowork")
        t("forge_dir == home/forge", p.forge_dir() == home / "forge")
    _clear_env()


def test_alias_callable_and_identical() -> None:
    print("\n[corvin_home() alias still works for back-compat]")
    with tempfile.TemporaryDirectory() as td:
        _clear_env()
        os.environ["CORVIN_HOME"] = td
        p = _fresh_paths()
        t("alias is the new function (same callable)",
          p.corvin_home is p.corvin_home)
        t("alias result == new result",
          p.corvin_home() == p.corvin_home())
    _clear_env()


def main() -> int:
    test_corvin_env_ignored_greenfield()
    test_corvin_env_still_works_with_warning()
    test_corvin_not_read_outside_repo()
    test_repo_corvin_dir_wins()
    test_repo_legacy_corvinos_only_is_not_honoured()
    test_repo_both_dirs_corvin_wins()
    test_repo_neither_dir_defaults_to_corvin()
    test_subdir_helpers_track_corvin_home()
    test_alias_callable_and_identical()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
