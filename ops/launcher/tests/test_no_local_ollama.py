"""ADR-2091: the launcher no longer probes, configures or injects a local Ollama.

Covers the upgrade path (an existing config.json that still carries the
pre-ADR-2091 ``ollama_url`` / ``model`` keys must keep loading) and drives
``corvin setup`` / ``corvin status`` through the real ``python -m corvin``
entry point in a throwaway HOME.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from corvin import cli, config as launcher_cfg, diagnose, docker_backend, native_backend

_LAUNCHER_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = ("ollama", "hermes", "11434", "qwen3")


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(launcher_cfg, "_CONFIG_PATH", path)
    return path


def test_ollama_module_is_gone():
    with pytest.raises(ImportError):
        importlib.import_module("corvin.ollama")


def test_legacy_config_keys_load_and_are_dropped(cfg_path):
    cfg_path.write_text(json.dumps({
        "ollama_url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "bridge": "discord",
    }))
    conf = launcher_cfg.load()
    assert conf["bridge"] == "discord"
    assert "ollama_url" not in conf and "model" not in conf
    assert launcher_cfg.is_configured()
    launcher_cfg.set_value("image", "example/img:1")
    assert "ollama_url" not in json.loads(cfg_path.read_text())


def test_defaults_carry_no_ollama(cfg_path):
    assert launcher_cfg.is_configured() is False
    assert not {"ollama_url", "model"} & set(launcher_cfg.load())


def test_setup_and_start_parsers_have_no_ollama_flags():
    parser = cli._build_parser()
    for cmd in ("setup", "start"):
        with pytest.raises(SystemExit):
            parser.parse_args([cmd, "--ollama-url", "http://x"])
        with pytest.raises(SystemExit):
            parser.parse_args([cmd, "--model", "qwen3:8b"])


def test_setup_yes_runs_without_ollama(cfg_path, monkeypatch, capsys):
    monkeypatch.setattr(docker_backend, "is_docker_available", lambda: True)
    pulled = []
    monkeypatch.setattr(docker_backend, "pull_image", lambda img: pulled.append(img) or True)
    rc = cli.cmd_setup(argparse.Namespace(yes=True, profile=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert pulled == [launcher_cfg._DEFAULTS["image"]]
    assert cfg_path.exists()
    assert not any(w in out.lower() for w in _FORBIDDEN), out


def test_status_does_not_mention_ollama(cfg_path, monkeypatch, capsys):
    monkeypatch.setattr(docker_backend, "is_running", lambda name: False)
    assert cli.cmd_status(argparse.Namespace()) == 0
    out = capsys.readouterr().out.lower()
    assert "gateway" in out
    assert not any(w in out for w in _FORBIDDEN), out


def test_docker_run_injects_no_ollama_env():
    cmd = docker_backend._build_run_cmd("img", "discord", "/tmp/d", "corvinos")
    joined = " ".join(cmd).lower()
    assert not any(w in joined for w in _FORBIDDEN), joined
    assert "--network" not in cmd
    assert f"{docker_backend.CONSOLE_PORT}:{docker_backend.CONSOLE_PORT}" in cmd


def test_native_start_injects_no_ollama_env(cfg_path, monkeypatch):
    captured = {}

    def fake_run(argv, env=None, **kw):
        captured.update(env or {})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(native_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(native_backend, "_find_bridge_manager", lambda: Path("/x/bridge_manager.py"))
    monkeypatch.setattr(native_backend, "_find_bridge_sh", lambda: None, raising=False)
    native_backend.start(foreground=True)
    assert "CORVIN_OLLAMA_BASE_URL" not in captured
    assert "CORVIN_HERMES_MODEL" not in captured


def test_diagnose_has_no_ollama_checks():
    assert not hasattr(diagnose, "check_ollama_running")
    assert not hasattr(diagnose, "check_ollama_autostart")


def _run_cli(args, home):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env["PYTHONPATH"] = os.pathsep.join([str(_LAUNCHER_ROOT), env.get("PYTHONPATH", "")])
    return subprocess.run([sys.executable, "-m", "corvin", *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_real_entry_setup_help_and_status(tmp_path):
    help_run = _run_cli(["setup", "--help"], tmp_path)
    assert help_run.returncode == 0, help_run.stderr
    assert not any(w in help_run.stdout.lower() for w in _FORBIDDEN)

    legacy = tmp_path / ".config" / "corvin-launcher" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"ollama_url": "http://127.0.0.1:11434", "model": "qwen3:8b"}))
    status = _run_cli(["status"], tmp_path)
    assert status.returncode == 0, status.stderr
    assert not any(w in status.stdout.lower() for w in _FORBIDDEN), status.stdout
