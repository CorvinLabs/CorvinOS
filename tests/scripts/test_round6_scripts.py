"""Adversarial review round 6 — scripts that leaked a key, wrote into the live
tree, or referenced files that no longer exist."""
from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"

CSRF_FIXERS = [
    "phase9_csrf_bulk_fixer_from_todo.py",
    "phase9_csrf_bulk_fixer_from_todo_v2.py",
    "phase9_csrf_bulk_fixer_mainline_only.py",
    "phase9_csrf_bulk_fixer.py",
]
CSRF_INVENTORIES = ["phase9_csrf_inventory.py", "phase9_csrf_inventory_mainline.py"]


def _env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["CORVIN_HOME"] = str(tmp_path / "corvin-home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "home" / ".config")
    return env


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tts_runner_never_logs_key_material(monkeypatch, caplog):
    key = "sk-proj-" + "A1b2C3d4E5" * 5
    monkeypatch.setenv("OPENAI_API_KEY", key)
    fake = types.ModuleType("openai")
    fake.OpenAI = lambda **kw: object()  # offline stand-in: no network, no SDK
    monkeypatch.setitem(sys.modules, "openai", fake)
    path_before = list(sys.path)
    try:
        mod = _load(SCRIPTS / "run_openai_tts_e2e_tests.py", "r6_tts_runner")
        with caplog.at_level(logging.INFO):
            runner = mod.E2ETestRunner()
    finally:
        sys.path[:] = path_before
    assert runner.openai_client is not None  # positive control: the key branch ran
    assert "API key present: True" in caplog.text
    assert key[:12] not in caplog.text
    assert mod.REPO_ROOT == REPO


@pytest.mark.parametrize("name", ["run_openai_tts_e2e_tests.py", "generate_concept_videos.py"])
def test_no_hardwired_live_tree_paths(name):
    src = (SCRIPTS / name).read_text()
    assert "/home/shumway" not in src
    assert 'projects/CorvinOS/' not in src


def test_concept_videos_output_dir_is_this_checkout():
    mod = _load(SCRIPTS / "generate_concept_videos.py", "r6_concept_videos")
    assert mod.OUTPUT_DIR == REPO / "outputs"


@pytest.mark.parametrize("name", CSRF_FIXERS)
def test_csrf_bulk_fixers_refuse(tmp_path, name):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        capture_output=True, text=True, timeout=60, env=_env(tmp_path), cwd=tmp_path,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "defused" in proc.stderr
    assert "core.security.csrf" in proc.stderr
    assert "/home/shumway" not in (SCRIPTS / name).read_text()


def _mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "routes.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
        "@router.post('/x')\ndef create_x():\n    return {}\n"
    )
    return root


@pytest.mark.parametrize("name", CSRF_INVENTORIES)
def test_csrf_inventory_writes_nothing_by_default(tmp_path, name):
    root = _mini_repo(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name), "--repo-root", str(root)],
        capture_output=True, text=True, timeout=60, env=_env(tmp_path), cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Vulnerable endpoints (no CSRF protection): 1" in proc.stdout  # positive control
    assert not (root / "TODO_CSRF_ENDPOINTS.txt").exists()
    assert not (REPO / "TODO_CSRF_ENDPOINTS.txt").exists()


@pytest.mark.parametrize("name", CSRF_INVENTORIES)
def test_csrf_inventory_writes_only_to_output(tmp_path, name):
    root = _mini_repo(tmp_path)
    out = tmp_path / "todo.txt"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name), "--repo-root", str(root), "--output", str(out)],
        capture_output=True, text=True, timeout=60, env=_env(tmp_path), cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pkg/routes.py" in out.read_text()
    assert not (root / "TODO_CSRF_ENDPOINTS.txt").exists()


def test_rebuild_from_carve_help_names_real_default(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "recovery" / "rebuild_from_carve.py"), "--help"],
        capture_output=True, text=True, timeout=60, env=_env(tmp_path), cwd=tmp_path,
    )
    # --help may exit before or after imports; either way the text is printed
    out = " ".join((proc.stdout + proc.stderr).split())
    assert "global/instance_id.json" in out, out[-2000:]
    assert "CORVIN_INSTANCE_ID_PATH" in out
