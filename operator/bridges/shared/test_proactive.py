"""Tests for proactive.py — ADR-0553 Phase 1 emit_proactive primitive + gate.

Covers: ship-dark (flag off ⇒ denied, 0 outbox writes), the fail-closed gate in
order (bad-kind / consent / house-rules / disclosure), rate-limit flood + dedup
coalescing + quiet-hours, content-free hash-chained audit (with a mutation
proof), never-raise (internal exception ⇒ error), and an E2E-wiring proof that
drives the REAL feature-flag, REAL consent grant, REAL disclosure store, REAL
house-rules gate and REAL forge audit chain, then proves exactly one correctly
routed envelope lands in the outbox and the audit event is content-free.
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
# corvin_core (feature_flags) + forge live outside shared/ — the bridge process
# runs with these on PYTHONPATH; mirror that for the real-flag / real-audit E2E.
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
TEXT = "Your background task finished successfully."


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
def _gates_pass(monkeypatch):
    """Force consent / house-rules / disclosure to PASS (isolate one gate)."""
    monkeypatch.setattr(P, "_consent_ok", lambda t, c, u: True)
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: True)


@pytest.fixture
def _audit_spy(monkeypatch):
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        P, "_write_audit_event",
        lambda tenant_id, event_type, details: events.append((tenant_id, event_type, details)),
    )
    return events


# ── ship-dark ───────────────────────────────────────────────────────────────

def test_ship_dark_flag_off_denies_zero_writes(outbox, _audit_spy):
    # No _flag_on override: default resolves OFF (overlay absent → registry default).
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []          # ZERO outbox writes
    assert len(_audit_spy) == 1
    _, evt, details = _audit_spy[0]
    assert evt == "proactive.denied"
    assert details["reason"] == "flag-off"


# ── fail-closed gate, in order ───────────────────────────────────────────────

def test_bad_kind_denied_failclosed(outbox, _flag_on, _gates_pass, _audit_spy):
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="totally_unknown", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "bad-kind"


def test_consent_deny_by_default(outbox, _flag_on, monkeypatch, _audit_spy):
    # Non-owner, no grant → deny-by-default. Other gates would pass.
    monkeypatch.setattr(pc, "_is_owner", lambda channel, uid: False)
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: True)
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "no-consent"


def test_house_rules_deny(outbox, _flag_on, monkeypatch, _audit_spy):
    monkeypatch.setattr(P, "_consent_ok", lambda t, c, u: True)
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: False)
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: True)
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "house-rules"


def test_disclosure_missing_denied(outbox, _flag_on, monkeypatch, _audit_spy):
    monkeypatch.setattr(P, "_consent_ok", lambda t, c, u: True)
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: False)
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "no-disclosure"


# ── happy path: all gates pass → exactly ONE envelope ────────────────────────

def test_all_gates_pass_emits_one_envelope(outbox, _flag_on, monkeypatch, _audit_spy):
    # Real consent grant (non-owner) + disclosure/house-rules forced pass.
    monkeypatch.setattr(pc, "_is_owner", lambda channel, uid: False)
    pc.grant(TENANT, CHANNEL, UID)
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: True)

    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion",
                           voice_path="/tmp/note.ogg", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    files = _outbox_files(outbox)
    assert len(files) == 1
    env = json.loads(files[0].read_text())
    assert env["_proactive_contact"] is True
    assert env["kind"] == "completion"
    assert env["channel"] == CHANNEL
    assert env["chat_id"] == CHAT_ID           # string, precision preserved
    assert env["text"] == TEXT
    assert env["voice_path"] == "/tmp/note.ogg"
    assert env["tenant_id"] == TENANT
    assert _audit_spy[-1][1] == "proactive.emitted"


# ── rate / flood / dedup / quiet-hours ───────────────────────────────────────

def test_rate_limit_flood(outbox, _flag_on, _gates_pass, monkeypatch, _audit_spy):
    monkeypatch.setattr(P, "MAX_PER_WINDOW", 3)
    for _ in range(3):
        assert P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                                uid=UID, text=TEXT, kind="progress",
                                outbox_dir=outbox) is P.EmitResult.EMITTED
    # N+1 within the window → rate_limited (flood).
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="progress", outbox_dir=outbox)
    assert res is P.EmitResult.RATE_LIMITED
    assert len(_outbox_files(outbox)) == 3
    assert _audit_spy[-1][2]["reason"] == "flood"


def test_dedup_key_coalesces(outbox, _flag_on, _gates_pass, _audit_spy):
    r1 = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                          uid=UID, text=TEXT, kind="digest", dedup_key="daily-2026-09-02",
                          outbox_dir=outbox)
    assert r1 is P.EmitResult.EMITTED
    # Same dedup_key in the window → coalesce, no second send.
    r2 = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                          uid=UID, text=TEXT, kind="digest", dedup_key="daily-2026-09-02",
                          outbox_dir=outbox)
    assert r2 is P.EmitResult.RATE_LIMITED
    assert len(_outbox_files(outbox)) == 1
    assert _audit_spy[-1][2]["reason"] == "coalesced"


def test_quiet_hours_rate_limited(outbox, _flag_on, _gates_pass, monkeypatch, _audit_spy):
    monkeypatch.setattr(P, "_in_quiet_hours", lambda now: True)
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="follow_up", outbox_dir=outbox)
    assert res is P.EmitResult.RATE_LIMITED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "quiet-hours"


# ── audit content-free + mutation proof ──────────────────────────────────────

def test_audit_content_free_and_mutation(outbox, _flag_on, _gates_pass, _audit_spy):
    dedup = "secret-correlation-key"
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text="SENSITIVE PRIVATE MESSAGE BODY",
                           kind="completion", dedup_key=dedup,
                           voice_path="/tmp/x.ogg", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    # MUTATION invariant: silencing the audit call empties this spy → red.
    assert len(_audit_spy) == 1
    _, evt, details = _audit_spy[0]
    assert evt == "proactive.emitted"
    # NO message text anywhere in the audit details.
    blob = json.dumps(details)
    assert "SENSITIVE" not in blob
    assert "text" not in details
    # dedup_key only as a full sha256 hash, never raw.
    import hashlib
    assert details["dedup_key_hash"] == hashlib.sha256(dedup.encode()).hexdigest()
    assert len(details["dedup_key_hash"]) == 64
    assert dedup not in blob
    assert details["voice"] is True
    assert details["decision"] == "emitted"
    assert details["reason"] == "ok"
    assert details["lom"]


# ── never-raise ──────────────────────────────────────────────────────────────

def test_never_raise_on_internal_error(outbox, _flag_on, _gates_pass, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("broken store / envelope build blew up")
    monkeypatch.setattr(P, "_build_envelope", _boom)
    # Must NOT raise; degrades to ERROR, no envelope written.
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.ERROR
    assert _outbox_files(outbox) == []


# ── E2E-wiring proof: real flag + real consent + real disclosure + real audit ─

def test_e2e_real_flag_real_gates_one_envelope_and_audit(outbox, monkeypatch):
    from corvin_core import feature_flags as ff  # real flag module

    # 1) Flip the REAL ship-dark flag ON via the console overlay (operator path).
    ff._write_overlay(TENANT, {"flags": {P.FLAG_ID: True}})
    assert ff.is_enabled(P.FLAG_ID, TENANT) is True

    # 2) Real proactive-contact consent grant for a non-owner uid.
    monkeypatch.setattr(pc, "_is_owner", lambda channel, uid: False)
    import disclosure  # type: ignore
    monkeypatch.setattr(disclosure, "_is_intrinsic_owner", lambda channel, uid: False)
    assert pc.grant(TENANT, CHANNEL, UID)["ok"] is True

    # 3) Real disclosure store seeded so has_seen(uid) is True (no CLAG needed).
    dstore = disclosure._store_path(CHANNEL, CHAT_ID)
    dstore.parent.mkdir(parents=True, exist_ok=True)
    dstore.write_text(json.dumps({UID: {"action": "joined", "first_seen": 1.0}}))
    assert disclosure.has_seen(CHANNEL, CHAT_ID, UID) is True

    # 4) Real house-rules gate (verified-clean policy) over benign text — passes.
    #    Real forge audit chain (no audit spy) writes to the tenant audit.jsonl.
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED

    # Exactly ONE correctly-routed envelope in the outbox.
    files = _outbox_files(outbox)
    assert len(files) == 1
    env = json.loads(files[0].read_text())
    assert env["_proactive_contact"] is True and env["kind"] == "completion"
    assert env["chat_id"] == CHAT_ID and env["channel"] == CHANNEL

    # Real, content-free audit event landed in the hash-chained tenant chain.
    audit = P._audit_path(TENANT)
    assert audit.is_file()
    emitted = [json.loads(ln) for ln in audit.read_text().splitlines()
               if '"proactive.emitted"' in ln]
    assert emitted, "expected a proactive.emitted audit event on the chain"
    rec = emitted[-1]
    assert rec["details"]["decision"] == "emitted"
    assert "text" not in rec["details"]
    assert TEXT not in json.dumps(rec["details"])


# ── Phase 2: solicited vs unsolicited (ADR-0553 amendment) ───────────────────

def test_solicited_skips_flag_consent_disclosure_and_delivers(outbox, monkeypatch, _audit_spy):
    """A solicited response (answer to an explicit /task action) delivers on a
    default install: flag OFF, consent deny-by-default AND disclosure-not-shown
    are all SKIPPED. House-rules still runs (forced pass here)."""
    monkeypatch.setattr(P, "_consent_ok", lambda t, c, u: False)      # would deny
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: False)  # would deny
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    # NO _flag_on override → default resolves OFF.
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion",
                           solicited=True, outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    assert len(_outbox_files(outbox)) == 1
    _, evt, details = _audit_spy[-1]
    assert evt == "proactive.emitted"
    assert details["decision"] == "emitted"
    assert details["solicited"] is True


def test_solicited_still_enforces_house_rules(outbox, monkeypatch, _audit_spy):
    """solicited keeps the fail-closed house-rules gate — a violating completion
    is held, not emitted."""
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: False)
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion",
                           solicited=True, outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _outbox_files(outbox) == []
    assert _audit_spy[-1][2]["reason"] == "house-rules"
    assert _audit_spy[-1][2]["solicited"] is True


def test_solicited_still_rate_limited(outbox, monkeypatch, _audit_spy):
    """solicited keeps the rate/flood bound."""
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    monkeypatch.setattr(P, "MAX_PER_WINDOW", 2)
    for _ in range(2):
        assert P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                                uid=UID, text=TEXT, kind="progress",
                                solicited=True, outbox_dir=outbox) is P.EmitResult.EMITTED
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="progress",
                           solicited=True, outbox_dir=outbox)
    assert res is P.EmitResult.RATE_LIMITED
    assert _audit_spy[-1][2]["reason"] == "flood"


def test_unsolicited_default_needs_flag(outbox, _audit_spy):
    """The default (solicited=False) runs the FULL gate — flag OFF denies."""
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _audit_spy[-1][2]["reason"] == "flag-off"
    assert _audit_spy[-1][2]["solicited"] is False


def test_unsolicited_with_flag_on_still_needs_consent(outbox, _flag_on, monkeypatch, _audit_spy):
    monkeypatch.setattr(P, "_consent_ok", lambda t, c, u: False)
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res is P.EmitResult.DENIED
    assert _audit_spy[-1][2]["reason"] == "no-consent"


def test_envelope_passthrough_preserves_shape_and_attaches_voice(outbox, monkeypatch, _audit_spy):
    """A migrated delivery path's pre-built envelope is written verbatim (its
    markers/_final preserved, NOT rewritten with kind/_proactive_contact) with
    the caller's filename, and voice_path is attached by the primitive."""
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    pre = {"msg_id": "cn_xyz", "channel": CHANNEL, "chat_id": CHAT_ID,
           "text": TEXT, "_completion_notify": True, "_final": True}
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", solicited=True,
                           envelope=pre, voice_path="/tmp/n.ogg",
                           out_file_name="cn_xyz_ab12.json", outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    files = _outbox_files(outbox)
    assert len(files) == 1
    assert files[0].name == "cn_xyz_ab12.json"      # caller filename pinned
    env = json.loads(files[0].read_text())
    assert env["_completion_notify"] is True and env["_final"] is True
    assert env["voice_path"] == "/tmp/n.ogg"        # attached by the primitive
    assert "_proactive_contact" not in env          # passthrough, not rebuilt


# ── "No hand-written envelope" — migrated delivery paths route via emit_proactive ─

def test_no_hand_written_envelope_in_migrated_paths():
    """The migrated poller delivery functions must not write the outbox directly
    — every send goes through emit_proactive. mid_turn_heartbeat.deliver_due is
    DEFERRED (sticky `_progress` semantics) and deliberately NOT asserted here."""
    import inspect
    import completion_notify as cn
    import task_progress as tp
    for fn in (cn.deliver_ready, tp.deliver_progress):
        src = inspect.getsource(fn)
        assert "_emit_via_proactive(" in src, \
            f"{fn.__name__} must route the write through emit_proactive"
        # The old direct-outbox-write idiom must be gone from the migrated fn.
        assert "out_file = outbox" not in src, \
            f"{fn.__name__} still constructs a direct outbox path"
        assert "tmp.replace(out_file)" not in src, \
            f"{fn.__name__} still writes the outbox directly"
    # And the shared helper each uses actually calls emit_proactive.
    assert "emit_proactive(" in inspect.getsource(cn._emit_via_proactive)
    assert "emit_proactive(" in inspect.getsource(tp._emit_via_proactive)


# ── E2E: a /task completion (solicited) → emit_proactive → one envelope, flag OFF ─

def test_e2e_solicited_completion_flag_off_ship_dark_direct_delivery(outbox, monkeypatch):
    """SHIP-DARK (ADR-0553 amendment / MB1): drive the REAL completion delivery
    path (register → attach_voice → mark_done → deliver_ready) with the
    proactive_communication flag OFF and prove: exactly one correctly-routed
    envelope lands in the outbox carrying the pre-synthesized voice_path via the
    DIRECT write (byte-identical to before the Phase-2 migration) — the flag-OFF
    default is NOT routed through the proactive choke point, so NO proactive.*
    audit event is written. No regression: the completion arrives flag-OFF."""
    import completion_notify as cn
    from corvin_core import feature_flags as ff  # real flag module

    tenant = "acme"
    # The ship-dark flag is genuinely OFF for this tenant (no overlay written).
    assert ff.is_enabled(P.FLAG_ID, tenant) is False

    tid = cn.register(channel="discord", chat_id=CHAT_ID, sender="u1",
                      tenant_id=tenant, label="nightly backtest", want_voice=True)
    voice = "/tmp/summary.ogg"
    assert cn.attach_voice(tid, voice) is True
    assert cn.mark_done(tid, text="Sharpe 1.9 — report attached.", ok=True) is True

    # Real delivery — no synth callback (bg_monitor-style poller). Flag OFF.
    assert cn.deliver_ready(outbox) == 1
    files = _outbox_files(outbox)
    assert len(files) == 1
    env = json.loads(files[0].read_text())
    assert env["_completion_notify"] is True          # completion shape preserved
    assert env["chat_id"] == CHAT_ID                  # string, precision preserved
    assert env["voice_path"] == voice                 # single voice-attach site
    assert "Sharpe 1.9" in env["text"]

    # Ship-dark: the flag-OFF direct path bypasses the proactive gate entirely,
    # so no proactive.* audit event is written for this completion.
    audit = P._audit_path(tenant)
    if audit.is_file():
        assert "proactive.emitted" not in audit.read_text()

    # exactly-once: a second poll delivers nothing more (ADR-0445 preserved).
    assert cn.deliver_ready(outbox) == 0
    assert len(_outbox_files(outbox)) == 1


# ── H1: the ship-dark flag is tenant-scoped, not env-scoped ──────────────────

def test_flag_on_is_tenant_scoped_not_env(outbox, monkeypatch, _audit_spy):
    """H1: emit_proactive resolves the flag against the EXPLICIT emission tenant,
    NOT an ambient CORVIN_TENANT_ID. Flag ON for tenant 'acme', OFF for
    '_default': emit(tenant='acme') passes the flag gate; emit(tenant='_default')
    is denied flag-off — even though the env tenant is _default in both calls."""
    from corvin_core import feature_flags as ff
    ff._write_overlay("acme", {"flags": {P.FLAG_ID: True}})
    assert ff.is_enabled(P.FLAG_ID, "acme") is True
    assert ff.is_enabled(P.FLAG_ID, "_default") is False
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")   # ambient env is _default
    # Gates after the flag pass (isolate the flag gate):
    monkeypatch.setattr(P, "_consent_ok", lambda t, c, u: True)
    monkeypatch.setattr(P, "_house_rules_allows", lambda text, *, channel, chat_key: True)
    monkeypatch.setattr(P, "_disclosure_shown", lambda c, ck, u: True)

    res_t = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id="acme",
                             uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res_t is P.EmitResult.EMITTED               # tenant 'acme' sees ON
    res_d = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id="_default",
                             uid=UID, text=TEXT, kind="completion", outbox_dir=outbox)
    assert res_d is P.EmitResult.DENIED                # tenant '_default' sees OFF
    assert _audit_spy[-1][2]["reason"] == "flag-off"


# ── M3: rate check + record is atomic under one flock ────────────────────────

def test_rate_check_record_atomic_no_overshoot(outbox, _flag_on, _gates_pass, monkeypatch):
    """M3: load+check+record run under ONE flock, so concurrent emits never
    exceed MAX_PER_WINDOW. Each thread takes a separate flock on the sidecar,
    which serializes the whole read-modify-write — exactly MAX_PER_WINDOW win."""
    import threading
    monkeypatch.setattr(P, "MAX_PER_WINDOW", 5)
    results: list = []
    guard = threading.Lock()

    def _worker():
        r = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                             uid=UID, text=TEXT, kind="progress", outbox_dir=outbox)
        with guard:
            results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    emitted = [r for r in results if r is P.EmitResult.EMITTED]
    assert len(emitted) == 5, results                  # never overshoots the cap
    assert len(_outbox_files(outbox)) == 5
    store = P._load_rate_store(P._ratelimit_path(TENANT, CHANNEL))
    assert int(store[UID]["count"]) == 5               # durable counter is exact


# ── M4: a non-str dedup_key never flips EMITTED into ERROR ───────────────────

def test_int_dedup_key_emits_not_error(outbox, _flag_on, _gates_pass):
    """M4: a non-str dedup_key is str-coerced before sha256 in the POST-write
    audit, so a delivered EMITTED is never flipped into ERROR (→ resend)."""
    res = P.emit_proactive(channel=CHANNEL, chat_id=CHAT_ID, tenant_id=TENANT,
                           uid=UID, text=TEXT, kind="completion", dedup_key=12345,
                           outbox_dir=outbox)
    assert res is P.EmitResult.EMITTED
    assert len(_outbox_files(outbox)) == 1


# ── L5: long path components are hashed, never colliding on a 64-char prefix ──

def test_long_component_hashed_no_collision():
    """L5: two DISTINCT 64+-char ids map to DIFFERENT store paths (hashed, not
    truncated to a colliding 64-char prefix); short ids are unchanged."""
    a = "x" * 70 + "AAAA"
    b = "x" * 70 + "BBBB"
    assert P._safe_component(a) != P._safe_component(b)
    assert P._ratelimit_path(TENANT, a) != P._ratelimit_path(TENANT, b)
    assert P._safe_component("discord") == "discord"   # short id untouched


# ── MB1: flag ON + house-rules DENY → direct fallback (no silent loss) ───────

def test_mb1_flag_on_house_deny_falls_back_direct(outbox, monkeypatch):
    """MB1: with proactive_communication ON, a SOLICITED completion whose
    emit_proactive DENIES (house-rules fail-closed / false-positive) is STILL
    delivered via the direct fallback — no silent loss, no eternal READY."""
    import completion_notify as cn
    from corvin_core import feature_flags as ff
    tenant = "acme"
    ff._write_overlay(tenant, {"flags": {P.FLAG_ID: True}})
    assert ff.is_enabled(P.FLAG_ID, tenant) is True
    monkeypatch.setattr(P, "_house_rules_allows",
                        lambda text, *, channel, chat_key: False)  # gate DENIES
    tid = cn.register(channel="discord", chat_id=CHAT_ID, sender="u1",
                      tenant_id=tenant, label="job")
    assert cn.mark_done(tid, text="the result the user is waiting for", ok=True)
    assert cn.deliver_ready(outbox) == 1               # delivered anyway
    files = _outbox_files(outbox)
    assert len(files) == 1
    env = json.loads(files[0].read_text())
    assert env["_completion_notify"] is True
    assert "the result the user is waiting for" in env["text"]
    assert cn.deliver_ready(outbox) == 0               # exactly-once, not stuck READY


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
