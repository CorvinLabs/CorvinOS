"""ADR-0021 Layer 31 Phase 31.1 — SBOM-Generation (CycloneDX 1.5).

Generates a CycloneDX 1.5 JSON Software Bill of Materials from a
plugin source tree. Aggregates:
  * pinned Python deps from `requirements.txt`
  * pinned npm deps from `package-lock.json` (lockfileVersion 2 / 3)
  * vendored third-party from `NOTICE`
  * plugin source-tree sha256 (sorted path||NUL||bytes||NUL, same
    algorithm as Phase-5 `payload_sha256`)

The output is suitable for:
  * Bundling into `.corvin-pkg` archives (Phase 31.1.1 — `package
    build` emits `sbom.cdx.json` alongside the manifest)
  * Operator-side `pip-audit` / `cyclonedx-py` consumption
  * EU CRA 2027 + EU AI Act 2026 Art. 15 paper-trail

Honest scope (per ADR-0021):
  * SBOM is **drift-detection + regulator-defensibility**.
  * It does NOT prevent the most common supply-chain attacks
    (legitimate-author-account-compromise). Hashes are honest
    artefact fingerprints; if the bytes were maliciously published
    upstream, the hash matches the malicious upload.
  * The value is in *what changed since last release* + *what we
    can prove we had installed*, NOT in *what we know is safe*.

Module surface
==============

  * :func:`build_sbom(plugin_dir, *, plugin_name, plugin_version)`
    → CycloneDX 1.5 dict
  * :func:`write_sbom(plugin_dir, output_path, ...)` → Path
  * :func:`load_sbom(path)` → dict (parsed + lightly validated)
  * :func:`sbom_components_summary(sbom_dict)` → ``(python_count,
    npm_count, vendored_count)`` for audit emission
  * :func:`emit_sbom_event(event_type, *, bundle_sha256, ...)` →
    write to unified audit chain with per-event allow-list
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_TOOL_NAME = "corvin_gateway_sbom"
_TOOL_VERSION = "0.1.0"
_CDX_SPEC_VERSION = "1.5"

# Files we scan inside the plugin tree
_REQUIREMENTS_TXT = "requirements.txt"
_PACKAGE_LOCK_JSON = "package-lock.json"
_NOTICE_FILE = "NOTICE"

# Per-event allow-lists. Mirror of L23/L24/L25/L28/L29/L30 rule.
_AUDIT_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "supply_chain.sbom_verified": frozenset({
        "bundle_sha256", "dep_count", "cdx_version",
    }),
    "supply_chain.sbom_missing": frozenset({
        "bundle_sha256",
    }),
    "supply_chain.dep_hashes_updated": frozenset({
        "plugin_name", "requirements_sha256", "dep_count",
    }),
    "supply_chain.dep_hash_mismatch": frozenset({
        "plugin_name", "package_name",
    }),
    "supply_chain.signature_rekor_verified": frozenset({
        "bundle_sha256", "rekor_log_index",
    }),
    "supply_chain.signature_chain_break": frozenset({
        "bundle_sha256", "reason",
    }),
}

_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "exploit_text", "vuln_body", "dep_full_list",
    "signature_bytes", "private_key", "requirements_full_text",
    "secret", "token", "key", "manifest_body",
})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SBOMError(Exception):
    """SBOM generation / parse / verify error."""


class SBOMAuditFieldNotAllowed(Exception):
    """Caller smuggled a forbidden / off-allowlist field."""


# ---------------------------------------------------------------------------
# Requirements.txt parser
# ---------------------------------------------------------------------------


# Match `name==version` plus optional inline hash markers on the next lines
_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-+]+)")
_REQ_HASH_RE = re.compile(r"--hash=([a-z0-9]+):([0-9a-f]+)", re.IGNORECASE)


def parse_requirements_txt(path: Path) -> list[dict[str, Any]]:
    """Extract package + version + hashes from a pip requirements file.

    Tolerant of:
      * comment lines (#…)
      * blank lines
      * line continuations (\\)
      * multiple --hash entries per package

    Skips entries that aren't `==`-pinned (e.g. `>=`, `~=`, git URLs)
    — those are NOT bundle-able for SBOM purposes, the operator
    should pin them before generating an SBOM.
    """
    if not path.exists():
        return []
    components: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SBOMError(f"reading {path}: {e}") from e
    # Join continuation lines so the regex can match across them
    flat = re.sub(r"\\\s*\n\s*", " ", raw)
    for line in flat.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # New package line
        m = _REQ_LINE_RE.match(stripped)
        if m:
            current = {
                "name": m.group(1).lower(),
                "version": m.group(2),
                "hashes": [],
            }
            for hm in _REQ_HASH_RE.finditer(stripped):
                current["hashes"].append({
                    "alg": hm.group(1).upper(),
                    "content": hm.group(2),
                })
            components.append(current)
    return components


# ---------------------------------------------------------------------------
# package-lock.json parser (npm)
# ---------------------------------------------------------------------------


def parse_package_lock(path: Path) -> list[dict[str, Any]]:
    """Extract npm packages + integrity hashes from a package-lock.json.

    Supports lockfileVersion 2 + 3 (`packages` object). Skips the root
    package (key ``""``) and ``node_modules/`` prefix stripping.
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise SBOMError(f"parsing {path}: {e}") from e
    if not isinstance(raw, dict):
        return []
    packages = raw.get("packages") or {}
    out: list[dict[str, Any]] = []
    for key, pkg in packages.items():
        if not isinstance(pkg, dict):
            continue
        if not key or key == "":
            continue  # root package
        # node_modules/<name> → <name>; nested deps preserve full path
        name = key
        if name.startswith("node_modules/"):
            name = name[len("node_modules/"):]
        version = pkg.get("version")
        if not isinstance(version, str):
            continue
        entry = {
            "name": name,
            "version": version,
            "hashes": [],
        }
        integrity = pkg.get("integrity")
        # integrity format: "sha512-base64=="
        if isinstance(integrity, str) and "-" in integrity:
            alg, _, content = integrity.partition("-")
            entry["hashes"].append({
                "alg": alg.upper(),
                "content": content,
            })
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# NOTICE parser (vendored third-party hints)
# ---------------------------------------------------------------------------


def parse_notice_file(path: Path) -> list[str]:
    """Extract one component-name per non-comment line from NOTICE.

    The NOTICE file format is intentionally loose — operators write
    free text. We collect non-blank non-comment lines as ``vendored``
    component names (no version info — NOTICE is a hint, not a
    catalog). The SBOM-component shape reflects this with empty
    version + ``"type": "library"`` + ``"scope": "optional"``.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SBOMError(f"reading {path}: {e}") from e
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        # Skip headings ("Third-party components:", "License:", etc.)
        if s.endswith(":") or len(s) > 200:
            continue
        out.append(s[:80])
    return out


# ---------------------------------------------------------------------------
# Plugin source-tree hash
# ---------------------------------------------------------------------------


def plugin_source_sha256(plugin_dir: Path) -> str:
    """Compute sha256 over the sorted ``path||NUL||bytes||NUL`` stream.

    Mirror of the Phase-5 ``payload_sha256`` algorithm so an SBOM
    bundled into an ``.corvin-pkg`` ties cleanly back to the same
    integrity check the signature covers.

    Excludes: ``.venv/``, ``.pytest_cache/``, ``__pycache__/``,
    ``*.pyc``, ``.git/``.
    """
    EXCLUDED_DIRS = {".venv", ".pytest_cache", "__pycache__", ".git",
                     "node_modules"}
    EXCLUDED_SUFFIXES = (".pyc",)
    h = hashlib.sha256()
    entries: list[tuple[str, Path]] = []
    base = plugin_dir.resolve()
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in p.parts):
            continue
        if p.suffix in EXCLUDED_SUFFIXES:
            continue
        rel = p.relative_to(base).as_posix()
        entries.append((rel, p))
    entries.sort(key=lambda x: x[0])
    for rel, p in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        try:
            h.update(p.read_bytes())
        except OSError:
            continue
        h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# SBOM builder
# ---------------------------------------------------------------------------


def build_sbom(
    plugin_dir: Path,
    *,
    plugin_name: str,
    plugin_version: str,
) -> dict[str, Any]:
    """Aggregate the plugin tree into a CycloneDX 1.5 dict."""
    if not isinstance(plugin_name, str) or not plugin_name:
        raise SBOMError("plugin_name required")
    if not isinstance(plugin_version, str) or not plugin_version:
        raise SBOMError("plugin_version required")
    plugin_dir = Path(plugin_dir)
    if not plugin_dir.is_dir():
        raise SBOMError(f"plugin_dir not a directory: {plugin_dir}")

    python_deps = parse_requirements_txt(plugin_dir / _REQUIREMENTS_TXT)
    npm_deps = parse_package_lock(plugin_dir / _PACKAGE_LOCK_JSON)
    vendored = parse_notice_file(plugin_dir / _NOTICE_FILE)
    source_sha = plugin_source_sha256(plugin_dir)

    components: list[dict[str, Any]] = []
    for dep in python_deps:
        components.append({
            "type": "library",
            "name": dep["name"],
            "version": dep["version"],
            "purl": f"pkg:pypi/{dep['name']}@{dep['version']}",
            "hashes": [
                {"alg": h["alg"], "content": h["content"]}
                for h in dep.get("hashes", [])
            ],
        })
    for dep in npm_deps:
        purl_name = dep["name"].replace("@", "%40")  # url-encode scoped pkgs
        components.append({
            "type": "library",
            "name": dep["name"],
            "version": dep["version"],
            "purl": f"pkg:npm/{purl_name}@{dep['version']}",
            "hashes": [
                {"alg": h["alg"], "content": h["content"]}
                for h in dep.get("hashes", [])
            ],
        })
    for name in vendored:
        components.append({
            "type": "library",
            "name": name,
            "version": "",
            "scope": "optional",
        })

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": _CDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "tools": [{
                "vendor": "corvinos",
                "name": _TOOL_NAME,
                "version": _TOOL_VERSION,
            }],
            "component": {
                "type": "application",
                "name": plugin_name,
                "version": plugin_version,
            },
            "properties": [{
                "name": "corvin:plugin_source_sha256",
                "value": source_sha,
            }],
        },
        "components": components,
    }
    return sbom


def write_sbom(
    plugin_dir: Path,
    output_path: Path,
    *,
    plugin_name: str,
    plugin_version: str,
) -> Path:
    """Write the SBOM to disk (mode 0o600, JSON with sorted keys)."""
    sbom = build_sbom(
        plugin_dir,
        plugin_name=plugin_name,
        plugin_version=plugin_version,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sbom, indent=2, sort_keys=True))
    os.chmod(output_path, 0o600)
    return output_path


def load_sbom(path: Path) -> dict[str, Any]:
    """Read + lightly-validate an SBOM JSON file."""
    p = Path(path)
    if not p.exists():
        raise SBOMError(f"SBOM not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise SBOMError(f"unparseable SBOM {p}: {e}") from e
    if not isinstance(raw, dict):
        raise SBOMError(f"{p}: top-level must be object")
    if raw.get("bomFormat") != "CycloneDX":
        raise SBOMError(f"{p}: bomFormat must be 'CycloneDX'")
    if raw.get("specVersion") not in ("1.4", "1.5"):
        raise SBOMError(
            f"{p}: specVersion must be 1.4 or 1.5, got "
            f"{raw.get('specVersion')!r}"
        )
    return raw


def sbom_components_summary(sbom: dict[str, Any]) -> tuple[int, int, int]:
    """Return ``(python_count, npm_count, vendored_count)``."""
    py = npm = vend = 0
    for c in sbom.get("components", []):
        if not isinstance(c, dict):
            continue
        purl = c.get("purl", "")
        scope = c.get("scope", "")
        if isinstance(purl, str) and purl.startswith("pkg:pypi/"):
            py += 1
        elif isinstance(purl, str) and purl.startswith("pkg:npm/"):
            npm += 1
        elif scope == "optional":
            vend += 1
    return py, npm, vend


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


def _validate_audit_details(event_type: str, details: dict[str, Any]) -> None:
    allowed = _AUDIT_ALLOWED_FIELDS.get(event_type)
    if allowed is None:
        raise SBOMAuditFieldNotAllowed(f"unknown event_type {event_type!r}")
    for k in details.keys():
        if k in _FORBIDDEN_FIELDS:
            raise SBOMAuditFieldNotAllowed(
                f"field {k!r} is in _FORBIDDEN_FIELDS for {event_type}"
            )
        if k not in allowed:
            raise SBOMAuditFieldNotAllowed(
                f"field {k!r} not in allow-list for {event_type}; "
                f"allowed: {sorted(allowed)}"
            )


def _corvin_home() -> Path:
    v = os.environ.get("CORVIN_HOME")
    if v:
        return Path(v).expanduser()
    return Path.home() / ".corvin"


def _audit_path() -> Path:
    return (_corvin_home() / "tenants" / "_default" / "global" /
            "forge" / "audit.jsonl")


def emit_sbom_event(event_type: str, details: dict[str, Any]) -> None:
    """Emit a Phase-31.6 SBOM-related audit event into the chain."""
    _validate_audit_details(event_type, details)
    # Locate the forge security_events module via path-walk (gateway and
    # forge are sibling plugins).
    repo = Path(__file__).resolve().parents[3]
    forge_path = repo / "operator" / "forge"
    if str(forge_path) not in sys.path:
        sys.path.insert(0, str(forge_path))
    from forge import security_events as _se  # noqa: WPS433
    p = _audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _se.write_event(p, event_type, details=details)
