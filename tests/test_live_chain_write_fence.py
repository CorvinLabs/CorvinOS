"""Write fence for the LIVE audit chain (root conftest, "live-chain write fence").

A test that opens the repo-local live chain for writing must fail at the open,
attributed to that test. The growth check in the root conftest cannot do this:
the running services append to the same chain with the same instance id, so a
growth diff cannot tell a test write from a service write (measured 2026-10-19:
a ``remote_trigger_sender.ping`` record every 25-40 s with no test running).

The live path below is the repo-local home every checkout resolves to when
``CORVIN_HOME`` is unset. The tests only ever open it with the write fence in
place: the fence refuses before the file is touched, so no byte is written.
"""
from __future__ import annotations

import errno
import io
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_CHAIN = REPO_ROOT / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"


def test_append_open_of_live_chain_is_refused():
    with pytest.raises(PermissionError) as exc:
        open(LIVE_CHAIN, "a")  # nothing is written: the fence refuses at open()
    assert exc.value.errno == errno.EACCES


def test_pathlib_write_of_live_chain_is_refused():
    with pytest.raises(PermissionError):
        with LIVE_CHAIN.open("ab"):
            pass


def test_io_open_and_low_level_write_of_live_chain_are_refused():
    with pytest.raises(PermissionError):
        io.open(LIVE_CHAIN, "a")
    with pytest.raises(PermissionError):
        os.open(LIVE_CHAIN, os.O_WRONLY | os.O_APPEND)


def test_read_of_live_chain_stays_allowed():
    # The boot tripwire and every reader only read the chain; the fence is write-only.
    if not LIVE_CHAIN.exists():
        pytest.skip("no live chain on this host")
    with open(LIVE_CHAIN, "rb") as fh:
        assert fh.read(1)


def test_sandbox_chain_write_is_allowed(tmp_path):
    chain = tmp_path / "corvin_home" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    with open(chain, "a", encoding="utf-8") as fh:
        fh.write("")
    assert chain.exists()


# ── Licence decision writer (license.capability_decision) ─────────────────────
# Path under test: remote_trigger_sender.ping -> a2a_license_gate.require_a2a_network
# -> capability_api.require_capability -> _audit_capability_decision -> write_event.
# Without CORVIN_HOME, corvin_home() falls back to the repo-local ``.corvin`` root,
# which IS the live chain. The probe below records every append attempt on the live
# path and refuses it, so the red run writes nothing; a green run never attempts it.

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _record_live_append_attempts(monkeypatch) -> list[str]:
    attempts: list[str] = []
    real_os_open = os.open

    def probe_open(path, flags, *args, **kwargs):
        if os.fsdecode(os.fspath(path)) == str(LIVE_CHAIN) and flags & _WRITE_FLAGS:
            attempts.append(str(path))
            raise PermissionError(errno.EACCES, "fence probe: live chain append refused", str(path))
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", probe_open)
    return attempts


def test_licence_decision_without_corvin_home_never_appends_to_live_chain(monkeypatch):
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        pytest.skip("the guard only applies inside a pytest run")
    attempts = _record_live_append_attempts(monkeypatch)
    monkeypatch.delenv("CORVIN_HOME", raising=False)
    from corvin_operator.license import capability_api

    committed = capability_api._audit_capability_decision(
        "_default", "a2a.network", "member", "allow", 1, 1, "remote_trigger_sender.ping",
    )
    assert attempts == [], "licence decision from a test run reached the live chain"
    assert committed is False


def test_licence_decision_with_sandbox_corvin_home_lands_in_sandbox(monkeypatch, tmp_path):
    # Positive control: the probe-free path really writes, into the sandbox only.
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))
    from corvin_operator.license import capability_api

    committed = capability_api._audit_capability_decision(
        "_default", "a2a.network", "member", "allow", 1, 1, "remote_trigger_sender.ping",
    )
    chain = tmp_path / "corvin_home" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    assert committed is True
    assert chain.exists() and "license.capability_decision" in chain.read_text(encoding="utf-8")
