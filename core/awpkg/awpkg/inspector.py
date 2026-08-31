"""AWPKG inspector — read-only introspection of .awpkg files."""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest, ManifestError, parse_bytes


class InspectError(RuntimeError):
    """Raised when the archive cannot be inspected."""


@dataclass
class InspectResult:
    path: Path
    manifest: Manifest
    archive_entries: list[str]
    readme: str | None
    warnings: list[str]

    def summary(self) -> str:
        m = self.manifest
        lines = [
            f"Package:     {m.id}  v{m.version}",
            f"Name:        {m.name}",
            f"Description: {m.description}",
        ]
        if m.author:
            lines.append(f"Author:      {m.author}")
        if m.license:
            lines.append(f"License:     {m.license}")
        if m.homepage:
            lines.append(f"Homepage:    {m.homepage}")
        lines.append("")
        lines.append("Components:")
        for kind, paths in m.components.items():
            for p in paths:
                lines.append(f"  [{kind}]  {p}")
        if m.dependencies:
            lines.append("")
            lines.append("Dependencies:")
            for dep in m.dependencies:
                lines.append(f"  {dep['id']}  {dep['version']}")
        perms = m.permissions
        if perms:
            lines.append("")
            lines.append("Permissions:")
            lines.append(f"  network:  {perms.get('network', False)}")
            lines.append(f"  compute:  {perms.get('compute', False)}")
            if perms.get("secrets"):
                lines.append(f"  secrets:  {', '.join(perms['secrets'])}")
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ! {w}")
        return "\n".join(lines)


def inspect(awpkg_path: str | Path) -> InspectResult:
    """Parse and validate an .awpkg without extracting anything."""
    path = Path(awpkg_path)
    if not path.exists():
        raise InspectError(f"file not found: {path}")
    if not zipfile.is_zipfile(path):
        raise InspectError(f"not a valid ZIP archive: {path}")

    warnings: list[str] = []
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        if "manifest.yaml" not in names:
            raise InspectError("archive does not contain manifest.yaml")

        try:
            manifest = parse_bytes(zf.read("manifest.yaml"))
        except ManifestError as exc:
            raise InspectError(f"manifest invalid: {exc}") from exc

        readme: str | None = None
        for r in ("README.md", "README.txt"):
            if r in names:
                readme = zf.read(r).decode("utf-8", errors="replace")
                break

        declared = set(manifest.all_component_paths)
        for name in names:
            if name.endswith("/") or name in {"manifest.yaml", "README.md", "README.txt"}:
                continue
            if name not in declared:
                warnings.append(f"undeclared archive entry: {name!r}")

        for p in declared:
            if p not in names:
                warnings.append(f"declared component missing from archive: {p!r}")

        archive_entries = [n for n in names if not n.endswith("/")]

    return InspectResult(
        path=path,
        manifest=manifest,
        archive_entries=archive_entries,
        readme=readme,
        warnings=warnings,
    )
