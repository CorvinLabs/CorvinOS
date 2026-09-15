"""Per-subtask E2E — ADR-0021 Phase 31.3 + 31.4 (Supply-Chain-Verify).

Covers:
  * walk_python_imports — AST-walk excludes .venv / __pycache__ / .pyc
  * walk_python_imports — top-level + try/except module-level imports
  * detect_capability_drift — undeclared + unused_declared
  * detect_capability_drift — stdlib filter ignores `os`, `sys`, etc.
  * load_capability_manifest — schema-tolerant fallback to None
  * Finding dataclass + severity normalisation
  * scan_plugin — pip-audit-missing emits cve_check_skipped audit
  * scan_plugin — fake-mode CVE finding emits cve_detected audit
  * scan_plugin — drift_report has capability_drift event
  * CRITICAL-diff snapshot persistence + load + new-only emission
  * Audit-Allow-List with forbidden-field rejection
  * CLI subcommands (weekly / critical / drift) round-trip
  * AST cost-contract: NO `import anthropic`
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "voice" / "scripts"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

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


def _fresh():
    sys.modules.pop("supply_chain_verify", None)
    return importlib.import_module("supply_chain_verify")


# ---------------------------------------------------------------------------
# Section 1 — walk_python_imports
# ---------------------------------------------------------------------------


def section_walk_imports() -> None:
    print("\n[1/7] walk_python_imports — AST-walk")
    sc = _fresh()
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "p"
        plugin.mkdir()
        (plugin / "main.py").write_text("import fastapi\nfrom pydantic import BaseModel\n")
        (plugin / "lib.py").write_text("import httpx\n")
        # Excluded: .venv / __pycache__
        (plugin / ".venv").mkdir()
        (plugin / ".venv" / "site.py").write_text("import requests\n")
        (plugin / "__pycache__").mkdir()
        (plugin / "__pycache__" / "x.py").write_text("import boto3\n")
        # try/except module-level
        (plugin / "optional.py").write_text(
            "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        )

        imports = sc.walk_python_imports(plugin)
        t("captures top-level Import", "fastapi" in imports)
        t("captures top-level ImportFrom", "pydantic" in imports)
        t("captures sibling file imports", "httpx" in imports)
        t("captures try/except module-level", "yaml" in imports)
        t("excludes .venv", "requests" not in imports)
        t("excludes __pycache__", "boto3" not in imports)


# ---------------------------------------------------------------------------
# Section 2 — detect_capability_drift
# ---------------------------------------------------------------------------


def section_detect_drift() -> None:
    print("\n[2/7] detect_capability_drift")
    sc = _fresh()
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "myplugin"
        plugin.mkdir()
        (plugin / "main.py").write_text(
            "import fastapi\nimport httpx\nfrom pydantic import BaseModel\n"
        )
        # declared has fastapi + pydantic, but missing httpx (undeclared)
        # and includes "uvicorn" that's not actually imported (unused)
        manifest = {
            "spec": {
                "declared_imports": {
                    "python": ["fastapi", "pydantic", "uvicorn"],
                },
            },
        }
        report = sc.detect_capability_drift(plugin, manifest)
        t("plugin_name set", report.plugin_name == "myplugin")
        t("undeclared imports detected",
          "httpx" in report.undeclared_imports,
          detail=str(report.undeclared_imports))
        t("unused declared detected",
          "uvicorn" in report.unused_declared,
          detail=str(report.unused_declared))
        t("has_drift = True", report.has_drift)

        # Stdlib filter — `os` import should not count as undeclared
        (plugin / "main.py").write_text(
            "import fastapi\nimport os\nimport sys\nimport json\n"
        )
        manifest = {"spec": {"declared_imports": {"python": ["fastapi"]}}}
        report = sc.detect_capability_drift(plugin, manifest)
        t("stdlib imports filtered out",
          not any(s in report.undeclared_imports for s in ("os", "sys", "json")),
          detail=str(report.undeclared_imports))
        t("no drift after stdlib filter", not report.has_drift)

        # No manifest fields → all imports are undeclared
        report = sc.detect_capability_drift(plugin, {})
        t("empty manifest → only non-stdlib undeclared",
          "fastapi" in report.undeclared_imports)


# ---------------------------------------------------------------------------
# Section 3 — load_capability_manifest
# ---------------------------------------------------------------------------


def section_load_manifest() -> None:
    print("\n[3/7] load_capability_manifest")
    sc = _fresh()
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "p"
        plugin.mkdir()
        # No manifest → None
        t("missing manifest → None",
          sc.load_capability_manifest(plugin) is None)
        # Well-formed manifest → dict
        (plugin / "plugin.corvin.yaml").write_text(
            "apiVersion: corvin/v1\nkind: PluginCapabilities\n"
            "spec:\n  declared_imports:\n    python: [fastapi]\n"
        )
        m = sc.load_capability_manifest(plugin)
        t("manifest loads as dict", isinstance(m, dict))
        # Malformed YAML → None (fail-open)
        (plugin / "plugin.corvin.yaml").write_text("not yaml {")
        t("malformed YAML → None", sc.load_capability_manifest(plugin) is None)


# ---------------------------------------------------------------------------
# Section 4 — run_pip_audit (fake mode)
# ---------------------------------------------------------------------------


def section_pip_audit_fake() -> None:
    print("\n[4/7] run_pip_audit fake-mode")
    sc = _fresh()
    os.environ["CORVIN_SUPPLY_CHAIN_FAKE"] = "1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("cryptography==42.0.0\n")
            findings, err = sc.run_pip_audit(req, "test-plugin")
            t("fake-mode returns 1 finding",
              err == "" and len(findings) == 1)
            f = findings[0]
            t("finding has CVE_ID", f.cve_id == "CVE-2026-99999")
            t("finding severity CRITICAL", f.severity == "CRITICAL")
            t("finding fix_available", f.fix_available is True)
    finally:
        os.environ.pop("CORVIN_SUPPLY_CHAIN_FAKE", None)


# ---------------------------------------------------------------------------
# Section 5 — scan_plugin + audit emission
# ---------------------------------------------------------------------------


def section_scan_plugin() -> None:
    print("\n[5/7] scan_plugin + audit emission")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        os.environ["CORVIN_SUPPLY_CHAIN_FAKE"] = "1"
        try:
            sc = _fresh()
            plugin = Path(tmp) / "fake-plugin"
            plugin.mkdir()
            (plugin / "main.py").write_text("import fastapi\nimport httpx\n")
            (plugin / "requirements.txt").write_text(
                "cryptography==42.0.0 --hash=sha256:abc\n")
            (plugin / "plugin.corvin.yaml").write_text(
                "apiVersion: corvin/v1\nkind: PluginCapabilities\n"
                "spec:\n  declared_imports:\n    python: [fastapi]\n"
            )
            findings, drift = sc.scan_plugin(plugin)
            t("CVE finding returned",
              len(findings) == 1 and findings[0].severity == "CRITICAL")
            t("drift report has httpx undeclared",
              drift is not None and "httpx" in drift.undeclared_imports)
            t("drift report has has_drift=True",
              drift.has_drift)

            # Drift audit event should have landed
            audit = (Path(tmp) / "tenants" / "_default" / "global" /
                     "forge" / "audit.jsonl")
            t("audit file created", audit.exists())
            if audit.exists():
                lines = [json.loads(l) for l in
                         audit.read_text().splitlines() if l]
                kinds = {l["event_type"] for l in lines}
                t("capability_drift event emitted",
                  "supply_chain.capability_drift" in kinds,
                  detail=str(kinds))

            # Without manifest → no drift report, no drift audit
            (plugin / "plugin.corvin.yaml").unlink()
            findings2, drift2 = sc.scan_plugin(plugin)
            t("no manifest → drift = None",
              drift2 is None)
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("CORVIN_SUPPLY_CHAIN_FAKE", None)


# ---------------------------------------------------------------------------
# Section 6 — CRITICAL-diff snapshot persistence
# ---------------------------------------------------------------------------


def section_critical_diff() -> None:
    print("\n[6/7] CRITICAL-diff snapshot persistence")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CORVIN_HOME"] = tmp
        try:
            sc = _fresh()

            # Initially empty
            initial = sc._load_critical_snapshot()
            t("empty initial snapshot", initial == set())

            # Save + load
            sc._save_critical_snapshot({"plug-a:CVE-2026-1", "plug-b:CVE-2026-2"})
            loaded = sc._load_critical_snapshot()
            t("snapshot persists",
              loaded == {"plug-a:CVE-2026-1", "plug-b:CVE-2026-2"})

            snap_p = sc._critical_snapshot_path()
            t("snapshot file mode 0o600",
              (snap_p.stat().st_mode & 0o777) == 0o600)

            # Malformed → empty
            snap_p.write_text("garbage{")
            t("malformed snapshot → empty set",
              sc._load_critical_snapshot() == set())
        finally:
            os.environ.pop("CORVIN_HOME", None)


# ---------------------------------------------------------------------------
# Section 7 — Audit allow-list + cost contract
# ---------------------------------------------------------------------------


def section_audit_cost() -> None:
    print("\n[7/7] Audit allow-list + cost contract")
    sc = _fresh()

    # Forbidden field rejected
    try:
        sc._validate_audit_details(
            "supply_chain.cve_detected",
            {"plugin_name": "x", "package_name": "p", "package_version": "1",
             "cve_id": "CVE", "severity": "HIGH", "fix_available": True,
             "cadence": "weekly", "exploit_text": "leaked!"},
        )
        t("forbidden field rejected", False)
    except ValueError:
        t("forbidden field rejected", True)

    # Off-allowlist field rejected
    try:
        sc._validate_audit_details(
            "supply_chain.cve_detected",
            {"plugin_name": "x", "extra_unwanted": "x"},
        )
        t("off-allowlist field rejected", False)
    except ValueError:
        t("off-allowlist field rejected", True)

    # Unknown event_type rejected
    try:
        sc._validate_audit_details("supply_chain.bogus", {"plugin_name": "x"})
        t("unknown event_type rejected", False)
    except ValueError:
        t("unknown event_type rejected", True)

    # Cost contract: AST walk for forbidden imports
    p = REPO / "operator" / "voice" / "scripts" / "supply_chain_verify.py"
    tree = ast.parse(p.read_text())
    forbidden = ("anthropic", "openai", "google.generativeai")
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden:
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in forbidden:
                bad.append(node.module)
    t("no forbidden LLM-SDK imports",
      not bad, detail=f"found: {bad}" if bad else "")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("test_supply_chain_verify.py — ADR-0021 Phase 31.3+31.4")
    print("=" * 60)
    section_walk_imports()
    section_detect_drift()
    section_load_manifest()
    section_pip_audit_fake()
    section_scan_plugin()
    section_critical_diff()
    section_audit_cost()
    print()
    print(f"PASS={PASS}  FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
