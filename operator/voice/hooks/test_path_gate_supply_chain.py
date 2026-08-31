"""Per-subtask E2E — ADR-0021 Path-Gate-Extension (Phase 31.4 + 31.6).

Covers the path-gate extension that protects:
  * <corvin_home>/global/supply_chain.yaml + .yml + .json
  * <corvin_home>/global/supply_chain/* (cache dir)
  * plugins/*/requirements.txt
  * plugins/*/package-lock.json
  * plugins/*/sbom.cdx.json
  * plugins/*/plugin.corvin.yaml

Plus Bash-hint detection for fail-closed on ambiguous commands
that reference these files.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "voice" / "hooks"))

import path_gate as pg

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


# ---------------------------------------------------------------------------
# Section 1 — Corvin-home supply_chain tree protection
# ---------------------------------------------------------------------------


def section_corvin_home() -> None:
    print("\n[1/3] Corvin-home supply_chain tree")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            # supply_chain.yaml at corvin-home root → protected
            p = Path(tmp) / "global" / "supply_chain.yaml"
            p.parent.mkdir(parents=True)
            p.write_text("k: v")
            t("supply_chain.yaml protected",
              pg.is_protected_path(str(p)))

            # subdir under global/supply_chain/ → protected
            cache = Path(tmp) / "global" / "supply_chain" / "last_critical.json"
            cache.parent.mkdir(exist_ok=True)
            cache.write_text("{}")
            t("supply_chain/<file> protected",
              pg.is_protected_path(str(cache)))

            # Non-supply-chain file under global/ → NOT protected
            ok_file = Path(tmp) / "global" / "harmless.txt"
            ok_file.write_text("ok")
            t("harmless file under global not protected",
              not pg.is_protected_path(str(ok_file)))
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 2 — Plugin-tree manifest + lockfile + SBOM protection
# ---------------------------------------------------------------------------


def section_plugin_tree() -> None:
    print("\n[2/3] Plugin-tree files")
    # Use the actual repo's plugin tree so the repo-resolver finds it
    plugins_dir = REPO / "plugins"
    for plug in ("corvin-gateway", "corvin-delegate", "voice", "forge"):
        # Test the *would-be* paths (we don't write into the live tree)
        for name in ("requirements.txt", "package-lock.json",
                     "sbom.cdx.json", "plugin.corvin.yaml"):
            p = plugins_dir / plug / name
            t(f"{plug}/{name} protected",
              pg.is_protected_path(str(p)))


# ---------------------------------------------------------------------------
# Section 3 — Bash hint detection
# ---------------------------------------------------------------------------


def section_bash_hints() -> None:
    print("\n[3/3] Bash hint detection (fail-closed)")
    hints = [
        ("eval \"echo > requirements.txt\"", "requirements.txt"),
        ("xargs sed -i s/x/y/ package-lock.json", "package-lock.json"),
        ("cat sbom.cdx.json | tee log", "sbom.cdx.json"),
        ("cat plugin.corvin.yaml | $(date)", "plugin.corvin.yaml"),
        ("$(cat supply_chain.yaml)", "supply_chain.yaml"),
    ]
    for cmd, hint in hints:
        t(f"contains hint '{hint}'",
          pg._looks_protected(cmd),
          detail=cmd[:50])

    # Benign command without hint → no fail-closed trigger
    t("benign command does NOT trigger hint",
      not pg._looks_protected("echo hello world"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("test_path_gate_supply_chain.py — ADR-0021 path-gate extension")
    print("=" * 60)
    section_corvin_home()
    section_plugin_tree()
    section_bash_hints()
    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
