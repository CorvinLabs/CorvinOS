"""A personal settings block never reaches the command line (ADR-2327 item 6, review round 9)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from corvin_operator.bridges.shared.agents import claude_code as cc  # noqa: E402

BLOCK = "[Personal settings of this user, from their private personal space. x]\nverbosity: brief"


def test_personal_block_is_refused_when_its_file_cannot_be_written():
    def _no_file(*_a, **_k):
        raise OSError("disk full")
    started = []
    with mock.patch.object(cc.tempfile, "mkstemp", _no_file), \
            mock.patch.object(cc.subprocess, "Popen", side_effect=lambda *a, **k: started.append(a)):
        gen = cc.ClaudeCodeEngine().spawn("hello", system=BLOCK, working_dir=Path(tempfile.mkdtemp()))
        with pytest.raises(RuntimeError, match="command line"):
            next(gen)
    assert started == []    # no process was started with the block on its command line
