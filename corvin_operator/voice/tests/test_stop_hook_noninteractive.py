"""Regression: the Stop hook must never speak for a programmatic ``claude -p`` run.

Console chat turns, the L44 classifier, Forge repair and context synthesis all
spawn ``claude -p`` (``CLAUDE_CODE_ENTRYPOINT=sdk-cli``). Only some spawn sites
set ``VOICE_HOOK_RECURSION``; the rest had their internal prompts and JSON
replies read aloud on an otherwise idle desktop. The hook now refuses on the
entrypoint itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "stop_hook.sh"


def _run(tmp_path: Path, entrypoint: str | None) -> str:
    cfg = tmp_path / "corvin-voice"
    cfg.mkdir()
    (cfg / "config.json").write_text(json.dumps({"enabled": True}))
    transcript = tmp_path / "t.jsonl"  # never reached when the guard fires
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path),
    }
    if entrypoint is not None:
        env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
    proc = subprocess.run(
        ["bash", str(_HOOK)],
        input=json.dumps({"transcript_path": str(transcript)}),
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    return (cfg / "voice.log").read_text()


@pytest.mark.parametrize("entrypoint", ["sdk-cli", "sdk-ts", "sdk-py"])
def test_programmatic_session_never_speaks(tmp_path, entrypoint):
    log = _run(tmp_path, entrypoint)
    assert "non-interactive session" in log
    assert "launched pipeline" not in log


def test_interactive_session_passes_the_guard(tmp_path):
    log = _run(tmp_path, "cli")
    assert "non-interactive session" not in log


_LIB = _HOOK.parents[1] / "scripts" / "voice_lib.sh"


@pytest.mark.skipif(shutil.which("jq") is None, reason="voice_cfg needs jq")
@pytest.mark.parametrize("value,expected", [("false", "false"), ("true", "true")])
def test_voice_cfg_honours_an_explicit_false(tmp_path, value, expected):
    """jq's ``// empty`` read ``false`` as unset, so the default ``true`` won."""
    cfg = tmp_path / "corvin-voice"
    cfg.mkdir()
    (cfg / "config.json").write_text('{"enabled": %s}' % value)
    out = subprocess.run(
        ["bash", "-c", f'source "{_LIB}"; voice_cfg .enabled true'],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
             "XDG_CONFIG_HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert out.stdout == expected


@pytest.mark.skipif(shutil.which("jq") is None, reason="voice_cfg needs jq")
def test_disabled_voice_never_launches_the_pipeline(tmp_path):
    cfg = tmp_path / "corvin-voice"
    cfg.mkdir()
    (cfg / "config.json").write_text(json.dumps({"enabled": False}))
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "XDG_CONFIG_HOME": str(tmp_path), "CLAUDE_CODE_ENTRYPOINT": "cli"}
    proc = subprocess.run(
        ["bash", str(_HOOK)], input=json.dumps({"transcript_path": str(tmp_path / "t")}),
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    log = (cfg / "voice.log").read_text()
    assert "voice disabled, skipping" in log
    assert "launched pipeline" not in log
