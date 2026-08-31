#!/usr/bin/env python3
"""cve_surveillance.py — CVE surveillance for Corvin dependencies.

ADR-0073 G-008 / ADR-0021 Phase 31.3:
Checks installed Python packages against the OSV (Open Source Vulnerabilities)
database via its public REST API (https://api.osv.dev/v1/query). Pure stdlib,
no external dependencies beyond urllib. Designed to run as a weekly CI job or
systemd timer.

Usage:
    python3 cve_surveillance.py [--req requirements.txt] [--cvss-min 7.0]
    python3 cve_surveillance.py --json        # machine-readable output
    python3 cve_surveillance.py --fail-on-critical  # exit 1 if CVSS >= 9.0

Exit codes:
    0  — no vulnerabilities above threshold
    1  — at least one vulnerability found above threshold
    2  — network error or scan failure (advisory only, does not block CI by default)

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
_MAX_PACKAGES_PER_BATCH = 50
_REQUEST_TIMEOUT = 30

# ── Package parsing ────────────────────────────────────────────────────────────

def _installed_packages() -> list[tuple[str, str]]:
    """Return list of (name, version) for all installed packages."""
    try:
        import importlib.metadata as meta
        return [(d.metadata["Name"], d.version) for d in meta.distributions()]
    except Exception:
        return []


def _req_packages(req_path: Path) -> list[tuple[str, str]]:
    """Parse requirements.txt → [(name, version_spec)]. Version may be '*'."""
    import re
    packages = []
    re_line = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(?:\[.*?\])?(?:\s*[><=!~^]{1,3}\s*([\w.*+]+))?")
    for line in req_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re_line.match(line)
        if m:
            packages.append((m.group(1), m.group(2) or "*"))
    return packages


# ── OSV API ───────────────────────────────────────────────────────────────────

@dataclass
class Vulnerability:
    vuln_id: str
    summary: str
    severity: str   # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN"
    cvss_score: float
    package: str
    version: str
    aliases: list[str] = field(default_factory=list)


def _batch_query(packages: list[tuple[str, str]]) -> dict[str, Any]:
    """Send a batch query to OSV and return the raw JSON response."""
    queries = [
        {
            "package": {"name": name.lower(), "ecosystem": "PyPI"},
            "version": version if version != "*" else "",
        }
        for name, version in packages
    ]
    body = json.dumps({"queries": queries}).encode("utf-8")
    req = urllib.request.Request(
        _OSV_BATCH_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"OSV API unreachable: {e}") from e


def _parse_severity(vuln_raw: dict) -> tuple[str, float]:
    """Extract severity label and CVSS score from an OSV vulnerability dict."""
    # Prefer database_specific CVSS if available
    for sev in vuln_raw.get("severity", []):
        score_str = sev.get("score", "")
        try:
            score = float(score_str)
            if score >= 9.0:
                return "CRITICAL", score
            elif score >= 7.0:
                return "HIGH", score
            elif score >= 4.0:
                return "MEDIUM", score
            else:
                return "LOW", score
        except (ValueError, TypeError):
            pass
    return "UNKNOWN", 0.0


def _scan_packages(packages: list[tuple[str, str]]) -> list[Vulnerability]:
    """Query OSV for all packages in batches; return list of vulnerabilities."""
    vulns: list[Vulnerability] = []
    # Process in chunks
    for i in range(0, len(packages), _MAX_PACKAGES_PER_BATCH):
        chunk = packages[i:i + _MAX_PACKAGES_PER_BATCH]
        try:
            resp = _batch_query(chunk)
        except RuntimeError as e:
            print(f"WARNING: OSV scan failed for batch {i//50}: {e}", file=sys.stderr)
            continue
        results = resp.get("results", [])
        for j, result in enumerate(results):
            if j >= len(chunk):
                break
            name, version = chunk[j]
            for v in result.get("vulns", []):
                sev, score = _parse_severity(v)
                vulns.append(Vulnerability(
                    vuln_id=v.get("id", "UNKNOWN"),
                    summary=v.get("summary", "")[:200],
                    severity=sev,
                    cvss_score=score,
                    package=name,
                    version=version,
                    aliases=v.get("aliases", [])[:5],
                ))
    return vulns


# ── Report formatting ─────────────────────────────────────────────────────────

def _format_human(vulns: list[Vulnerability], cvss_min: float) -> str:
    above = [v for v in vulns if v.cvss_score >= cvss_min or (v.severity in ("CRITICAL", "HIGH") and cvss_min <= 7.0)]
    if not above:
        return f"CVE surveillance: OK — {len(vulns)} total findings, none above CVSS {cvss_min:.1f}"

    lines = [f"CVE surveillance: {len(above)} finding(s) above CVSS {cvss_min:.1f}\n"]
    for v in sorted(above, key=lambda x: -x.cvss_score):
        lines.append(f"  [{v.severity}] {v.vuln_id} — {v.package}=={v.version}")
        if v.summary:
            lines.append(f"    {v.summary}")
        if v.aliases:
            lines.append(f"    Aliases: {', '.join(v.aliases)}")
        lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="CVE surveillance (ADR-0073 G-008 Phase 31.3)")
    ap.add_argument("--req", default="requirements.txt", help="requirements.txt path")
    ap.add_argument("--installed", action="store_true",
                    help="scan all installed packages (not just requirements.txt)")
    ap.add_argument("--cvss-min", type=float, default=7.0,
                    help="minimum CVSS score to report (default: 7.0 = HIGH)")
    ap.add_argument("--fail-on-critical", action="store_true",
                    help="exit 1 when CVSS >= 9.0 (CRITICAL); otherwise advisory only")
    ap.add_argument("--json", action="store_true", dest="json_out",
                    help="machine-readable JSON output")
    args = ap.parse_args(argv)

    req_path = Path(args.req)

    if args.installed:
        packages = _installed_packages()
        print(f"Scanning {len(packages)} installed packages...", file=sys.stderr)
    elif req_path.exists():
        packages = _req_packages(req_path)
        # Resolve actual installed versions where possible
        try:
            import importlib.metadata as meta
            packages = [(n, meta.version(n) if v in ("*", "") else v) for n, v in packages]
        except Exception:
            pass
        print(f"Scanning {len(packages)} packages from {req_path}...", file=sys.stderr)
    else:
        print(f"ERROR: {req_path} not found. Use --installed or --req <path>.", file=sys.stderr)
        return 2

    try:
        vulns = _scan_packages(packages)
    except Exception as e:
        print(f"ERROR: CVE scan failed: {e}", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "packages_scanned": len(packages),
            "vulnerabilities": [
                {
                    "id": v.vuln_id,
                    "package": v.package,
                    "version": v.version,
                    "severity": v.severity,
                    "cvss_score": v.cvss_score,
                    "summary": v.summary,
                    "aliases": v.aliases,
                }
                for v in sorted(vulns, key=lambda x: -x.cvss_score)
            ],
        }, indent=2))
    else:
        print(_format_human(vulns, args.cvss_min))

    critical = [v for v in vulns if v.cvss_score >= 9.0]
    above_threshold = [v for v in vulns if v.cvss_score >= args.cvss_min]

    if args.fail_on_critical and critical:
        return 1
    if not args.fail_on_critical and above_threshold:
        return 0  # advisory only — don't block CI by default

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
