"""ManifestManager must describe the tree it runs from, portably.

Regression for the 2026-09-27 review: manifest.py hard-wired
/home/shumway/projects/CorvinOS, hashed `find` output that carried ABSOLUTE
paths in filesystem order, and walked core/console/.venv (~7 600 files,
~7.6 s inside the boot lifespan). Identical code at two locations therefore
always read as MANIFEST_HASH_MISMATCH.
"""
from __future__ import annotations

from pathlib import Path

from core.deployment import manifest


def _tree(root: Path, body: str) -> None:
    (root / "core" / "pkg").mkdir(parents=True)
    (root / "core" / "pkg" / "mod.py").write_text(body)
    (root / "core" / "pkg" / "other.py").write_text("y = 2\n")
    venv = root / "core" / "console" / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "site.py").write_text(f"# third-party noise {root}\n")


def test_identical_code_at_different_locations_hashes_equal(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "deeper" / "b"
    _tree(a, "x = 1\n")
    _tree(b, "x = 1\n")
    monkeypatch.setattr(manifest, "REPO_ROOT", a)
    ha = manifest.ManifestManager.calculate_manifest_hash(["core/"])
    monkeypatch.setattr(manifest, "REPO_ROOT", b)
    hb = manifest.ManifestManager.calculate_manifest_hash(["core/"])
    assert ha == hb


def test_code_change_changes_hash(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    _tree(a, "x = 1\n")
    _tree(b, "x = 2\n")
    monkeypatch.setattr(manifest, "REPO_ROOT", a)
    ha = manifest.ManifestManager.calculate_manifest_hash(["core/"])
    monkeypatch.setattr(manifest, "REPO_ROOT", b)
    hb = manifest.ManifestManager.calculate_manifest_hash(["core/"])
    assert ha != hb


def test_git_sha_comes_from_the_running_tree():
    import subprocess

    expected = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(manifest.__file__).resolve().parents[2], text=True
    ).strip()
    assert manifest.ManifestManager.get_git_sha() == expected
