"""ADR-0154 M5 (Structural Embedding) — path-gate coupling E2E (pytest).

The path-gate hook runs as a fresh subprocess per tool call, so M5 keys off a
signal the subprocess can actually observe: a license token PRESENT on disk that
fails to validate (present-but-invalid). Asserts the load-bearing safety
property: default-OFF is a no-op, no-token (free tier) never denies, and the
gate can only ADD a deny — it never fails the path-gate open.
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent
for _p in (str(_HOOK_DIR), str(_HOOK_DIR.resolve().parents[1] / "license")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os  # noqa: E402

os.environ.setdefault("CORVIN_HOME", tempfile.mkdtemp(prefix="ota-pathgate-"))

import path_gate  # noqa: E402

WRITE_SAFE = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/ota_safe_file.txt"}}


@pytest.fixture()
def fake_validator(monkeypatch):
    """Inject a controllable license.validator so is_loaded() is deterministic."""
    state = {"loaded": False}
    mod = types.ModuleType("license.validator")
    mod.load_license_from_env = lambda *a, **k: None  # type: ignore[attr-defined]
    mod.is_loaded = lambda: state["loaded"]  # type: ignore[attr-defined]
    pkg = sys.modules.get("license") or types.ModuleType("license")
    monkeypatch.setitem(sys.modules, "license", pkg)
    monkeypatch.setitem(sys.modules, "license.validator", mod)
    return state


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv("CORVIN_OTA_PATH_GATE", raising=False)
    monkeypatch.delenv("CORVIN_LICENSE_KEY", raising=False)
    yield


def test_default_off_is_noop(monkeypatch):
    # Even with a present+invalid token, flag OFF → unchanged behavior.
    monkeypatch.setenv("CORVIN_LICENSE_KEY", "CORVIN-bogus.token.sig")
    allow, _ = path_gate.check(WRITE_SAFE)
    assert allow is True


def test_flag_on_no_token_allows(monkeypatch):
    monkeypatch.setenv("CORVIN_OTA_PATH_GATE", "1")
    # No token present → free tier → never denies.
    monkeypatch.setattr(path_gate, "_ota_license_token_present", lambda: False)
    allow, _ = path_gate.check(WRITE_SAFE)
    assert allow is True


def test_flag_on_token_present_but_invalid_denies(monkeypatch, fake_validator):
    monkeypatch.setenv("CORVIN_OTA_PATH_GATE", "1")
    monkeypatch.setattr(path_gate, "_ota_license_token_present", lambda: True)
    fake_validator["loaded"] = False  # present but did not activate
    allow, reason = path_gate.check(WRITE_SAFE)
    assert allow is False
    assert "ADR-0154 M5" in reason


def test_flag_on_valid_license_allows(monkeypatch, fake_validator):
    monkeypatch.setenv("CORVIN_OTA_PATH_GATE", "1")
    monkeypatch.setattr(path_gate, "_ota_license_token_present", lambda: True)
    fake_validator["loaded"] = True  # valid license → no deny
    allow, _ = path_gate.check(WRITE_SAFE)
    assert allow is True


def test_helper_off_is_noop():
    deny, reason = path_gate._ota_structural_deny("Write")
    assert deny is False and reason == ""


def test_helper_ignores_non_gated_tool(monkeypatch):
    monkeypatch.setenv("CORVIN_OTA_PATH_GATE", "1")
    monkeypatch.setattr(path_gate, "_ota_license_token_present", lambda: True)
    deny, _ = path_gate._ota_structural_deny("Read")  # not a write-class tool
    assert deny is False


def test_never_fails_open_on_protected_path(monkeypatch):
    # Even with the flag OFF, a genuinely protected path must still deny — the
    # OTA layer must never relax the base gate.
    home = Path(os.environ["CORVIN_HOME"])
    audit = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(audit)}}
    allow, _ = path_gate.check(payload)
    assert allow is False  # base protection intact regardless of OTA
