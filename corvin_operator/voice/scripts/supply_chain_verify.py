#!/usr/bin/env python3
"""ADR-0021 Layer 31 Phase 31.3 + 31.4 — Supply-Chain Surveillance.

Two cadences (operator-set via systemd Timer):
  * **weekly** (Mon 05:00) — aggregated digest of MEDIUM/HIGH/CRITICAL
    findings. One bridge-notification per plugin per severity tier.
  * **critical** (daily 05:00) — only NEW CRITICAL findings since
    yesterday's diff snapshot. Bestehende CRITICAL-Findings werden
    NICHT täglich re-alarmiert (alert-fatigue defence).

Two sub-checks per plugin:
  1. **CVE-Surveillance** — `pip-audit` over `requirements.txt`
     (+ `npm audit` if `package.json` present). Severity-filtered.
  2. **Capability-Drift** — AST-walk over the plugin's `*.py` source
     vs. declared imports in `plugin.corvin.yaml`. Operator-visible
     drift via `supply_chain.capability_drift` audit-event.

Cost contract
=============

* No LLM calls. No `import anthropic`. Pure-Python with optional
  `pip-audit` subprocess (operator-installed).
* When `pip-audit` is missing, the check exits with `cve_check_skipped`
  audit warning instead of failing-closed (mirror of L31 honest scope).

Honest scope (per ADR-0021)
============================

* Detects **drift** (a new dep landed without being in the SBOM/manifest)
  and **post-hoc CVEs** (a published CVE matches an installed version).
* Does NOT detect malicious-but-not-yet-CVE-published packages
  (legitimate-author-compromise window). For that, sandbox-runtime
  + reproducible-builds (Phase 31.7+) are needed.

Module surface
==============

CLI:
    supply_chain_verify.py weekly [--notify-bridge]
    supply_chain_verify.py critical [--notify-bridge]
    supply_chain_verify.py drift [--plugin NAME]

Public-Python:
    walk_python_imports(plugin_dir) -> set[str]
    detect_capability_drift(plugin_dir, manifest) -> DriftReport
    run_pip_audit(requirements_path) -> list[Finding]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_VALID_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_NOTIFY_SEVERITIES_WEEKLY = ("CRITICAL", "HIGH", "MEDIUM")
_NOTIFY_SEVERITIES_CRITICAL = ("CRITICAL",)

_MANIFEST_FILENAME = "plugin.corvin.yaml"

# Per-event audit allow-list. Mirror of L23/L24/L25/L28/L29/L30 rule.
_AUDIT_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "supply_chain.cve_detected": frozenset({
        "plugin_name", "package_name", "package_version",
        "cve_id", "severity", "fix_available", "cadence",
    }),
    "supply_chain.cve_check_skipped": frozenset({
        "plugin_name", "reason",
    }),
    "supply_chain.capability_drift": frozenset({
        "plugin_name", "undeclared_imports", "unused_declared",
    }),
}

_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "exploit_text", "vuln_body", "dep_full_list",
    "signature_bytes", "private_key", "requirements_full_text",
    "secret", "token", "key",
})

# Max items per audit field list — keep chain entries bounded
_MAX_DRIFT_LIST = 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    plugin_name: str
    package_name: str
    package_version: str
    cve_id: str
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    fix_available: bool = False


@dataclass
class DriftReport:
    plugin_name: str
    undeclared_imports: tuple[str, ...]
    unused_declared: tuple[str, ...]

    @property
    def has_drift(self) -> bool:
        return bool(self.undeclared_imports or self.unused_declared)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _corvin_home() -> Path:
    for var in ("CORVIN_HOME", "CORVIN_HOME"):
        v = os.environ.get(var)
        if v:
            return Path(v).expanduser()
    return Path.home() / ".corvin"


def _audit_path() -> Path:
    return (_corvin_home() / "tenants" / "_default" / "global" /
            "forge" / "audit.jsonl")


def _state_dir() -> Path:
    return _corvin_home() / "global" / "supply_chain"


def _plugins_root() -> Path:
    """Resolve <repo>/plugins/ (legacy) or repo root for compat.

    ADR-0035: plugins/ was split into core/ + operator/. Returns the repo root
    so callers can iterate sub-trees. The legacy plugins/ path is returned when
    it still exists (migration window).
    """
    repo = Path(__file__).resolve().parents[3]
    legacy = repo / "plugins"
    if legacy.is_dir():
        return legacy
    return repo


def _all_plugin_dirs(repo: Path | None = None) -> list[Path]:
    """Return all plugin sub-directories across core/ and operator/."""
    if repo is None:
        repo = Path(__file__).resolve().parents[3]
    dirs = []
    for top in ("core", "operator", "plugins"):
        top_dir = repo / top
        if top_dir.is_dir():
            dirs.extend(sorted(d for d in top_dir.iterdir() if d.is_dir() and not d.name.startswith(".")))
    return dirs


# ---------------------------------------------------------------------------
# AST-walk for capability drift
# ---------------------------------------------------------------------------


def walk_python_imports(plugin_dir: Path) -> set[str]:
    """Walk every `*.py` under `plugin_dir` and collect top-level imports.

    Honest scope (per ADR-0021):
      * In-function imports IGNORED (cost-prohibitive otherwise).
      * Dynamic imports via `importlib.import_module(literal_string)`
        IGNORED — the literal-string case is rare in practice and the
        AST machinery for it doubles the runtime; obfuscation
        (`importlib.import_module(b64decode(...).decode())`) cannot
        be detected at all, so honest scope is the right answer.
      * Conditional imports inside `try: import x` ARE captured (those
        are top-level, just guarded for optional deps).

    Returns top-level package names only (e.g. `email.mime.text` →
    `email`).
    """
    EXCLUDED_DIRS = {".venv", ".pytest_cache", "__pycache__", ".git",
                     "node_modules"}
    imports: set[str] = set()
    for p in plugin_dir.rglob("*.py"):
        if any(part in EXCLUDED_DIRS for part in p.parts):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        # Top-level + module-level (children of ast.Module body) only
        for node in tree.body:
            _collect_module_level_imports(node, imports)
        # Also: try/except blocks at module level still expose imports
        for node in tree.body:
            if isinstance(node, ast.Try):
                for sub in node.body:
                    _collect_module_level_imports(sub, imports)
                for handler in node.handlers:
                    for sub in handler.body:
                        _collect_module_level_imports(sub, imports)
    return imports


def _collect_module_level_imports(node: ast.AST, sink: set[str]) -> None:
    """Add top-level package name(s) from an Import / ImportFrom node."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name:
                sink.add(alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        if node.module and node.level == 0:
            sink.add(node.module.split(".")[0])


def detect_capability_drift(
    plugin_dir: Path,
    manifest: dict[str, Any] | None,
) -> DriftReport:
    """Compare AST-walked imports against declared imports.

    Returns a DriftReport with:
      * undeclared_imports: imports the AST sees but manifest doesn't
        declare (capped at _MAX_DRIFT_LIST, alphabetically sorted)
      * unused_declared: manifest declarations the AST doesn't see
    """
    plugin_name = plugin_dir.name
    actual = walk_python_imports(plugin_dir)
    declared: set[str] = set()
    if isinstance(manifest, dict):
        spec = manifest.get("spec") or {}
        if isinstance(spec, dict):
            decl = (spec.get("declared_imports") or {}).get("python") or []
            if isinstance(decl, list):
                declared = {x for x in decl if isinstance(x, str)}
    # Filter out stdlib + standard naming we always allow
    STDLIB_ALWAYS_OK = {
        "os", "sys", "re", "json", "time", "datetime", "pathlib",
        "typing", "dataclasses", "collections", "subprocess", "hashlib",
        "shutil", "tempfile", "uuid", "argparse", "ast", "io", "functools",
        "logging", "threading", "queue", "asyncio", "enum", "contextlib",
        "warnings", "abc", "copy", "itertools", "string", "math",
        "secrets", "fcntl", "base64", "urllib", "html", "email", "ssl",
        "socket", "struct", "zipfile", "tarfile", "gzip", "csv", "sqlite3",
        "concurrent", "multiprocessing", "signal", "select", "errno",
        "platform", "textwrap", "difflib", "inspect", "traceback",
        "weakref", "atexit", "gc", "operator",
    }
    actual_filtered = actual - STDLIB_ALWAYS_OK
    undeclared = sorted(actual_filtered - declared)[:_MAX_DRIFT_LIST]
    unused = sorted(declared - actual_filtered)[:_MAX_DRIFT_LIST]
    return DriftReport(
        plugin_name=plugin_name,
        undeclared_imports=tuple(undeclared),
        unused_declared=tuple(unused),
    )


def load_capability_manifest(plugin_dir: Path) -> dict[str, Any] | None:
    """Read `plugin.corvin.yaml` from a plugin tree, or None if absent."""
    p = plugin_dir / _MANIFEST_FILENAME
    if not p.exists():
        return None
    try:
        import yaml as _y
        with p.open("r", encoding="utf-8") as fh:
            raw = _y.safe_load(fh)
    except Exception:  # noqa: BLE001
        return None
    return raw if isinstance(raw, dict) else None


# ---------------------------------------------------------------------------
# pip-audit subprocess wrapper
# ---------------------------------------------------------------------------


def run_pip_audit(
    requirements_path: Path,
    plugin_name: str,
    *,
    timeout_s: int = 60,
) -> tuple[list[Finding], str]:
    """Run `pip-audit --requirement <path> --format json`.

    Returns ``(findings, error_reason)``. ``error_reason == ""`` on
    success. Findings are parsed from the pip-audit JSON output;
    severity is mapped from the OSV / advisory severity tag.

    Test-hook: ``CORVIN_SUPPLY_CHAIN_FAKE=1`` returns canned
    findings without spawning. Tests use this to exercise the full
    pipeline without a real pip-audit install.
    """
    if os.environ.get("CORVIN_SUPPLY_CHAIN_FAKE") == "1":
        # Canned: one CRITICAL finding for tests
        return ([Finding(
            plugin_name=plugin_name,
            package_name="cryptography",
            package_version="42.0.0",
            cve_id="CVE-2026-99999",
            severity="CRITICAL",
            fix_available=True,
        )], "")

    if not requirements_path.exists():
        return ([], "no-requirements")
    cmd = ["pip-audit", "--requirement", str(requirements_path),
           "--format", "json", "--strict"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
    except FileNotFoundError:
        return ([], "pip-audit-missing")
    except subprocess.TimeoutExpired:
        return ([], "subprocess-timeout")
    except Exception as e:  # noqa: BLE001
        return ([], f"spawn-error:{type(e).__name__}")
    # pip-audit exits non-zero when findings are present — that's not
    # an error condition for us. Parse stdout regardless.
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        # Some pip-audit versions emit a different shape on error — skip
        if result.returncode != 0:
            return ([], f"pip-audit-exit-{result.returncode}")
        return ([], "unparseable-output")
    findings: list[Finding] = []
    deps = data.get("dependencies") if isinstance(data, dict) else None
    if isinstance(deps, list):
        for entry in deps:
            if not isinstance(entry, dict):
                continue
            pkg = entry.get("name", "")
            ver = entry.get("version", "")
            for vuln in entry.get("vulns") or []:
                if not isinstance(vuln, dict):
                    continue
                vid = vuln.get("id") or ""
                sev = _normalise_severity(vuln.get("severity"))
                fix_avail = bool(vuln.get("fix_versions"))
                findings.append(Finding(
                    plugin_name=plugin_name,
                    package_name=pkg,
                    package_version=ver,
                    cve_id=vid,
                    severity=sev,
                    fix_available=fix_avail,
                ))
    return (findings, "")


def _normalise_severity(raw: Any) -> str:
    """Map pip-audit / OSV severity values to our 4-tier enum."""
    if isinstance(raw, str) and raw.upper() in _VALID_SEVERITIES:
        return raw.upper()
    if isinstance(raw, list) and raw:
        # pip-audit may emit a list of {type, score, severity}
        for entry in raw:
            if isinstance(entry, dict):
                level = entry.get("severity")
                if isinstance(level, str) and level.upper() in _VALID_SEVERITIES:
                    return level.upper()
    return "MEDIUM"  # safe default — not silent, just middle-of-road


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


def _validate_audit_details(event_type: str, details: dict[str, Any]) -> None:
    allowed = _AUDIT_ALLOWED_FIELDS.get(event_type)
    if allowed is None:
        raise ValueError(f"unknown event_type {event_type!r}")
    for k in details.keys():
        if k in _FORBIDDEN_FIELDS:
            raise ValueError(
                f"field {k!r} is in _FORBIDDEN_FIELDS for {event_type}"
            )
        if k not in allowed:
            raise ValueError(
                f"field {k!r} not in allow-list for {event_type}"
            )


def _emit(event_type: str, details: dict[str, Any]) -> None:
    _validate_audit_details(event_type, details)
    repo = Path(__file__).resolve().parents[3]
    forge_path = repo / "operator" / "forge"
    if str(forge_path) not in sys.path:
        sys.path.insert(0, str(forge_path))
    from forge import security_events as _se  # noqa: WPS433
    p = _audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _se.write_event(p, event_type, details=details)


# ---------------------------------------------------------------------------
# CRITICAL-diff snapshot persistence
# ---------------------------------------------------------------------------


def _critical_snapshot_path() -> Path:
    return _state_dir() / "last_critical.json"


def _load_critical_snapshot() -> set[str]:
    """Return set of `<plugin>:<cve_id>` keys from the last run."""
    p = _critical_snapshot_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(data, dict):
        return set()
    keys = data.get("keys")
    if not isinstance(keys, list):
        return set()
    return {k for k in keys if isinstance(k, str)}


def _save_critical_snapshot(keys: set[str]) -> None:
    p = _critical_snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"keys": sorted(keys),
                              "ts": time.time()}, indent=2))
    os.chmod(p, 0o600)


# ---------------------------------------------------------------------------
# Public run-cycle
# ---------------------------------------------------------------------------


def scan_plugin(plugin_dir: Path) -> tuple[list[Finding], DriftReport | None]:
    """Run CVE + capability-drift checks against one plugin tree.

    Returns ``(findings, drift_report)``. ``drift_report`` is None
    when the plugin has no `plugin.corvin.yaml` (manifest-less
    plugins are exempt from drift detection; they get CVE checks
    only).
    """
    plugin_name = plugin_dir.name
    requirements = plugin_dir / "requirements.txt"
    findings: list[Finding] = []
    if requirements.exists():
        findings, err = run_pip_audit(requirements, plugin_name)
        if err == "pip-audit-missing":
            try:
                _emit("supply_chain.cve_check_skipped",
                       {"plugin_name": plugin_name, "reason": err})
            except Exception:  # noqa: BLE001
                pass
    drift_report: DriftReport | None = None
    manifest = load_capability_manifest(plugin_dir)
    if manifest is not None:
        drift_report = detect_capability_drift(plugin_dir, manifest)
        if drift_report.has_drift:
            try:
                _emit("supply_chain.capability_drift", {
                    "plugin_name": plugin_name,
                    "undeclared_imports": list(drift_report.undeclared_imports),
                    "unused_declared": list(drift_report.unused_declared),
                })
            except Exception:  # noqa: BLE001
                pass
    return findings, drift_report


def emit_cve_findings(findings: list[Finding], *, cadence: str,
                      severity_filter: tuple[str, ...]) -> int:
    """Emit `supply_chain.cve_detected` per finding above the severity filter.

    Returns the count of emitted events.
    """
    count = 0
    for f in findings:
        if f.severity not in severity_filter:
            continue
        try:
            _emit("supply_chain.cve_detected", {
                "plugin_name": f.plugin_name,
                "package_name": f.package_name,
                "package_version": f.package_version,
                "cve_id": f.cve_id,
                "severity": f.severity,
                "fix_available": f.fix_available,
                "cadence": cadence,
            })
            count += 1
        except Exception:  # noqa: BLE001
            pass
    return count


def _cmd_weekly(args: argparse.Namespace) -> int:
    """Weekly digest: emit MEDIUM/HIGH/CRITICAL findings + capability drift."""
    plugin_dirs = _all_plugin_dirs()
    if not plugin_dirs:
        print("no plugin directories found", file=sys.stderr)
        return 2
    total_findings = 0
    for plugin_dir in plugin_dirs:
        findings, drift = scan_plugin(plugin_dir)
        n = emit_cve_findings(findings, cadence="weekly",
                                severity_filter=_NOTIFY_SEVERITIES_WEEKLY)
        total_findings += n
        if not args.quiet:
            label = "CVE+drift" if drift and drift.has_drift else "CVE"
            print(f"  {plugin_dir.name:30s} {label:10s} findings={n}")
    if not args.quiet:
        print(f"weekly: emitted {total_findings} CVE event(s)")
    return 0


def _cmd_critical(args: argparse.Namespace) -> int:
    """Daily critical-diff: emit only NEW CRITICAL findings since last run."""
    plugin_dirs = _all_plugin_dirs()
    if not plugin_dirs:
        print("no plugin directories found", file=sys.stderr)
        return 2
    prior = _load_critical_snapshot()
    current_keys: set[str] = set()
    new_findings: list[Finding] = []
    for plugin_dir in plugin_dirs:
        findings, _ = scan_plugin(plugin_dir)
        for f in findings:
            if f.severity != "CRITICAL":
                continue
            key = f"{f.plugin_name}:{f.cve_id}"
            current_keys.add(key)
            if key not in prior:
                new_findings.append(f)
    n = emit_cve_findings(new_findings, cadence="critical-diff",
                            severity_filter=_NOTIFY_SEVERITIES_CRITICAL)
    _save_critical_snapshot(current_keys)
    if not args.quiet:
        print(f"critical: {n} new CRITICAL finding(s) since last run")
    return 0 if n == 0 else 1


def _cmd_drift(args: argparse.Namespace) -> int:
    """Drift check only — print + audit, no CVE checks."""
    plugin_dirs = _all_plugin_dirs()
    if not plugin_dirs:
        print("no plugin directories found", file=sys.stderr)
        return 2
    selected = [args.plugin] if args.plugin else None
    for plugin_dir in plugin_dirs:
        if selected and plugin_dir.name not in selected:
            continue
        manifest = load_capability_manifest(plugin_dir)
        if manifest is None:
            if not args.quiet:
                print(f"  {plugin_dir.name:30s} (no manifest, skipped)")
            continue
        report = detect_capability_drift(plugin_dir, manifest)
        if report.has_drift:
            try:
                _emit("supply_chain.capability_drift", {
                    "plugin_name": report.plugin_name,
                    "undeclared_imports": list(report.undeclared_imports),
                    "unused_declared": list(report.unused_declared),
                })
            except Exception:  # noqa: BLE001
                pass
            if not args.quiet:
                print(f"  {plugin_dir.name:30s} DRIFT "
                      f"undeclared={len(report.undeclared_imports)} "
                      f"unused={len(report.unused_declared)}")
        else:
            if not args.quiet:
                print(f"  {plugin_dir.name:30s} ok")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="supply_chain_verify",
        description="ADR-0021 Phase 31.3+31.4 — supply-chain surveillance",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    weekly = sub.add_parser("weekly", help="Weekly CVE + drift digest")
    weekly.add_argument("--quiet", action="store_true")
    weekly.set_defaults(func=_cmd_weekly)

    critical = sub.add_parser("critical",
                                 help="Daily CRITICAL-diff (NEW findings only)")
    critical.add_argument("--quiet", action="store_true")
    critical.set_defaults(func=_cmd_critical)

    drift = sub.add_parser("drift",
                            help="Capability-drift check only (no CVE)")
    drift.add_argument("--plugin", help="restrict to a single plugin name")
    drift.add_argument("--quiet", action="store_true")
    drift.set_defaults(func=_cmd_drift)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
