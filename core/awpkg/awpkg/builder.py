"""AWPKG builder — build, init and export packages."""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from .manifest import ManifestError, parse_raw

try:
    import yaml as _yaml  # type: ignore[import]
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


class BuildError(ValueError):
    """Raised when a build source is misconfigured."""


_AWPKG_YAML_TEMPLATE = """\
# awpkg build config — edit and run 'corvin pkg build'
awpkg: "1.0"
id: "com.example.my-workflow"
name: "My Workflow"
version: "0.1.0"
description: "Describe your workflow here."
author: "Your Name <you@example.com>"
license: "Apache-2.0"

components:
  workflows:
    - src/workflow.awp.yaml
  # forge_tools:
  #   - src/tools/code_my_tool.json
  # skills:
  #   - src/skills/my_skill/SKILL.md
  # personas:
  #   - src/personas/my_persona.yaml

permissions:
  network: false
  compute: false
  secrets: []

dependencies: []
"""

_WORKFLOW_TEMPLATE = """\
awp: "1.0.0"

workflow:
  name: my_workflow
  description: "A minimal example AWP workflow."

orchestration:
  engine: dag
  graph:
    - id: step_one
      type: agent
      agent: assistant
      instructions: "Do something useful."
      depends_on: []
"""


def init(dest_dir: str | Path) -> Path:
    """Write awpkg.yaml skeleton and a sample workflow into dest_dir."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    cfg = dest / "awpkg.yaml"
    if cfg.exists():
        raise BuildError(f"awpkg.yaml already exists at {cfg}")
    cfg.write_text(_AWPKG_YAML_TEMPLATE, encoding="utf-8")
    src = dest / "src"
    src.mkdir(exist_ok=True)
    wf = src / "workflow.awp.yaml"
    if not wf.exists():
        wf.write_text(_WORKFLOW_TEMPLATE, encoding="utf-8")
    return cfg


def build(
    source_dir: str | Path,
    output_dir: str | Path | None = None,
) -> Path:
    """Build an .awpkg from awpkg.yaml in source_dir.

    Returns the path to the generated .awpkg file.
    """
    source = Path(source_dir).resolve()
    cfg_path = source / "awpkg.yaml"
    if not cfg_path.exists():
        raise BuildError(f"awpkg.yaml not found in {source}")

    if _HAS_YAML:
        raw: dict[str, Any] = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    else:
        raise BuildError(
            "PyYAML is required to build packages (pip install pyyaml)"
        )

    try:
        manifest = parse_raw(raw)
    except ManifestError as exc:
        raise BuildError(f"awpkg.yaml validation failed: {exc}") from exc

    out_dir = Path(output_dir).resolve() if output_dir else source
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{manifest.id.replace('.', '-')}-{manifest.version}.awpkg"
    out_path = out_dir / filename

    manifest_bytes = _yaml.dump(raw, allow_unicode=True, default_flow_style=False).encode("utf-8")

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", manifest_bytes)

        readme = source / "README.md"
        if readme.exists():
            zf.write(readme, "README.md")

        for path in manifest.all_component_paths:
            src_file = source / path
            if not src_file.exists():
                raise BuildError(
                    f"declared component {path!r} not found at {src_file}"
                )
            zf.write(src_file, path)

    return out_path


def build_from_dict(
    manifest_dict: dict[str, Any],
    files: dict[str, bytes],
    output_path: str | Path,
) -> Path:
    """Build an .awpkg from in-memory data. Used by tests and programmatic callers.

    Args:
        manifest_dict: The manifest as a Python dict (validated internally).
        files: Dict mapping archive path → file bytes (e.g. "tools/code_foo.json" → b"...").
        output_path: Where to write the .awpkg file.
    """
    from .manifest import parse_raw, ManifestError
    try:
        parse_raw(manifest_dict)
    except ManifestError as exc:
        raise BuildError(str(exc)) from exc

    if _HAS_YAML:
        manifest_bytes = _yaml.dump(
            manifest_dict, allow_unicode=True, default_flow_style=False
        ).encode("utf-8")
    else:
        manifest_bytes = json.dumps(manifest_dict).encode("utf-8")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", manifest_bytes)
        for arc_path, data in files.items():
            zf.writestr(arc_path, data)
    return out


def export(
    package_id: str,
    output_dir: str | Path,
    scope: str = "user",
    corvin_home: Path | None = None,
) -> Path:
    """Re-pack an installed package back into an .awpkg file."""
    from .installer import _packages_root, _resolve_corvin_home
    import json

    home = corvin_home or _resolve_corvin_home()
    install_dir = _packages_root(scope, home) / package_id
    meta_file = install_dir / "_awpkg_meta.json"
    if not meta_file.exists():
        raise BuildError(f"package {package_id!r} not installed in scope {scope!r}")

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{package_id.replace('.', '-')}-{meta['version']}.awpkg"
    out_path = out_dir / filename

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest_file = install_dir / "manifest.yaml"
        if manifest_file.exists():
            zf.write(manifest_file, "manifest.yaml")

        for paths in meta.get("components", {}).values():
            for p in paths:
                src = install_dir / p
                if src.exists():
                    zf.write(src, p)

        readme = install_dir / "README.md"
        if readme.exists():
            zf.write(readme, "README.md")

    return out_path
