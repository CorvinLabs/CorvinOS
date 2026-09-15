"""E2E: corvin_home() resolution + sub-dir helpers across all three plugins."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PASS = 0; FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — '+detail) if detail else ''}")
    if ok: PASS += 1
    else: FAIL += 1


def _import(modpath_dir):
    sys.path.insert(0, str(modpath_dir))
    if "paths" in sys.modules: del sys.modules["paths"]
    import paths
    sys.path.pop(0)
    return paths


def test_default_resolution():
    print("\n[default — repo-root discovery]")
    os.environ.pop("CORVIN_HOME", None)
    p = _import(REPO / "operator" / "bridges" / "shared")
    home = p.corvin_home()
    # Post-rebrand: resolver prefers .corvin, falls back to .corvinOS.
    t("ends with /.corvin or /.corvinOS",
      home.name in (".corvin", ".corvinOS"),
      detail=f"got {home.name}")
    t("parent has repo marker (.corvin_repo or legacy plugins/)",
      (home.parent / ".corvin_repo").exists() or (home.parent / "plugins").is_dir())
    t("voice_dir ends /voice", p.voice_dir().name == "voice")
    t("cowork_dir ends /cowork", p.cowork_dir().name == "cowork")
    t("forge_dir ends /forge", p.forge_dir().name == "forge")


def test_env_override():
    print("\n[CORVIN_HOME override]")
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_HOME"] = td
        p = _import(REPO / "operator" / "bridges" / "shared")
        t("home == td", str(p.corvin_home()) == td)
        t("voice_dir ends with /voice", p.voice_dir().name == "voice")
    os.environ.pop("CORVIN_HOME", None)


def test_env_override_whitespace():
    print("\n[CORVIN_HOME whitespace-only override falls back to default]")
    os.environ.pop("CORVIN_HOME", None)
    p = _import(REPO / "operator" / "bridges" / "shared")
    default_home = p.corvin_home()
    for bogus in (" ", "   ", "\t", "\n"):
        os.environ["CORVIN_HOME"] = bogus
        p = _import(REPO / "operator" / "bridges" / "shared")
        home = p.corvin_home()
        t(f"CORVIN_HOME={bogus!r} does not resolve to Path({bogus!r})",
          home != Path(bogus), detail=f"got {home}")
        t(f"CORVIN_HOME={bogus!r} falls back to the same default as unset",
          home == default_home, detail=f"got {home}, expected {default_home}")
    os.environ.pop("CORVIN_HOME", None)


def test_all_three_plugins_agree():
    print("\n[all three plugins resolve to same root]")
    os.environ.pop("CORVIN_HOME", None)
    voice_p = _import(REPO / "operator" / "bridges" / "shared")
    cowork_p = _import(REPO / "operator" / "cowork" / "lib")
    forge_p = _import(REPO / "operator" / "forge" / "forge")
    t("voice == cowork", voice_p.corvin_home() == cowork_p.corvin_home())
    t("cowork == forge", cowork_p.corvin_home() == forge_p.corvin_home())


def main() -> int:
    test_default_resolution()
    test_env_override()
    test_env_override_whitespace()
    test_all_three_plugins_agree()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
