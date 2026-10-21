"""The boot-time key-ring integrity check understands the server's kid aliases.

The licence server publishes every session key three times — ``sess-vN``,
``lic-vN`` and ``ibc-vN`` (one keypair, three token classes) — while the
client ring stores it once under ``sess-vN`` and resolves the aliases when it
verifies tokens. Until 2026-10-21 the integrity check did not, and every boot
of an up-to-date install warned twice "server kid 'lic-v1' not in local
SESSION_SERVER_KEY_RING — update CorvinOS" (seen on a fresh install of main).
"""
from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import session_refresh  # noqa: E402
import validator  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, keys: dict) -> None:
    import urllib.request as ur
    body = json.dumps({"keys": keys, "current_kid": "sess-v1"}).encode()
    monkeypatch.setattr(ur, "urlopen", lambda req, timeout=3: _Resp(body))
    monkeypatch.setattr(session_refresh, "_TEST_MODE_SNAPSHOT", "0")


def test_aliases_of_a_known_session_key_do_not_warn(monkeypatch, caplog):
    k = validator.SESSION_SERVER_KEY_RING["sess-v1"]
    _serve(monkeypatch, {"sess-v1": k, "lic-v1": k, "ibc-v1": k})
    with caplog.at_level(logging.WARNING, logger=session_refresh._log.name):
        assert session_refresh.check_public_key_integrity() is True
    assert "not in local SESSION_SERVER_KEY_RING" not in caplog.text


def test_a_tampered_alias_is_a_mismatch(monkeypatch, caplog):
    k = validator.SESSION_SERVER_KEY_RING["sess-v1"]
    _serve(monkeypatch, {"sess-v1": k, "lic-v1": "AAAA" + k[4:]})
    monkeypatch.setattr(session_refresh, "_audit_license_event", lambda *a, **kw: None)
    with caplog.at_level(logging.CRITICAL, logger=session_refresh._log.name):
        assert session_refresh.check_public_key_integrity() is False
    assert "KEY INTEGRITY MISMATCH" in caplog.text


def test_a_genuinely_new_session_kid_still_warns(monkeypatch, caplog):
    k = validator.SESSION_SERVER_KEY_RING["sess-v1"]
    _serve(monkeypatch, {"sess-v1": k, "sess-v2": "B" * 60})
    with caplog.at_level(logging.WARNING, logger=session_refresh._log.name):
        assert session_refresh.check_public_key_integrity() is True
    assert "'sess-v2' not in local SESSION_SERVER_KEY_RING" in caplog.text
