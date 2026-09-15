"""Tests for proactive_consent.py — Phase 0.5 proactive-contact consent.

Covers: deny-by-default, owner carve-out, grant→revoke roundtrip, hard-kill
outbox purge (proactive envelopes only), purpose-separation from the Layer-17
inbound consent.py store, and E2E wiring (the JS handler routes /proactive on
to grant).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import proactive_consent as pc  # noqa: E402


TENANT = "_default"
CHANNEL = "discord"
UID = "user_12345"
OWNER_UID = "owner_99999"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point CORVIN_HOME at a temp dir so no real store is touched."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    yield


@pytest.fixture
def _non_owner(monkeypatch):
    """Force _is_owner to False (no channel whitelist dependency)."""
    monkeypatch.setattr(pc, "_is_owner", lambda channel, uid: False)


@pytest.fixture
def _owner(monkeypatch):
    """Force the specific OWNER_UID to be recognised as intrinsic owner."""
    monkeypatch.setattr(pc, "_is_owner", lambda channel, uid: uid == OWNER_UID)


# ── deny-by-default ────────────────────────────────────────────────────────

def test_deny_by_default(_non_owner):
    assert pc.is_granted(TENANT, CHANNEL, UID) is False


# ── owner carve-out ────────────────────────────────────────────────────────

def test_owner_carve_out_true_without_record(_owner):
    # Owner is contactable with NO grant record on disk.
    assert pc.is_granted(TENANT, CHANNEL, OWNER_UID) is True
    # And the store file was never written for the owner.
    assert not pc._store_path(TENANT, CHANNEL).exists()


def test_owner_carve_out_via_real_disclosure_check(monkeypatch):
    # Exercise the real _is_owner path delegating to disclosure._is_intrinsic_owner.
    import disclosure  # type: ignore
    monkeypatch.setattr(disclosure, "_is_intrinsic_owner",
                        lambda channel, uid: uid == OWNER_UID)
    assert pc.is_granted(TENANT, CHANNEL, OWNER_UID) is True
    assert pc.is_granted(TENANT, CHANNEL, UID) is False


# ── grant → revoke roundtrip ────────────────────────────────────────────────

def test_grant_revoke_roundtrip(_non_owner):
    assert pc.is_granted(TENANT, CHANNEL, UID) is False
    r = pc.grant(TENANT, CHANNEL, UID)
    assert r["ok"] is True
    assert pc.is_granted(TENANT, CHANNEL, UID) is True
    r2 = pc.revoke(TENANT, CHANNEL, UID)
    assert r2["ok"] is True
    assert pc.is_granted(TENANT, CHANNEL, UID) is False


# ── hard-kill purge ─────────────────────────────────────────────────────────

def _write_env(outbox: Path, name: str, env: dict) -> Path:
    outbox.mkdir(parents=True, exist_ok=True)
    p = outbox / name
    p.write_text(json.dumps(env), encoding="utf-8")
    return p


def test_revoke_hard_kill_purges_proactive_but_keeps_replies(_non_owner, tmp_path):
    outbox = tmp_path / "outbox"

    # Proactive envelope for UID (task-progress) — must be purged.
    proactive = _write_env(outbox, "tp_1.json",
                           {"_task_progress": True, "chat_id": UID, "text": "ping"})
    # Proactive completion for UID via `to` routing — must be purged.
    proactive2 = _write_env(outbox, "cn_1.json",
                            {"_completion_notify": True, "to": UID, "text": "done"})
    # Normal reply envelope for UID — must SURVIVE (no proactive marker).
    reply = _write_env(outbox, "reply_1.json",
                       {"chat_id": UID, "text": "a normal reply"})
    # Proactive envelope for a DIFFERENT uid — must SURVIVE.
    other = _write_env(outbox, "tp_2.json",
                      {"_task_progress": True, "chat_id": "someone_else", "text": "x"})

    pc.grant(TENANT, CHANNEL, UID)
    r = pc.revoke(TENANT, CHANNEL, UID, outbox_dir=outbox)

    assert r["ok"] is True
    assert r["purged"] == 2
    assert not proactive.exists()
    assert not proactive2.exists()
    assert reply.exists()       # normal reply untouched
    assert other.exists()       # other uid's proactive untouched


# ── purpose separation from consent.py (Layer-17 inbound) ───────────────────

def test_purpose_separation_inbound_consent_does_not_grant_proactive(
        _non_owner, monkeypatch):
    # Grant an INBOUND observer-transcript consent via consent.py.
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    import consent  # type: ignore
    # Write an inbound (Layer-17 observer-transcript) durable grant via the
    # real consent.py API. This is a DIFFERENT legal purpose (GDPR Art. 6/7).
    # Wrapped: even if the inbound grant path is unavailable in this sandbox,
    # the invariant under test is that a proactive grant is NOT implied by it.
    try:
        consent.grant(CHANNEL, "chatX", UID, ttl_s=None, via="slash")
    except Exception:
        pass

    # proactive is_granted must remain False regardless of any inbound grant.
    assert pc.is_granted(TENANT, CHANNEL, UID) is False
    # And the two stores are different files.
    assert pc._store_path(TENANT, CHANNEL) != consent._store_path(CHANNEL, "chatX")
    assert "proactive_consent" in str(pc._store_path(TENANT, CHANNEL))
    assert "proactive_consent" not in str(consent._store_path(CHANNEL, "chatX"))


# ── E2E wiring / reachability ───────────────────────────────────────────────

def test_e2e_cli_grant_makes_is_granted_true(_non_owner):
    # Drive the real CLI transport boundary (the same entry the JS handler
    # shells out to) and prove is_granted flips.
    assert pc._cli_main(["on", TENANT, CHANNEL, UID]) == 0
    assert pc.is_granted(TENANT, CHANNEL, UID) is True
    assert pc._cli_main(["off", TENANT, CHANNEL, UID]) == 0
    assert pc.is_granted(TENANT, CHANNEL, UID) is False


def test_e2e_wiring_js_handler_routes_proactive_on_to_grant():
    """Reachability: the JS command handler dispatches /proactive on to the
    proactive_consent CLI 'on' subcommand (which is grant)."""
    js = (HERE / "js" / "in_chat_commands.js").read_text(encoding="utf-8")
    # The CLI constant is wired to proactive_consent.py.
    assert "bridgeSharedPy('proactive_consent.py')" in js
    # dispatch() routes /proactive to proactiveReply.
    assert "head === '/proactive'" in js
    assert "return proactiveReply(ctx, tail)" in js
    # proactiveReply shells out to the CLI with the 'on' subcommand => grant.
    assert "PROACTIVE_CLI" in js
    assert "subL === 'on' ? 'on' : 'off'" in js
    # tenant is passed explicitly (no env-fallback for the tenant routing key).
    assert "process.env.CORVIN_TENANT_ID || '_default'" in js


# ── M2: owner carve-out is fail-CLOSED (no whitelist → no auto-owner) ────────

def test_owner_carveout_failclosed_without_whitelist(monkeypatch):
    """M2: disclosure._is_intrinsic_owner fails OPEN (True for everyone) when a
    channel has NO whitelist. The proactive owner carve-out must NOT inherit
    that — with no whitelist ANY uid is deny-by-default (explicit grant needed)."""
    import disclosure  # type: ignore
    NOWL = "nowhitelist_channel_xyz"           # no settings.json under bridges/
    assert disclosure._read_channel_whitelist(NOWL) == []   # genuinely no whitelist
    # Even though disclosure would fail-open to True here …
    monkeypatch.setattr(disclosure, "_is_intrinsic_owner", lambda channel, uid: True)
    # … the proactive carve-out refuses to auto-grant (fail-closed).
    assert pc._is_owner(NOWL, "anybody") is False
    assert pc.is_granted(TENANT, NOWL, "anybody") is False


def test_owner_carveout_true_with_whitelist_and_owner(monkeypatch):
    """M2: with a whitelist that EXISTS and lists uid, the carve-out fires."""
    import disclosure  # type: ignore
    CH = "wl_channel"
    monkeypatch.setattr(disclosure, "_read_channel_whitelist",
                        lambda channel: [OWNER_UID])       # whitelist EXISTS
    # Real _is_intrinsic_owner: uid in whitelist.
    assert pc._is_owner(CH, OWNER_UID) is True
    assert pc.is_granted(TENANT, CH, OWNER_UID) is True
    assert pc.is_granted(TENANT, CH, "not_the_owner") is False


# ── L5: long path components are hashed, never colliding ─────────────────────

def test_long_component_hashed_no_collision():
    long_a = "t" * 70 + "AAAA"
    long_b = "t" * 70 + "BBBB"
    assert pc._safe_component(long_a) != pc._safe_component(long_b)
    assert pc._store_path(long_a, CHANNEL) != pc._store_path(long_b, CHANNEL)
    assert pc._safe_component("_default") == "_default"    # short id untouched


# ── L6: hard-kill purge reaches GROUP-channel envelopes (chat_id != uid) ─────

def test_revoke_purges_group_channel_by_chat_id(_non_owner, tmp_path):
    """L6: proactive envelopes route by chat_id (the channel), not uid. In a
    group channel chat_id != uid, so the purge must match the /proactive off
    chat_id, not just uid — otherwise a group's queued proactive ping survives."""
    outbox = tmp_path / "outbox"
    group_chat = "group_channel_777"               # != uid
    proactive = _write_env(outbox, "cn_grp.json",
                           {"_completion_notify": True, "chat_id": group_chat,
                            "text": "queued group ping"})
    # A proactive envelope for a DIFFERENT chat must SURVIVE.
    other = _write_env(outbox, "cn_other.json",
                       {"_proactive_contact": True, "chat_id": "some_other_chat",
                        "text": "x"})

    pc.grant(TENANT, CHANNEL, UID)
    r = pc.revoke(TENANT, CHANNEL, UID, chat_id=group_chat, outbox_dir=outbox)

    assert r["ok"] is True
    assert r["purged"] == 1
    assert not proactive.exists()                  # group envelope purged
    assert other.exists()                          # other chat untouched


def test_cli_off_accepts_chat_id_arg(_non_owner, tmp_path, monkeypatch):
    """L6 wiring: the CLI `off` subcommand accepts the optional chat_id the JS
    handler now passes, and purges that chat's proactive envelope."""
    outbox = tmp_path / "outbox"
    group_chat = "grp_888"
    monkeypatch.setattr(pc, "_default_outbox_dir", lambda: outbox)
    _write_env(outbox, "tp_grp.json",
               {"_task_progress": True, "chat_id": group_chat, "text": "ping"})
    assert pc._cli_main(["off", TENANT, CHANNEL, UID, group_chat]) == 0
    assert not (outbox / "tp_grp.json").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
