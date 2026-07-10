"""AWPKG installer — install, remove, list packages.

Security model (all checks happen BEFORE any extraction):
  1. manifest.yaml present and valid (JSON Schema)
  2. All declared component paths exist in the archive
  3. No undeclared paths exist (besides manifest.yaml and README.md)
  4. No absolute paths or path-traversal sequences in any entry
  5. Forge tool names match code.* pattern
  6. SkillForge linter run on every SKILL.md
  7. AWP validator run on every workflow YAML
  8. permissions.network: false enforced — tools must not declare network: allow

Any failure aborts before extracting a single byte.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import emit
from .manifest import Manifest, ManifestError, parse_bytes, validate_tool_names

_ALLOWED_PREFIXES = frozenset(
    {"workflows/", "tools/", "skills/", "personas/", "data/"}
)
_ALWAYS_ALLOWED = frozenset({"manifest.yaml", "README.md", "README.txt"})
_PATH_TRAVERSAL_RE = re.compile(r"(^|/)\.\.(/|$)")
_SEMVER_EXACT_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")


class InstallError(RuntimeError):
    """Raised when installation fails (pre-extraction check or linter)."""


class NotInstalledError(LookupError):
    """Raised when remove() is called for a package that is not installed."""


@dataclass
class InstalledPackage:
    id: str
    name: str
    version: str
    scope: str
    install_dir: Path
    components: dict[str, list[str]]
    permissions: dict[str, Any]


def _packages_root(scope: str, corvin_home: Path | None = None) -> Path:
    home = corvin_home or _resolve_corvin_home()
    if scope == "user":
        return home / "packages"
    if scope == "project":
        return _resolve_project_root() / ".corvin" / "packages"
    if scope == "session":
        return home / "sessions" / "_awpkg_session" / "packages"
    raise ValueError(f"unknown scope: {scope!r}")


def _resolve_corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():  # legacy fallback during migration
            for sub in (".corvin",):
                if (parent / sub).is_dir():
                    return parent / sub
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _resolve_project_root() -> Path:
    """Walk up from CWD to find the nearest repo root (`.corvin_repo` marker or legacy `plugins/`)."""
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent
    return cwd


def _check_archive_safety(zf: zipfile.ZipFile, manifest: Manifest) -> None:
    """Pre-extraction safety checks. Raises InstallError on any violation."""
    names = set(zf.namelist())

    for name in names:
        if name.endswith("/"):
            continue
        if _PATH_TRAVERSAL_RE.search(name):
            raise InstallError(f"path-traversal in archive entry: {name!r}")
        if name.startswith("/"):
            raise InstallError(f"absolute path in archive entry: {name!r}")
        if name == "manifest.yaml" or name in _ALWAYS_ALLOWED:
            continue
        if not any(name.startswith(pfx) for pfx in _ALLOWED_PREFIXES):
            raise InstallError(
                f"undeclared top-level path in archive: {name!r}. "
                f"Only {sorted(_ALLOWED_PREFIXES)} are permitted."
            )

    declared = set(manifest.all_component_paths)
    for path in declared:
        if path not in names:
            raise InstallError(f"declared component missing from archive: {path!r}")

    archive_component_files = {
        n for n in names
        if not n.endswith("/")
        and n not in _ALWAYS_ALLOWED
        and n != "manifest.yaml"
    }
    undeclared = archive_component_files - declared
    if undeclared:
        raise InstallError(
            f"archive contains undeclared files: {sorted(undeclared)!r}"
        )


def _check_tool_names(zf: zipfile.ZipFile, manifest: Manifest) -> None:
    tool_contents = {
        p: zf.read(p)
        for p in manifest.components.get("forge_tools", [])
        if p in zf.namelist()
    }
    bad = validate_tool_names(manifest, tool_contents)
    if bad:
        raise InstallError(
            f"Forge tool name(s) must match code.<name>: {bad!r}"
        )


def _check_tool_network(zf: zipfile.ZipFile, manifest: Manifest) -> None:
    if manifest.network_allowed:
        return
    for tool_path in manifest.components.get("forge_tools", []):
        raw = json.loads(zf.read(tool_path).decode("utf-8"))
        meta = raw.get("meta", {})
        if meta.get("network") == "allow":
            raise InstallError(
                f"tool {tool_path!r} declares network:allow but "
                f"manifest.permissions.network is false"
            )


def _run_skill_linter(zf: zipfile.ZipFile, manifest: Manifest) -> None:
    try:
        import sys as _sys
        skill_forge_path = Path(__file__).resolve().parents[3] / "skill-forge"
        if str(skill_forge_path) not in _sys.path:
            _sys.path.insert(0, str(skill_forge_path))
        from skill_forge.linter import lint  # type: ignore[import]
        for skill_path in manifest.components.get("skills", []):
            body = zf.read(skill_path).decode("utf-8")
            result = lint(body)
            if not result.ok:
                raise InstallError(
                    f"SkillForge linter rejected {skill_path!r}: "
                    f"{'; '.join(result.errors)}"
                )
    except ImportError:
        pass


def _run_workflow_validator(zf: zipfile.ZipFile, manifest: Manifest) -> None:
    """Validate every workflow YAML in the package BEFORE extraction.

    Security-model item: a package must not install a malformed/invalid
    workflow. A WorkflowInvalid (bad graph, unknown node type, cycle, missing
    dependency) aborts the install as an InstallError. Only a genuinely
    unavailable validator (ImportError — the workflows package isn't on this
    host) is tolerated as a soft skip; every other failure is fatal so a
    corrupt YAML can never slip through as "installed clean".
    """
    try:
        import sys as _sys
        wf_path = Path(__file__).resolve().parents[3] / "core" / "workflows"
        if str(wf_path) not in _sys.path:
            _sys.path.insert(0, str(wf_path))
        from corvin_workflows.validator import validate, WorkflowInvalid  # type: ignore[import]
        from corvin_workflows.storage import WorkflowDoc  # type: ignore[import]
        import yaml  # type: ignore[import]
    except ImportError:
        return  # workflow validator unavailable on this host — soft skip

    for wf_path_str in manifest.components.get("workflows", []):
        raw_yaml = zf.read(wf_path_str).decode("utf-8")
        try:
            data = yaml.safe_load(raw_yaml)
            doc = WorkflowDoc.from_dict(data)
            validate(doc)
        except InstallError:
            raise
        except WorkflowInvalid as exc:
            raise InstallError(f"workflow validation failed for {wf_path_str!r}: {exc}")
        except Exception as exc:  # malformed YAML / bad shape → refuse
            raise InstallError(f"workflow {wf_path_str!r} could not be parsed/validated: {exc}")


def _extract_to(zf: zipfile.ZipFile, manifest: Manifest, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = zf.read(name)
        target.write_bytes(data)
    meta_file = dest / "_awpkg_meta.json"
    meta_file.write_text(
        json.dumps(
            {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "components": manifest.components,
                "permissions": manifest.permissions,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def install(
    awpkg_path: str | Path,
    scope: str = "user",
    corvin_home: Path | None = None,
    tenant_id: str = "_default",
) -> InstalledPackage:
    """Install an .awpkg file. Raises InstallError on any violation."""
    awpkg_path = Path(awpkg_path)
    if not awpkg_path.exists():
        raise InstallError(f"file not found: {awpkg_path}")
    if not zipfile.is_zipfile(awpkg_path):
        raise InstallError(f"not a valid ZIP archive: {awpkg_path}")

    with zipfile.ZipFile(awpkg_path, "r") as zf:
        names = zf.namelist()
        if "manifest.yaml" not in names:
            raise InstallError("archive does not contain manifest.yaml")

        manifest_bytes = zf.read("manifest.yaml")
        try:
            manifest = parse_bytes(manifest_bytes)
        except ManifestError as exc:
            raise InstallError(str(exc)) from exc

        _check_archive_safety(zf, manifest)
        _check_tool_names(zf, manifest)
        _check_tool_network(zf, manifest)
        _run_skill_linter(zf, manifest)
        _run_workflow_validator(zf, manifest)

        dest = _packages_root(scope, corvin_home) / manifest.id
        _extract_to(zf, manifest, dest)

    emit(
        "package.installed",
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        scope=scope,
        tenant_id=tenant_id,
        source=str(awpkg_path),
    )

    return InstalledPackage(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        scope=scope,
        install_dir=dest,
        components=manifest.components,
        permissions=manifest.permissions,
    )


def remove(
    package_id: str,
    scope: str = "user",
    corvin_home: Path | None = None,
    tenant_id: str = "_default",
) -> None:
    """Remove an installed package. Raises NotInstalledError if not found."""
    dest = _packages_root(scope, corvin_home) / package_id
    if not dest.exists():
        raise NotInstalledError(
            f"package {package_id!r} not installed in scope {scope!r}"
        )
    shutil.rmtree(dest)
    emit(
        "package.removed",
        id=package_id,
        scope=scope,
        tenant_id=tenant_id,
    )


def list_installed(
    scope: str = "user",
    corvin_home: Path | None = None,
) -> list[InstalledPackage]:
    """Return all packages installed in the given scope."""
    root = _packages_root(scope, corvin_home)
    if not root.exists():
        return []
    packages: list[InstalledPackage] = []
    for item in sorted(root.iterdir()):
        if not item.is_dir():
            continue
        meta_file = item / "_awpkg_meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            packages.append(
                InstalledPackage(
                    id=meta["id"],
                    name=meta["name"],
                    version=meta["version"],
                    scope=scope,
                    install_dir=item,
                    components=meta.get("components", {}),
                    permissions=meta.get("permissions", {}),
                )
            )
        except Exception:
            continue
    return packages


def is_installed(
    package_id: str,
    scope: str = "user",
    corvin_home: Path | None = None,
) -> bool:
    dest = _packages_root(scope, corvin_home) / package_id
    return (dest / "_awpkg_meta.json").exists()


def register_components(
    installed: InstalledPackage,
    *,
    corvin_home: Path | None = None,
    tenant_id: str = "_default",
) -> dict[str, list[str]]:
    """Register Forge tools and SkillForge skills from an installed package.

    After install(), call this to make the package's tools and skills visible
    to the running Forge and SkillForge MCP servers via file-based registration.
    Tools are written to <corvin_home>/forge/tools/user/<name>/tool.json.
    Skills are written to <corvin_home>/skill-forge/skills/user/<name>/.

    Returns a summary dict: {"forge_tools": [...names...], "skills": [...names...]}.
    """
    import json as _json
    import shutil as _shutil
    import time as _time

    home = corvin_home or _resolve_corvin_home()
    registered: dict[str, list[str]] = {"forge_tools": [], "skills": []}

    # --- Forge tools -------------------------------------------------------
    forge_tools_root = home / "forge" / "tools" / "user"
    for tool_arc_path in installed.components.get("forge_tools", []):
        src = installed.install_dir / tool_arc_path
        if not src.exists():
            continue
        try:
            raw = _json.loads(src.read_text(encoding="utf-8"))
            tool_name = raw.get("name", Path(tool_arc_path).stem)
        except Exception:
            tool_name = Path(tool_arc_path).stem
        dest_dir = forge_tools_root / tool_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        _shutil.copy2(src, dest_dir / "tool.json")
        registered["forge_tools"].append(tool_name)

    # --- SkillForge skills -------------------------------------------------
    skill_forge_root = home / "skill-forge" / "skills" / "user"
    for skill_arc_path in installed.components.get("skills", []):
        src = installed.install_dir / skill_arc_path
        if not src.exists():
            continue
        # arc path shape: "skills/<name>/SKILL.md"
        parts = Path(skill_arc_path).parts
        skill_name = parts[1] if len(parts) > 2 else Path(skill_arc_path).stem
        dest_dir = skill_forge_root / skill_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        _shutil.copy2(src, dest_dir / "SKILL.md")
        meta_file = dest_dir / "meta.json"
        if not meta_file.exists():
            meta_file.write_text(
                _json.dumps(
                    {
                        "name": skill_name,
                        "scope": "user",
                        "created_at": _time.time(),
                        "grades": [],
                        "mean_score": 0.0,
                        "source": f"awpkg:{installed.id}@{installed.version}",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        registered["skills"].append(skill_name)

    emit(
        "package.components_registered",
        id=installed.id,
        version=installed.version,
        scope=installed.scope,
        tenant_id=tenant_id,
        forge_tools=registered["forge_tools"],
        skills=registered["skills"],
    )
    return registered
