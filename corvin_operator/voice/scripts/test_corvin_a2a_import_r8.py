"""A2A review round 8, finding 4: ``corvin-a2a import`` must match the console
import when the issuer refuses on ITS licence (HTTP 402) — roll the files back
and exit non-zero instead of printing "[OK]" over a one-way pairing — and it
must show the connection's stored name (the issuer's ``nam``), not ``lbl``.

No network: ``send_friendship_ack`` is stubbed; every directory is a temp dir.
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

import corvin_a2a  # noqa: E402
import a2a_friendship  # noqa: E402

_REAL_GATE = a2a_friendship._ack_url_rejection_reason


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
    return tmp_path


def _token(name: str = "Alice's box") -> str:
    _tok, s = a2a_friendship.create_friendship_token(
        url="http://10.1.2.3:8775", ttl_seconds=3600, label="For Max")
    # Re-sign with an issuer display name (as a real issuer does).
    payload = json.loads(a2a_friendship._b64_dec(s[len(a2a_friendship.TOKEN_PREFIX):].split(".")[0]))
    payload["nam"] = name
    # A foreign issuer: its own binding key, not ours (else own-token refusal).
    import secrets
    payload["bpk"] = secrets.token_hex(32)
    pb = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    import hmac
    sig = hmac.new(a2a_friendship._derive_sig_key(payload["key"]), pb, "sha256").digest()
    return a2a_friendship.TOKEN_PREFIX + a2a_friendship._b64_enc(pb) + "." + a2a_friendship._b64_enc(sig)


def _args(token: str, **kw) -> argparse.Namespace:
    return argparse.Namespace(token=token, url=None, overwrite=False, dry_run=False, **kw)


def test_issuer_licence_refusal_rolls_back_and_fails(env, monkeypatch, capsys):
    monkeypatch.setattr(a2a_friendship, "send_friendship_ack",
                        lambda token, **kw: {"ok": False, "error": "http_402"})
    rc = corvin_a2a._cmd_import_token(_args(_token()))
    assert rc == 1
    assert list((env / "o").glob("*.json")) == []
    assert list((env / "e").glob("*.json")) == []
    cap = capsys.readouterr()
    assert "[OK]" not in cap.out
    assert "a2a_peers_max" in cap.err  # refused for the licence, not another reason


def test_output_shows_the_stored_connection_name(env, monkeypatch, capsys):
    monkeypatch.setattr(a2a_friendship, "send_friendship_ack",
                        lambda token, **kw: {"ok": True, "reachable": True})
    monkeypatch.setattr(a2a_friendship, "remember_peer_instance_id", lambda *a, **kw: None)
    rc = corvin_a2a._cmd_import_token(_args(_token("Alice's box")))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Alice's box" in out
    assert "For Max" not in out
    stored = json.loads(next((env / "e").glob("*.json")).read_text())
    assert stored.get("label") == "Alice's box"



# ── Round 9: `corvin-a2a accept` host-gates a FOREIGN invite's URL ─────────

def _foreign_invite(url: str, rp: str = "/v1/a2a/receive") -> str:
    import secrets
    import time
    import a2a_invite as inv
    p = {"v": 1, "iid": "someone-else", "oid": "peerx", "url": url, "rp": rp,
         "hk": secrets.token_hex(32), "rk": secrets.token_hex(32),
         "pa": ["assistant"], "mt": 300, "iat": time.time(), "exp": time.time() + 3600}
    pb = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
    return inv.TOKEN_PREFIX + inv._b64_enc(pb) + "." + inv._b64_enc(b"\x00" * 32)


@pytest.mark.parametrize("url,rp", [
    ("http://127.0.0.1:8765", "/v1/a2a/receive"),
    ("http://169.254.169.254", "/latest"),
    ("http://10.1.1.1:8765", "@169.254.169.254/latest/meta-data"),
])
def test_cli_accept_refuses_forbidden_invite_hosts(env, monkeypatch, capsys, url, rp):
    # The env fixture stubs the gate out; this test needs the REAL one.
    monkeypatch.setattr(a2a_friendship, "_ack_url_rejection_reason", _REAL_GATE)
    monkeypatch.setattr(corvin_a2a.instance_identity, "get_instance_id", lambda: "local-iid")
    rc = corvin_a2a._cmd_accept(argparse.Namespace(token=_foreign_invite(url, rp),
                                                   overwrite=False, dry_run=False))
    assert rc == 1
    assert list((env / "o").glob("*.json")) == []
    assert list((env / "e").glob("*.json")) == []
    err = capsys.readouterr().err
    assert "rejected" in err or "receive path" in err, err


def test_cli_accept_keeps_accepting_a_lan_peer(env, monkeypatch):
    monkeypatch.setattr(a2a_friendship, "_ack_url_rejection_reason", _REAL_GATE)
    monkeypatch.setattr(corvin_a2a.instance_identity, "get_instance_id", lambda: "local-iid")
    rc = corvin_a2a._cmd_accept(argparse.Namespace(token=_foreign_invite("http://10.1.1.1:8765"),
                                                   overwrite=False, dry_run=False, respond=False))
    assert rc == 0
    assert json.loads((env / "e" / "peerx.json").read_text())["url"] == "http://10.1.1.1:8765/v1/a2a/receive"
