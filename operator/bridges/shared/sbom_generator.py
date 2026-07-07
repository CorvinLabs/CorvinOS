#!/usr/bin/env python3
"""sbom_generator.py — CycloneDX 1.5 SBOM generator for Corvin.

ADR-0073 G-008 / ADR-0021 Phase 31.1:
Generates a minimal CycloneDX 1.5 JSON SBOM from requirements.txt without
requiring the cyclonedx-bom package. Pure stdlib + optional importlib.metadata.

Also generates requirements.hash.txt (Phase 31.2) for use with
`pip install --require-hashes` in production deployments.

Usage:
    python3 sbom_generator.py [--req requirements.txt] [--out sbom.cdx.json]
    python3 sbom_generator.py --hash-only [--req requirements.txt]
    python3 sbom_generator.py --check   # verifies existing sbom.cdx.json is fresh

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_VERSION = "1.0.0"
_CYCLONEDX_SPEC = "1.5"
_SERIAL_PREFIX = "urn:uuid:"

# ── Requirement parsing ────────────────────────────────────────────────────────

_REQ_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9_.\-]+)"  # package name
    r"(?:\[.*?\])?"             # extras (ignored)
    r"\s*([><=!~^]{1,3}\s*[\w.*+]+)?"  # version specifier (optional)
    r"\s*(?:#.*)?$"             # trailing comment
)


def _parse_requirements(req_path: Path) -> list[dict[str, str]]:
    """Parse requirements.txt (or, since the project dropped requirements.txt in
    favor of pyproject.toml, the ``[project] dependencies`` array) and return a
    list of {name, version_spec} dicts."""
    if req_path.name == "pyproject.toml":
        return _parse_pyproject_dependencies(req_path)
    packages: list[dict[str, str]] = []
    for raw_line in req_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_LINE_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        spec = (m.group(2) or "").strip()
        packages.append({"name": name, "version_spec": spec})
    return packages


def _parse_pyproject_dependencies(pyproject_path: Path) -> list[dict[str, str]]:
    """Extract the base ``[project] dependencies`` array without a TOML parser
    dependency (tomllib is 3.11+ only; this project supports 3.10+). Each entry
    looks exactly like a requirements.txt line (e.g. ``"fastapi >= 0.110"``), so
    once unquoted it reuses the same _REQ_LINE_RE."""
    text = pyproject_path.read_text(encoding="utf-8")
    m = re.search(r"(?m)^dependencies\s*=\s*\[(.*?)^\]", text, re.DOTALL)
    if not m:
        return []
    packages: list[dict[str, str]] = []
    for entry_match in re.finditer(r'"([^"]+)"', m.group(1)):
        line = entry_match.group(1).strip()
        rm = _REQ_LINE_RE.match(line)
        if not rm:
            continue
        packages.append({"name": rm.group(1), "version_spec": (rm.group(2) or "").strip()})
    return packages


# ── Installed version resolution ───────────────────────────────────────────────

def _resolve_installed_versions(packages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Attempt to resolve installed versions via importlib.metadata. Falls back to '*'."""
    try:
        import importlib.metadata as meta
    except ImportError:
        return [{**p, "version": "*"} for p in packages]

    resolved = []
    for pkg in packages:
        name = pkg["name"]
        try:
            version = meta.version(name)
        except meta.PackageNotFoundError:
            version = pkg["version_spec"].lstrip(">=<~^!").strip() or "*"
        resolved.append({**pkg, "version": version})
    return resolved


# ── Hash computation for installed packages ───────────────────────────────────

def _package_sha256(name: str, version: str) -> str | None:
    """Compute SHA-256 of the installed package distribution files if available."""
    try:
        import importlib.metadata as meta
        dist = meta.distribution(name)
        files = dist.files or []
        h = hashlib.sha256()
        found = False
        for file in sorted(str(f) for f in files):
            fpath = Path(str(dist.locate_file(file)))
            if fpath.is_file():
                h.update(fpath.read_bytes())
                found = True
        return h.hexdigest() if found else None
    except Exception:  # noqa: BLE001
        return None


# ── CycloneDX component builder ───────────────────────────────────────────────

def _make_component(pkg: dict[str, str], idx: int) -> dict[str, Any]:
    name = pkg["name"]
    version = pkg.get("version", "*")
    sha = _package_sha256(name, version)

    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": f"{name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{name.lower()}@{version}",
    }

    if sha:
        component["hashes"] = [{"alg": "SHA-256", "content": sha}]

    # External reference to PyPI
    component["externalReferences"] = [
        {
            "type": "distribution",
            "url": f"https://pypi.org/project/{name}/{version}/",
        }
    ]

    return component


# ── Main SBOM builder ─────────────────────────────────────────────────────────

def _serial_number() -> str:
    import uuid
    return _SERIAL_PREFIX + str(uuid.uuid4())


def build_sbom(req_path: Path, *, component_name: str = "Corvin") -> dict[str, Any]:
    packages = _parse_requirements(req_path)
    resolved = _resolve_installed_versions(packages)

    components = [_make_component(p, i) for i, p in enumerate(resolved)]

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sbom: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": _CYCLONEDX_SPEC,
        "serialNumber": _serial_number(),
        "version": 1,
        "metadata": {
            "timestamp": now_iso,
            "tools": [
                {
                    "vendor": "Corvin",
                    "name": "sbom_generator",
                    "version": _VERSION,
                }
            ],
            "component": {
                "type": "application",
                "name": component_name,
                "version": "see CHANGELOG.md",
                "purl": "pkg:github/veegee82/Corvin",
            },
        },
        "components": components,
    }
    return sbom


# ── Hash file generator (Phase 31.2) ─────────────────────────────────────────

def build_hash_requirements(req_path: Path) -> str:
    """Generate requirements with --hash markers for pip install --require-hashes.

    Calls `pip download` in a tempdir to fetch wheel/sdist and compute hashes.
    Falls back to a header-only stub if pip is unavailable.
    """
    try:
        import tempfile, shutil
        tmpdir = Path(tempfile.mkdtemp())
        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "pip", "download",
                    "--no-deps", "--no-cache-dir",
                    "-r", str(req_path),
                    "-d", str(tmpdir),
                ],
                capture_output=True, text=True, timeout=120,
            )
            lines = [
                "# requirements.hash.txt — generated by sbom_generator.py",
                "# Use with: pip install --require-hashes -r requirements.hash.txt",
                "# ADR-0073 G-008 / ADR-0021 Phase 31.2",
                "#",
            ]
            if result.returncode != 0:
                lines.append("# pip download failed — populate hashes manually")
                lines.append(f"# stderr: {result.stderr[:200]}")
                return "\n".join(lines) + "\n"

            for whl_or_sdist in sorted(tmpdir.iterdir()):
                sha = hashlib.sha256(whl_or_sdist.read_bytes()).hexdigest()
                name_part = whl_or_sdist.stem
                lines.append(f"    --hash=sha256:{sha}  # {name_part}")

            return "\n".join(lines) + "\n"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        return (
            "# requirements.hash.txt — STUB (sbom_generator could not run pip download)\n"
            f"# Error: {e}\n"
            "# Populate manually: pip download -r requirements.txt -d /tmp/pkgs\n"
            "# Then: sha256sum /tmp/pkgs/*.whl | awk '{print \"    --hash=sha256:\" $1}'\n"
        )


# ── Freshness check ───────────────────────────────────────────────────────────

def check_freshness(sbom_path: Path, *, max_age_days: int = 90) -> tuple[bool, str]:
    """Return (ok, message). ok=False if SBOM is missing or older than max_age_days."""
    if not sbom_path.exists():
        return False, f"SBOM not found at {sbom_path}"
    try:
        data = json.loads(sbom_path.read_text())
        ts_str = data.get("metadata", {}).get("timestamp", "")
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).days
        if age > max_age_days:
            return False, f"SBOM is {age} days old (max {max_age_days})"
        return True, f"SBOM is {age} days old (within {max_age_days}-day limit)"
    except Exception as e:
        return False, f"SBOM parse error: {e}"


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="CycloneDX SBOM generator (ADR-0073 G-008)")
    ap.add_argument("--req", default=None,
                     help="Path to requirements.txt or pyproject.toml "
                          "(default: requirements.txt if present, else pyproject.toml)")
    ap.add_argument("--out", default="sbom.cdx.json", help="Output SBOM path")
    ap.add_argument("--hash-only", action="store_true", help="Generate requirements.hash.txt only")
    ap.add_argument("--check", action="store_true", help="Check freshness of existing SBOM")
    ap.add_argument("--max-age-days", type=int, default=90, help="Max SBOM age for --check")
    args = ap.parse_args(argv)

    if args.req is not None:
        req_path = Path(args.req)
    else:
        req_path = Path("requirements.txt")
        if not req_path.exists():
            req_path = Path("pyproject.toml")
    if not req_path.exists():
        print(f"ERROR: requirements file not found: {req_path}", file=sys.stderr)
        return 1

    if args.check:
        ok, msg = check_freshness(Path(args.out), max_age_days=args.max_age_days)
        print(f"{'OK' if ok else 'FAIL'}: {msg}")
        return 0 if ok else 1

    if args.hash_only:
        hash_text = build_hash_requirements(req_path)
        hash_path = req_path.parent / "requirements.hash.txt"
        hash_path.write_text(hash_text, encoding="utf-8")
        print(f"Written: {hash_path}")
        return 0

    sbom = build_sbom(req_path)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    comp_count = len(sbom.get("components", []))
    print(f"Written: {out_path} ({comp_count} components, CycloneDX {_CYCLONEDX_SPEC})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
