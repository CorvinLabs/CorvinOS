"""``corvin-a2a create-token`` / ``import-token`` on a Free install (round 2 of the fresh-system
review, 2026-10-21): the console answered 402 ``member_seat_required`` before writing anything,
the CLI still created the token, wrote the origin + endpoint files and printed ``UNREACHABLE``
(``test_fresh_a2a_two_instances_live.py`` measured it). Same gate, same refusal, nothing written.

No network: the licence resolver is the only variable (``licence_tier`` marker, see conftest).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
_SHARED = _SCRIPTS.parents[1] / "bridges" / "shared"
for _p in (str(_SCRIPTS), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import a2a_friendship  # noqa: E402
import corvin_a2a  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for name, sub in (("REMOTE_ORIGINS_DIR", "o"), ("REMOTE_ENDPOINTS_DIR", "e"),
                      ("REMOTE_PENDING_FRIENDSHIPS_DIR", "p")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(name, str(tmp_path / sub))
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_A2A_BIND_KEY_PATH", str(tmp_path / "home" / "bindkey"))
    monkeypatch.setenv("CORVIN_A2A_URL", "http://10.9.9.9:8775")
    monkeypatch.setenv("CORVIN_A2A_RELAY_URL", "off")
    monkeypatch.setattr(a2a_friendship, "_ack_url_rejection_reason", lambda url: None)
    # never reach a peer
    monkeypatch.setattr(a2a_friendship, "send_friendship_ack",
                        lambda token, **kw: {"ok": True, "reachable": True, "via": "direct"})
    return tmp_path


def _create_args(**kw) -> argparse.Namespace:
    base = dict(url="http://10.1.2.3:8775", label="x", ttl="24h", scope=None, max_call_ttl=0,
                remember_url=False, json=True, qr=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _foreign_token() -> str:
    _tok, s = a2a_friendship.create_friendship_token(url="http://10.1.2.3:8775", ttl_seconds=3600, label="For Max")
    payload = json.loads(a2a_friendship._b64_dec(s[len(a2a_friendship.TOKEN_PREFIX):].split(".")[0]))
    import hmac
    import secrets
    payload["bpk"] = secrets.token_hex(32)  # a foreign issuer, not our own binding key
    pb = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(a2a_friendship._derive_sig_key(payload["key"]), pb, "sha256").digest()
    return a2a_friendship.TOKEN_PREFIX + a2a_friendship._b64_enc(pb) + "." + a2a_friendship._b64_enc(sig)


@pytest.mark.licence_tier("free")
def test_free_install_create_token_is_refused_and_writes_nothing(env, capsys):
    rc = corvin_a2a._cmd_create_token(_create_args())
    assert rc == 1
    cap = capsys.readouterr()
    assert "member seat" in cap.err and "nothing was written" in cap.err, cap.err
    assert "corvin-a2a:ft1:" not in cap.out
    assert list((env / "p").glob("*")) == [], "a pending record was written on a refused create"


@pytest.mark.licence_tier("free")
def test_free_install_import_token_is_refused_and_writes_nothing(env, capsys):
    rc = corvin_a2a._cmd_import_token(argparse.Namespace(token=_foreign_token(), url=None, overwrite=False, dry_run=False))
    assert rc == 1
    assert "member seat" in capsys.readouterr().err
    assert list((env / "o").glob("*.json")) == [] and list((env / "e").glob("*.json")) == []


def test_member_install_create_and_import_proceed(env, capsys):
    rc = corvin_a2a._cmd_create_token(_create_args())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["token"].startswith("corvin-a2a:ft1:")
    assert len(list((env / "p").glob("*"))) == 1
    rc = corvin_a2a._cmd_import_token(argparse.Namespace(token=_foreign_token(), url=None, overwrite=False, dry_run=False))
    assert rc == 0, capsys.readouterr()
    assert len(list((env / "o").glob("*.json"))) == 1


def test_refusal_is_fail_closed_when_the_gate_is_unavailable(env, monkeypatch, capsys):
    import a2a_license_gate

    def boom(**_k):
        raise RuntimeError("licensing broke")
    monkeypatch.setattr(a2a_license_gate, "require_a2a_network", boom)
    assert corvin_a2a._cmd_create_token(_create_args()) == 1
    assert "licence check unavailable" in capsys.readouterr().err
