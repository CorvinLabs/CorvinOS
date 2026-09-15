"""test_proactive_freecomm.py — ADR-0553/0554 Phase 4: free, unsolicited
multi-message proactive communication (digests / follow-ups) + Voice as the
single synthesis site + per-scope Quiet-Hours.

Covers:
  * Unsolicited needs flag + consent: flag OFF ⇒ denied; flag ON + non-owner
    without grant ⇒ denied; owner (carve-out) + flag ON ⇒ emitted.
  * Multiple + ordering: three unsolicited sends to the owner ⇒ three ordered
    envelopes in a temp outbox (ts monotonic in send order).
  * Voice single-site: an unsolicited digest with voice="summary" synthesizes
    exactly once (build_voice_summary + TTS mocked) ⇒ voice_path set + a
    content-free `proactive.voice_synthesized` audit; a TTS failure degrades to
    text-only + `proactive.voice_skipped` (never blocks); a pre-set voice_path
    ⇒ NO double synthesis.
  * Quiet-Hours: inside the per-(tenant,channel,uid) window ⇒ rate_limited;
    outside ⇒ emitted.
  * Coalescing: the same dedup_key twice within the window ⇒ one delivery.
  * Revoke hard-kill: a non-owner with a grant, after revoke ⇒ further
    unsolicited denied + pending proactive envelopes purged.
  * E2E: owner + REAL ship-dark flag ON ⇒ send_proactive three times with voice
    ⇒ three routed envelopes carrying voice_path + content-free
    `proactive.emitted` (unsolicited) and `proactive.voice_synthesized` events
    on the real hash-chained tenant audit.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
_REPO = HERE.parents[2]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import proactive as P  # noqa: E402
import proactive_consent as pc  # noqa: E402

TENANT = "_default"
CHANNEL = "discord"
UID = "user_42"
CHAT_ID = "987654321098765432"  # 19-digit snowflake > 2^53
TEXT = "Here is your scheduled daily digest of what happened."


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    yield


@pytest.fixture
def outbox(tmp_path):
    return tmp_path / "outbox"


def _outbox_files(outbox: Path) -> list[Path]:
    return list(outbox.glob("*.json")) if outbox.exists() else []


@pytest.fixture
def _flag_on(monkeypatch):
    # _flag_on now takes an explicit tenant_id (H1) — accept + ignore it.
    monkeypatch.setattr(P, "_flag_on", lambda *a, **k: True)


@pytest.fixture
def _house_pass(monkeypatch):
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)


@pytest.fixture
def _owner(monkeypatch):
    """Treat UID as the intrinsic owner — the carve-out that makes consent +
    disclosure pass with no record (the repo channel whitelist does not list the
    synthetic test UID)."""
    import disclosure  # type: ignore
    monkeypatch.setattr(disclosure, "_is_intrinsic_owner", lambda channel, uid: True)


@pytest.fixture
def _audit_spy(monkeypatch):
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        P, "_write_audit_event",
        lambda tenant_id, event_type, details: events.append((tenant_id, event_type, details)),
    )
    return events


# ── unsolicited needs flag + consent ────────────────────────────────────────

def test_unsolicited_flag_off_denied(outbox, _house_pass, _audit_spy):
    """Ship-dark: flag OFF ⇒ every unsolicited send is denied, ZERO writes."""
    res = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="digest",
                           voice="off", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "flag-off"
    assert _audit_spy[-1][2]["solicited"] is False


def test_unsolicited_nonowner_no_grant_denied(outbox, _flag_on, _house_pass, monkeypatch, _audit_spy):
    monkeypatch.setattr(pc, "_is_owner", lambda channel, uid: False)
    res = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="digest",
                           voice="off", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "no-consent"


def test_unsolicited_owner_emitted(outbox, _flag_on, _house_pass, _owner, _audit_spy):
    # No channel whitelist ⇒ owner carve-out applies (consent + disclosure pass).
    res = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="digest",
                           voice="off", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    assert len(_outbox_files(outbox)) == 1
    assert _audit_spy[-1][1] == "proactive.emitted"


# ── multiple + ordering ─────────────────────────────────────────────────────

def test_multiple_sends_ordered(outbox, _flag_on, _house_pass, _owner):
    """Three unsolicited sends to the owner ⇒ three envelopes, ts monotonic."""
    texts = ["digest-1", "digest-2", "digest-3"]
    for t in texts:
        assert P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                                tenant_id=TENANT, text=t, kind="digest",
                                voice="off", outbox_dir=outbox) is P.EmitResult.EMITTED
    envs = [json.loads(f.read_text()) for f in _outbox_files(outbox)]
    assert len(envs) == 3
    envs.sort(key=lambda e: e["ts"])
    assert [e["text"] for e in envs] == texts       # delivered in send order
    ts = [e["ts"] for e in envs]
    assert ts == sorted(ts)                          # ts monotonic
    assert len({e["msg_id"] for e in envs}) == 3     # unique msg_ids


# ── voice single synthesis site ─────────────────────────────────────────────

def test_voice_single_site_synthesized(outbox, _flag_on, _house_pass, _owner, monkeypatch, _audit_spy):
    calls = {"summary": 0, "tts": 0}

    def _bvs(text, *, override=None):
        calls["summary"] += 1
        return "Spoken digest summary."

    def _tts(spoken):
        calls["tts"] += 1
        return "/tmp/digest-note.ogg"

    monkeypatch.setattr(P, "_build_voice_summary", _bvs)
    monkeypatch.setattr(P, "_synthesize_voice_note", _tts)

    res = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="digest",
                           voice="summary", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    assert calls == {"summary": 1, "tts": 1}          # synthesized exactly once
    env = json.loads(_outbox_files(outbox)[0].read_text())
    assert env["voice_path"] == "/tmp/digest-note.ogg"
    # A content-free proactive.voice_synthesized event landed, no text/summary.
    vs = [e for e in _audit_spy if e[1] == "proactive.voice_synthesized"]
    assert len(vs) == 1
    _, _, details = vs[0]
    assert details["mode"] == "summary"
    assert details["chars"] == len("Spoken digest summary.")
    assert details["backend"] == "ogg"
    blob = json.dumps(details)
    assert "Spoken digest" not in blob and TEXT not in blob
    # The emitted audit records voice=True (final path present).
    emitted = [e for e in _audit_spy if e[1] == "proactive.emitted"][-1]
    assert emitted[2]["voice"] is True


def test_voice_tts_error_text_only(outbox, _flag_on, _house_pass, _owner, monkeypatch, _audit_spy):
    """A TTS failure degrades to text-only delivery + voice_skipped — never blocks."""
    monkeypatch.setattr(P, "_build_voice_summary", lambda text, *, override=None: "Spoken.")

    def _boom(spoken):
        raise RuntimeError("TTS engine exploded")

    monkeypatch.setattr(P, "_synthesize_voice_note", _boom)

    res = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="digest",
                           voice="summary", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED                 # text still delivered
    env = json.loads(_outbox_files(outbox)[0].read_text())
    assert "voice_path" not in env                     # text-only
    assert env["text"] == TEXT
    skipped = [e for e in _audit_spy if e[1] == "proactive.voice_skipped"]
    assert len(skipped) == 1
    assert skipped[0][2]["reason"] == "error"
    assert [e for e in _audit_spy if e[1] == "proactive.emitted"][-1][2]["voice"] is False


def test_voice_preset_path_no_double_synth(outbox, _flag_on, _house_pass, _owner, monkeypatch, _audit_spy):
    """A pre-set voice_path (Phase 2 record) wins — no synthesis runs."""
    def _must_not_run(*a, **k):
        raise AssertionError("synthesis must not run when voice_path is pre-set")

    monkeypatch.setattr(P, "_build_voice_summary", _must_not_run)
    monkeypatch.setattr(P, "_synthesize_voice_note", _must_not_run)

    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="digest",
                           voice_path="/tmp/prebuilt.ogg", voice="summary",
                           outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    env = json.loads(_outbox_files(outbox)[0].read_text())
    assert env["voice_path"] == "/tmp/prebuilt.ogg"    # the pre-set path
    assert [e for e in _audit_spy if e[1] == "proactive.voice_synthesized"] == []


# ── quiet hours ─────────────────────────────────────────────────────────────

def test_quiet_hours_rate_limited(outbox, _flag_on, _house_pass, _owner, _audit_spy):
    hour = time.localtime().tm_hour
    # Window covering the current hour ⇒ inside quiet-hours.
    assert P.set_quiet_hours(TENANT, CHANNEL, UID, hour, (hour + 1) % 24) is True
    res = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="digest",
                           voice="off", outbox_dir=outbox)
    assert res is P.EmitResult.RATE_LIMITED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "quiet-hours"

    # Window NOT covering the current hour ⇒ delivers.
    assert P.set_quiet_hours(TENANT, CHANNEL, UID, (hour + 2) % 24, (hour + 3) % 24) is True
    res2 = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                            tenant_id=TENANT, text=TEXT, kind="digest",
                            voice="off", outbox_dir=outbox)
    assert res2 is P.EmitResult.EMITTED
    assert len(_outbox_files(outbox)) == 1


# ── coalescing (dedup_key within the window) ────────────────────────────────

def test_coalescing_same_dedup_key_one_delivery(outbox, _flag_on, _house_pass, _owner, _audit_spy):
    key = "daily-digest-2026-09-03"
    r1 = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                          tenant_id=TENANT, text=TEXT, kind="digest",
                          voice="off", dedup_key=key, outbox_dir=outbox)
    assert r1 is P.EmitResult.EMITTED
    r2 = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                          tenant_id=TENANT, text=TEXT, kind="digest",
                          voice="off", dedup_key=key, outbox_dir=outbox)
    assert r2 is P.EmitResult.RATE_LIMITED
    assert len(_outbox_files(outbox)) == 1
    assert _audit_spy[-1][2]["reason"] == "coalesced"


# ── revoke hard-kill ────────────────────────────────────────────────────────

def test_revoke_hard_kill_denies_and_purges(outbox, _flag_on, _house_pass, monkeypatch, _audit_spy):
    """A non-owner with a grant delivers; after revoke, further unsolicited is
    denied AND the pending proactive envelope is purged."""
    monkeypatch.setattr(pc, "_is_owner", lambda channel, uid: False)
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: True)
    assert pc.grant(TENANT, CHANNEL, UID)["ok"] is True

    # chat_id == uid so the envelope routes to uid (DM), making it purgeable.
    res = P.send_proactive(channel=CHANNEL, chat_id=UID, uid=UID,
                           tenant_id=TENANT, text=TEXT, kind="follow_up",
                           voice="off", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    assert len(_outbox_files(outbox)) == 1

    # Revoke ⇒ hard-kill purge of the pending proactive envelope.
    out = pc.revoke(TENANT, CHANNEL, UID, outbox_dir=outbox)
    assert out["ok"] is True and out["purged"] >= 1
    assert _outbox_files(outbox) == []                 # purged

    # A further unsolicited send ⇒ denied (consent revoked).
    res2 = P.send_proactive(channel=CHANNEL, chat_id=UID, uid=UID,
                            tenant_id=TENANT, text=TEXT, kind="follow_up",
                            voice="off", outbox_dir=outbox)
    assert res2 is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "no-consent"


# ── E2E: owner + REAL flag ON ⇒ three routed voice envelopes + real audit ────

def test_e2e_owner_flag_on_three_voice_digests_and_audit(outbox, _owner, monkeypatch):
    from corvin_core import feature_flags as ff  # real flag module

    # 1) Flip the REAL ship-dark flag ON via the console overlay (operator path).
    ff._write_overlay(TENANT, {"flags": {P.FLAG_ID: True}})
    assert ff.is_enabled(P.FLAG_ID, TENANT) is True

    # 2) Owner carve-out (no whitelist) ⇒ consent + disclosure pass for real.
    #    Only the (CI-undriveable) TTS engine is mocked; real house-rules + audit.
    monkeypatch.setattr(P, "_build_voice_summary",
                        lambda text, *, override=None: "Spoken digest.")
    monkeypatch.setattr(P, "_synthesize_voice_note",
                        lambda spoken: "/tmp/e2e-note.ogg")

    for i in range(3):
        res = P.send_proactive(channel=CHANNEL, chat_id=CHAT_ID, uid=UID,
                               tenant_id=TENANT, text=f"Digest {i}: all clear.",
                               kind="digest", voice="summary", outbox_dir=outbox)
        assert res is P.EmitResult.EMITTED

    files = _outbox_files(outbox)
    assert len(files) == 3
    for f in files:
        env = json.loads(f.read_text())
        assert env["_proactive_contact"] is True and env["kind"] == "digest"
        assert env["channel"] == CHANNEL and env["chat_id"] == CHAT_ID
        assert env["voice_path"] == "/tmp/e2e-note.ogg"

    # Content-free proactive.emitted (unsolicited) + voice_synthesized on the chain.
    audit = P._audit_path(TENANT)
    assert audit.is_file()
    lines = [json.loads(ln) for ln in audit.read_text().splitlines() if ln.strip()]
    emitted = [r for r in lines if r.get("event_type") == "proactive.emitted"]
    synth = [r for r in lines if r.get("event_type") == "proactive.voice_synthesized"]
    assert len(emitted) == 3 and len(synth) == 3
    for r in emitted:
        assert r["details"]["decision"] == "emitted"
        assert r["details"]["solicited"] is False
        assert "text" not in r["details"]
        assert "all clear" not in json.dumps(r["details"])
    for r in synth:
        assert r["details"]["mode"] == "summary"
        assert "Spoken digest" not in json.dumps(r["details"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
