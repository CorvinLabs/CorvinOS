"""An operator /cancel is a stop, not a crash, on Windows too (round 10, 2026-11-03).

Windows has no signal exit codes: taskkill /F and TerminateProcess end the CLI with
exit code 1, so the exit-code check alone reported every /cancel as
"Claude API call failed". The /cancel flag is stamped before the kill (_cancel_chat).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import adapter  # noqa: E402


@pytest.fixture()
def cancel_flag():
    key = "web:cancel-test"
    with adapter._TURN_CANCEL_LOCK:
        adapter._TURN_CANCEL_REQUESTS.add(key)
    yield key
    with adapter._TURN_CANCEL_LOCK:
        adapter._TURN_CANCEL_REQUESTS.discard(key)


def test_signal_exit_codes_are_a_stop_everywhere():
    for rc in adapter._STOPPED_EXIT_CODES:
        assert adapter._operator_stopped(rc, "web:none")


def test_windows_exit_1_with_a_stamped_cancel_is_a_stop(monkeypatch, cancel_flag):
    monkeypatch.setattr(adapter.os, "name", "nt")
    assert adapter._operator_stopped(1, cancel_flag)


def test_exit_1_without_a_cancel_is_still_a_failure(monkeypatch):
    monkeypatch.setattr(adapter.os, "name", "nt")
    assert not adapter._operator_stopped(1, "web:nobody-cancelled")
    assert not adapter._operator_stopped(0, "web:nobody-cancelled")


def test_posix_keeps_the_exit_code_contract(monkeypatch, cancel_flag):
    monkeypatch.setattr(adapter.os, "name", "posix")
    assert not adapter._operator_stopped(1, cancel_flag)
