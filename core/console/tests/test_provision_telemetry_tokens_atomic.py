"""Regression: instance/telemetry token persistence must be atomic and
0600-from-creation (adversarial review finding).

Before this fix, `provision_telemetry_tokens()` wrote each token via a plain
`write_text()` followed by a separate `chmod()` — no atomicity, and a brief
window where the file existed at the process's default (potentially
permissive) mode before being narrowed. Two processes racing to provision
for the first time (e.g. a bridge daemon and the web console booting
together) could interleave two different token-endpoint responses into a
mismatched pair that `ensure_ping_tokens()`'s existence-only check can never
detect or self-heal.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from corvin_console.aco import htrace_consent as hc


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_both_tokens_are_written_at_0600_atomically(tmp_path):
    home = tmp_path / ".corvin"
    (home / "aco" / "telemetry").mkdir(parents=True, exist_ok=True)

    payload = {"instance_token": "itok-abc", "telemetry_token": "ttok-xyz"}
    with patch("urllib.request.OpenerDirector.open", return_value=_FakeResponse(payload)):
        ok = hc.provision_telemetry_tokens(home, "inst-1")

    assert ok is True
    inst_p = hc._instance_token_path(home)
    tel_p = hc._telemetry_token_path(home)
    assert inst_p.read_text(encoding="utf-8") == "itok-abc"
    assert tel_p.read_text(encoding="utf-8") == "ttok-xyz"
    assert _mode(inst_p) == 0o600
    assert _mode(tel_p) == 0o600


def test_no_temp_files_left_behind_on_success(tmp_path):
    home = tmp_path / ".corvin"
    (home / "aco" / "telemetry").mkdir(parents=True, exist_ok=True)

    payload = {"instance_token": "itok", "telemetry_token": "ttok"}
    with patch("urllib.request.OpenerDirector.open", return_value=_FakeResponse(payload)):
        hc.provision_telemetry_tokens(home, "inst-1")

    leftovers = list(hc._instance_token_path(home).parent.glob(".*.tmp"))
    assert not leftovers, f"temp files leaked: {leftovers}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
