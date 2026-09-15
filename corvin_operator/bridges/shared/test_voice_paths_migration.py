"""E2E: voice-shared modules resolve their state roots correctly.

Phase J.1.4b regression test, updated for the XDG-canonical fix (6776979,
CLAUDE.md-pinned). Two distinct contracts are asserted:

* ``vault`` (BYOK secrets) and ``memory`` (topic store) are **XDG-canonical**:
  they root under ``<XDG_CONFIG_HOME or ~/.config>/corvin-voice/`` and
  deliberately ignore ``CORVIN_HOME``/``voice_dir()``. The old voice_dir()
  fallback split the store between the console (XDG set) and the systemd
  ``--user`` bridge (XDG unset) — same reader!=writer bug as the voice profile.
* ``scheduler`` and the adapter ``sessions`` root remain tenant-scoped under
  ``voice_dir()`` (= ``$CORVIN_HOME/voice``).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _reset_env() -> None:
    """Clear every env that could shadow voice_dir() resolution."""
    for k in (
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
        "VOICE_CONFIG_DIR",
        "FORGE_ROOT",
    ):
        os.environ.pop(k, None)


def _purge_modules(*names: str) -> None:
    for n in names:
        sys.modules.pop(n, None)


def _xdg_voice_root() -> Path:
    """Mirror the canonical resolver: ``<XDG_CONFIG_HOME or ~/.config>/corvin-voice``."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(xdg) / "corvin-voice"


def test_vault_default_xdg_canonical() -> None:
    print("\n[vault default → <XDG_CONFIG_HOME or ~/.config>/corvin-voice/vault]")
    with tempfile.TemporaryDirectory() as td:
        _reset_env()
        # CORVIN_HOME is set to a tmp dir precisely to prove the vault IGNORES it
        # and stays XDG-canonical (the reader!=writer fix).
        os.environ["CORVIN_HOME"] = td
        _purge_modules("paths", "vault")
        import vault  # noqa: E402

        # vault.py resolves its path via a FUNCTION (_vault_dir()), not a
        # module-level constant — a module-level VAULT_DIR was itself a
        # path-audit-class bug (2026-07-14): computed once at import time,
        # a later env-var change (like the CORVIN_HOME override this test
        # sets) could never retarget it, so any long-lived process (or a
        # test suite importing vault.py before this test ran) got it wrong
        # for good. Calling the function on every check is the fix.
        cand = None
        if hasattr(vault, "_vault_dir"):
            cand = vault._vault_dir()
        elif hasattr(vault, "VAULT_DIR"):
            cand = vault.VAULT_DIR
        if cand is not None:
            t(
                "vault dir under XDG-canonical corvin-voice root",
                str(cand).startswith(str(_xdg_voice_root())),
                detail=f"got {cand}",
            )
        else:
            t(
                "vault module exposes a path resolver",
                False,
                detail="no _vault_dir() function / VAULT_DIR attribute",
            )
    os.environ.pop("CORVIN_HOME", None)


def test_memory_default_xdg_canonical() -> None:
    print("\n[memory default → <XDG_CONFIG_HOME or ~/.config>/corvin-voice/memory]")
    with tempfile.TemporaryDirectory() as td:
        _reset_env()
        os.environ["CORVIN_HOME"] = td
        _purge_modules("paths", "memory")
        import memory  # noqa: E402

        cand = (
            getattr(memory, "MEMORY_DIR", None)
            or getattr(memory, "_MEMORY_DIR", None)
        )
        if cand is not None:
            t(
                "memory dir under XDG-canonical corvin-voice root",
                str(cand).startswith(str(_xdg_voice_root())),
                detail=f"got {cand}",
            )
        else:
            t("memory module exposes path constant", False)
    os.environ.pop("CORVIN_HOME", None)


def test_scheduler_default() -> None:
    print("\n[scheduler default → voice_dir/schedule.json]")
    with tempfile.TemporaryDirectory() as td:
        _reset_env()
        os.environ["CORVIN_HOME"] = td
        _purge_modules("paths", "scheduler")
        import paths  # noqa: E402
        import scheduler  # noqa: E402

        cand = (
            getattr(scheduler, "SCHEDULE_FILE", None)
            or getattr(scheduler, "_SCHEDULE_FILE", None)
        )
        if cand is not None:
            t(
                "schedule file under voice_dir",
                str(cand).startswith(str(paths.voice_dir())),
                detail=f"got {cand}",
            )
        else:
            t("scheduler module exposes path constant", False)
    os.environ.pop("CORVIN_HOME", None)


def test_adapter_sessions_default() -> None:
    print("\n[adapter sessions → voice_dir/sessions]")
    with tempfile.TemporaryDirectory() as td:
        _reset_env()
        os.environ["CORVIN_HOME"] = td
        # adapter pulls in router / forge — purge them too so the env
        # change actually re-resolves the SESSIONS_ROOT constant.
        _purge_modules("paths", "adapter", "router", "router_embedding", "audit")
        import paths  # noqa: E402
        import adapter  # noqa: E402

        cand = (
            getattr(adapter, "SESSIONS_ROOT", None)
            or getattr(adapter, "SESSIONS_DIR", None)
        )
        if cand is not None:
            t(
                "sessions dir under voice_dir",
                str(cand).startswith(str(paths.voice_dir())),
                detail=f"got {cand}",
            )
        else:
            t("adapter exposes sessions path", False)
    os.environ.pop("CORVIN_HOME", None)


def main() -> int:
    test_vault_default_xdg_canonical()
    test_memory_default_xdg_canonical()
    test_scheduler_default()
    test_adapter_sessions_default()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
