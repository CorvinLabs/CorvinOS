"""Test helpers — build .awpkg archives from fixture sources."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


FIXTURES = Path(__file__).parent / "fixtures"


def make_awpkg(
    manifest: dict[str, Any],
    extra_files: dict[str, bytes] | None = None,
    tmp_path: Path | None = None,
    filename: str | None = None,
) -> Path:
    """Build a .awpkg ZIP in tmp_path from manifest dict + extra files.

    extra_files maps archive paths to raw bytes.
    If manifest declares components, they must appear in extra_files.
    """
    if tmp_path is None:
        import tempfile
        tmp_path = Path(tempfile.mkdtemp())

    name = filename or (
        f"{manifest.get('id', 'test').replace('.', '-')}-"
        f"{manifest.get('version', '0.0.1')}.awpkg"
    )
    out = tmp_path / name

    if _HAS_YAML:
        manifest_bytes = _yaml.dump(
            manifest, allow_unicode=True, default_flow_style=False
        ).encode("utf-8")
    else:
        manifest_bytes = json.dumps(manifest).encode("utf-8")

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", manifest_bytes)
        for arc_path, data in (extra_files or {}).items():
            zf.writestr(arc_path, data)

    return out


def make_awpkg_from_fixture(fixture_name: str, tmp_path: Path) -> Path:
    """Build a .awpkg from a tests/fixtures/<fixture_name>/src/ directory.

    Reads the manifest from a manifest.yaml sidecar if present, or from a
    <fixture_name>.manifest.yaml at the fixture root. Falls back to
    generating a manifest from the src contents.
    """
    fixture_dir = FIXTURES / fixture_name
    src_dir = fixture_dir / "src"

    manifest_file = fixture_dir / "manifest.yaml"
    if not manifest_file.exists():
        raise FileNotFoundError(f"no manifest.yaml in {fixture_dir}")

    if _HAS_YAML:
        manifest = _yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    else:
        raise RuntimeError("PyYAML required for fixture loading")

    extra: dict[str, bytes] = {}
    for path in manifest.get("components", {}).values():
        for arc_path in path:
            file_path = src_dir / arc_path
            if file_path.exists():
                extra[arc_path] = file_path.read_bytes()

    return make_awpkg(manifest, extra, tmp_path)


def minimal_manifest(
    pkg_id: str = "com.test.minimal",
    version: str = "1.0.0",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "awpkg": "1.0",
        "id": pkg_id,
        "name": "Test Package",
        "version": version,
        "description": "A minimal test package.",
        "components": {"workflows": ["workflows/test.awp.yaml"]},
    }
    base.update(overrides)
    return base


MINIMAL_WORKFLOW = b"""\
awp: "1.0.0"
workflow:
  name: test_workflow
  description: A minimal test workflow.
orchestration:
  engine: dag
  graph:
    - id: step_one
      type: agent
      agent: assistant
      instructions: "Do something."
      depends_on: []
"""
