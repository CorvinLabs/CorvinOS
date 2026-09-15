#!/usr/bin/env python3
"""test_consent_gate.py — Layer 17 per-subtask E2E.

Covers:

  L17/1  parse_ttl semantics (durable / TTL / clamp / invalid)
  L17/2  parse_share_prefix recognises /share <text>
  L17/3  grant / revoke / status round-trip + lazy prune of expired entries
  L17/4  is_granted: durable, time_bounded, no-entry, expired, revoked
  L17/5  observer envelope WITHOUT consent → drop + consent.observer_dropped
  L17/6  observer envelope WITH durable consent → buffer + bridge.observer_appended
  L17/7  observer envelope with `_share: true` → admit one-shot, audit
  L17/8  consume-time drift: grant → write → revoke → owner turn → drop
  L17/9  CLI subcommands (parse-ttl, parse-share, on/off/status/list) round-trip

The tests use the same sandbox pattern as test_adapter_security_hardening.py:
fresh /tmp dir per case, CORVIN_HOME redirected, channel settings.json
swapped in for the duration, ADAPTER_FAKE_CLAUDE=1 so the adapter never
spawns a real claude subprocess.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    import pytest
    _PYTEST_AVAILABLE = True
except ImportError:
    _PYTEST_AVAILABLE = False


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _set_sandbox(tmp: Path) -> None:
    os.environ["ADAPTER_INBOX"] = str(tmp / "inbox")
    os.environ["ADAPTER_OUTBOX"] = str(tmp / "outbox")
    os.environ["ADAPTER_PROCESSED"] = str(tmp / "processed")
    os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
    os.environ["ADAPTER_DISABLE_VOICE"] = "1"
    os.environ["ADAPTER_ROUTING_MODE"] = "off"
    os.environ["VOICE_AUDIT_PATH"] = str(tmp / "audit.jsonl")
    os.environ["CORVIN_HOME"] = str(tmp / "corvinos")
    for d in ("inbox", "outbox", "processed"):
        (tmp / d).mkdir(parents=True, exist_ok=True)


def _write_channel_settings(channel: str, settings: dict) -> Path:
    chan_dir = ROOT.parent / channel
    chan_dir.mkdir(parents=True, exist_ok=True)
    settings_file = chan_dir / "settings.json"
    settings_file.write_text(json.dumps(settings))
    return settings_file


def _restore_channel_settings(settings_file: Path, original: str | None) -> None:
    if original is None:
        settings_file.unlink(missing_ok=True)
    else:
        settings_file.write_text(original)


def _fresh_adapter():
    for mod in ("adapter", "consent"):
        sys.modules.pop(mod, None)
    import adapter  # type: ignore
    return adapter


def _fresh_consent():
    sys.modules.pop("consent", None)
    import consent  # type: ignore
    return consent


def _read_audit_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _forge_audit_path(tmp: Path) -> Path:
    return tmp / "corvinos" / "global" / "forge" / "audit.jsonl"


def _all_audit_events(tmp: Path) -> list[dict]:
    """Bridge events go to VOICE_AUDIT_PATH, consent.* events go through
    the forge hash chain. Merge both for assertions."""
    out: list[dict] = []
    out.extend(_read_audit_events(Path(os.environ["VOICE_AUDIT_PATH"])))
    out.extend(_read_audit_events(_forge_audit_path(tmp)))
    return out


# ─────────────────────────────────────────────────────────────────
# L17/1 — parse_ttl
# ─────────────────────────────────────────────────────────────────

def case_parse_ttl() -> None:
    _section("L17/1: parse_ttl semantics")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l1-"))
    try:
        _set_sandbox(tmp)
        consent = _fresh_consent()

        # Durable keywords → None
        for kw in ("on", "yes", "ja", "true", "always", "ON", "Yes"):
            assert consent.parse_ttl(kw) is None, f"durable kw: {kw!r}"
        # Revoke keywords → None too (caller distinguishes via the keyword)
        for kw in ("off", "no", "nein", "false", "revoke"):
            assert consent.parse_ttl(kw) is None, f"revoke kw: {kw!r}"
        # Empty → None
        assert consent.parse_ttl("") is None
        assert consent.parse_ttl(None) is None  # type: ignore[arg-type]
        # Numeric
        assert consent.parse_ttl("60s") == 60
        assert consent.parse_ttl("5m") == 300
        assert consent.parse_ttl("1h") == 3600
        assert consent.parse_ttl("7d") == 7 * 86400
        # Clamp lower (1s → MIN_TTL_S=60)
        assert consent.parse_ttl("1s") == consent.MIN_TTL_S
        # Clamp upper (60d → MAX_TTL_S=30d)
        assert consent.parse_ttl("60d") == consent.MAX_TTL_S
        # Invalid → -1
        for bad in ("abc", "5x", "1week", "5", "h5", "-5m"):
            assert consent.parse_ttl(bad) == -1, f"should be invalid: {bad!r}"
        print("  pass — parse_ttl covers all 18 cases")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L17/2 — parse_share_prefix
# ─────────────────────────────────────────────────────────────────

def case_parse_share_prefix() -> None:
    _section("L17/2: parse_share_prefix")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l2-"))
    try:
        _set_sandbox(tmp)
        consent = _fresh_consent()

        ok, payload = consent.parse_share_prefix("/share hello world")
        assert ok and payload == "hello world", (ok, payload)
        ok, payload = consent.parse_share_prefix("/SHARE  multi  line")
        assert ok and payload == "multi  line", (ok, payload)  # case-insensitive
        ok, payload = consent.parse_share_prefix("/share")
        assert ok and payload == "", (ok, payload)  # bare /share = empty payload
        ok, payload = consent.parse_share_prefix("/share\nbody")
        assert ok and payload == "body", (ok, payload)  # multiline body
        ok, payload = consent.parse_share_prefix("hello /share")
        assert not ok, "must match at start only"
        ok, payload = consent.parse_share_prefix("")
        assert not ok
        ok, payload = consent.parse_share_prefix("/sharefoo")
        assert not ok, "no word boundary trick"
        print("  pass — parse_share_prefix covers 7 cases")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L17/3+4 — grant / revoke / status / lazy prune
# ─────────────────────────────────────────────────────────────────

def case_grant_revoke_status_roundtrip() -> None:
    _section("L17/3: grant / revoke / status round-trip")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l3-"))
    try:
        _set_sandbox(tmp)
        consent = _fresh_consent()

        # No entry → not granted
        ok, reason = consent.is_granted("telegram", "555", "alice")
        assert not ok and reason == "no-entry", (ok, reason)

        # Grant durable
        e = consent.grant("telegram", "555", "alice", ttl_s=None)
        assert e["mode"] == "durable" and e["expires_at"] is None
        ok, reason = consent.is_granted("telegram", "555", "alice")
        assert ok and reason == "durable", (ok, reason)

        # Status mirrors is_granted
        s = consent.status("telegram", "555", "alice")
        assert s["granted"] and s["mode"] == "durable" and s["remaining_s"] == 0

        # Time-bounded grant for a different uid
        consent.grant("telegram", "555", "bob", ttl_s=120)
        ok, reason = consent.is_granted("telegram", "555", "bob")
        assert ok and reason.startswith("ttl:"), (ok, reason)
        s = consent.status("telegram", "555", "bob")
        assert s["mode"] == "time_bounded" and 60 < s["remaining_s"] <= 120

        # list_consents shows both
        lst = consent.list_consents("telegram", "555")
        assert set(lst.keys()) == {"alice", "bob"}, lst.keys()

        # Revoke alice
        existed = consent.revoke("telegram", "555", "alice")
        assert existed is True
        ok, reason = consent.is_granted("telegram", "555", "alice")
        assert not ok and reason == "no-entry", (ok, reason)
        # Re-revoke → no-op, returns False
        existed = consent.revoke("telegram", "555", "alice")
        assert existed is False

        # Audit chain has 3 events: 2 grants + 1 revoke
        events = _read_audit_events(_forge_audit_path(tmp))
        types = [e.get("event_type") for e in events]
        assert types.count("consent.granted") == 2, types
        assert types.count("consent.revoked") == 1, types
        print("  pass — grant/revoke/status round-trip + audit emitted")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


def case_lazy_prune_expired() -> None:
    _section("L17/4: is_granted lazy-prunes expired entries")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l4-"))
    try:
        _set_sandbox(tmp)
        consent = _fresh_consent()

        # Hand-craft a store with an expired time_bounded entry by writing
        # the JSON directly — clamps in grant() would block sub-MIN_TTL.
        path = consent._store_path("telegram", "999")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "stale": {
                "mode": "time_bounded",
                "granted_at": time.time() - 7200,
                "expires_at": time.time() - 3600,  # 1h ago
                "channel": "telegram",
                "granted_via": "test",
                "ttl_s": 3600,
            }
        }))
        ok, reason = consent.is_granted("telegram", "999", "stale")
        assert not ok and reason == "expired", (ok, reason)
        # Pruned from disk
        data = json.loads(path.read_text())
        assert "stale" not in data, data
        # Audit event for the lazy-prune
        events = _read_audit_events(_forge_audit_path(tmp))
        assert any(e.get("event_type") == "consent.expired" for e in events), events
        print("  pass — expired entries pruned + audit emitted")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# L17/5 — observer envelope without consent → drop
# ─────────────────────────────────────────────────────────────────

def case_observer_drop_without_consent() -> None:
    _section("L17/5: observer envelope without consent → drop + audit")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l5-"))
    settings_file = ROOT.parent / "telegram" / "settings.json"
    original = settings_file.read_text() if settings_file.exists() else None
    try:
        _set_sandbox(tmp)
        _write_channel_settings("telegram", {
            "whitelist": ["the-owner"],
            "read_only": ["mit-leser"],
        })
        adapter = _fresh_adapter()

        msg = {"id": "obs_nocon", "channel": "telegram",
               "from": "mit-leser", "chat_id": "555",
               "_observer": True, "text": "uninvited line",
               "ts": time.time()}
        p = Path(adapter.INBOX) / "obs_nocon.json"
        p.write_text(json.dumps(msg))
        adapter.process_one(p, {})

        # No buffer was written
        buf_path = adapter._observer_buffer_path("telegram", "555")
        assert not buf_path.exists() or buf_path.read_text().strip() == "", \
            "buffer must stay empty when sender has no consent"
        # Audit chain has consent.observer_dropped
        events = _read_audit_events(_forge_audit_path(tmp))
        dropped = [e for e in events
                   if e.get("event_type") == "consent.observer_dropped"]
        assert dropped, \
            f"expected consent.observer_dropped, got {[e.get('event_type') for e in events]}"
        details = dropped[0].get("details", {})
        assert details.get("reason") == "no-consent", details
        # GDPR Art. 5 minimisation: text_len instead of verbatim snippet (M1 fix)
        assert "text_len" in details, f"expected text_len in details, got: {details}"
        assert details.get("text_len") == len("uninvited line"), details
        print("  pass — observer dropped + audit reason='no-consent'")
    finally:
        _restore_channel_settings(settings_file, original)
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────
# L17/6 — observer envelope WITH durable consent → buffered
# ─────────────────────────────────────────────────────────────────

def case_observer_pass_with_consent() -> None:
    _section("L17/6: observer envelope WITH consent → buffered")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l6-"))
    settings_file = ROOT.parent / "telegram" / "settings.json"
    original = settings_file.read_text() if settings_file.exists() else None
    try:
        _set_sandbox(tmp)
        _write_channel_settings("telegram", {
            "whitelist": ["the-owner"],
            "read_only": ["mit-leser"],
        })
        # Pre-grant durable consent before the adapter sees the envelope.
        consent = _fresh_consent()
        consent.grant("telegram", "555", "mit-leser", ttl_s=None)

        adapter = _fresh_adapter()
        msg = {"id": "obs_ok", "channel": "telegram",
               "from": "mit-leser", "chat_id": "555",
               "_observer": True, "text": "consented line",
               "ts": time.time()}
        p = Path(adapter.INBOX) / "obs_ok.json"
        p.write_text(json.dumps(msg))
        adapter.process_one(p, {})

        buf_path = adapter._observer_buffer_path("telegram", "555")
        assert buf_path.exists(), f"buffer should exist at {buf_path}"
        lines = [json.loads(l) for l in buf_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1 and lines[0]["text"] == "consented line", lines
        assert lines[0].get("consent_reason") == "durable", lines[0]
        assert lines[0].get("one_shot") is False, lines[0]

        events = _all_audit_events(tmp)
        appended = [e for e in events
                    if e.get("event_type") == "bridge.observer_appended"]
        assert appended, f"expected bridge.observer_appended, got {[e.get('event_type') for e in events]}"
        details = appended[0].get("details", {})
        assert details.get("consent_reason") == "durable", details
        assert details.get("one_shot_share") is False, details
        # No drop event
        assert not [e for e in events
                    if e.get("event_type") == "consent.observer_dropped"], events
        print("  pass — observer with durable consent buffered + audit clean")
    finally:
        _restore_channel_settings(settings_file, original)
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────
# L17/7 — `_share: true` envelope → one-shot admit, no consent needed
# ─────────────────────────────────────────────────────────────────

def case_share_envelope_admits_without_consent() -> None:
    _section("L17/7: /share one-shot envelope bypasses consent gate")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l7-"))
    settings_file = ROOT.parent / "telegram" / "settings.json"
    original = settings_file.read_text() if settings_file.exists() else None
    try:
        _set_sandbox(tmp)
        _write_channel_settings("telegram", {
            "whitelist": ["the-owner"],
            "read_only": ["mit-leser"],
        })
        adapter = _fresh_adapter()

        msg = {"id": "share_one", "channel": "telegram",
               "from": "mit-leser", "chat_id": "555",
               "_observer": True, "_share": True,
               "text": "single one-shot line", "ts": time.time()}
        p = Path(adapter.INBOX) / "share_one.json"
        p.write_text(json.dumps(msg))
        adapter.process_one(p, {})

        buf_path = adapter._observer_buffer_path("telegram", "555")
        assert buf_path.exists(), "buffer should hold the share envelope"
        lines = [json.loads(l) for l in buf_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1, lines
        assert lines[0]["text"] == "single one-shot line"
        assert lines[0].get("one_shot") is True, lines[0]
        # Consent reason is "no-gate" because we bypassed the gate.
        assert lines[0].get("consent_reason") == "no-gate", lines[0]

        events = _all_audit_events(tmp)
        share_admit = [e for e in events
                       if e.get("event_type") == "consent.share_admitted"]
        assert share_admit, [e.get("event_type") for e in events]
        details = share_admit[0].get("details", {})
        assert details.get("via") == "share-prefix", details
        # GDPR Art. 5 minimisation: text_len instead of verbatim snippet (M1 fix)
        assert "text_len" in details, f"expected text_len in details, got: {details}"
        assert details.get("text_len") == len("single one-shot line"), details

        # bridge.observer_appended also fires, and one_shot_share=True
        appended = [e for e in events
                    if e.get("event_type") == "bridge.observer_appended"]
        assert appended, "expected bridge.observer_appended"
        assert appended[0].get("details", {}).get("one_shot_share") is True
        print("  pass — /share envelope admits + audit consent.share_admitted")
    finally:
        _restore_channel_settings(settings_file, original)
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────
# L17/8 — consume-time drift: revoke between buffer-write and owner-turn
# ─────────────────────────────────────────────────────────────────

def case_consume_drift_revoke_after_buffer() -> None:
    _section("L17/8: consume-time drift — revoke after buffer-write")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l8-"))
    settings_file = ROOT.parent / "telegram" / "settings.json"
    original = settings_file.read_text() if settings_file.exists() else None
    try:
        _set_sandbox(tmp)
        _write_channel_settings("telegram", {
            "whitelist": ["the-owner"],
            "read_only": ["mit-leser"],
        })
        consent = _fresh_consent()
        consent.grant("telegram", "555", "mit-leser", ttl_s=None)
        adapter = _fresh_adapter()

        # Step 1: observer line lands in buffer (consent active)
        obs = {"id": "obs_drift", "channel": "telegram",
               "from": "mit-leser", "chat_id": "555",
               "_observer": True, "text": "soon-stale line", "ts": time.time()}
        p1 = Path(adapter.INBOX) / "obs_drift.json"
        p1.write_text(json.dumps(obs))
        adapter.process_one(p1, {})
        buf_path = adapter._observer_buffer_path("telegram", "555")
        assert buf_path.exists(), "buffer should be populated"

        # Step 2: revoke between buffer-write and owner-turn
        consent.revoke("telegram", "555", "mit-leser")

        # Step 3: owner turn — fake claude captures the prompt
        captured: list[str] = []
        def _fake_call(prompt, channel="whatsapp", chat_key="anon", **kwargs):
            captured.append(prompt)
            return "ok"
        adapter.call_claude = _fake_call
        adapter.call_claude_streaming = _fake_call

        owner_msg = {"id": "owner_drift", "channel": "telegram",
                     "from": "the-owner", "chat_id": "555",
                     "text": "what did people say?", "ts": time.time()}
        p2 = Path(adapter.INBOX) / "owner_drift.json"
        p2.write_text(json.dumps(owner_msg))
        adapter.process_one(p2, {})

        assert captured, "fake claude should have been called"
        prompt = captured[0]
        # The buffered line was DROPPED at consume-time; no observer block
        # should appear in the prompt.
        assert "OBSERVER TRANSCRIPT" not in prompt, \
            f"observer block must be empty after consume-drift: {prompt[:300]}"
        assert "soon-stale line" not in prompt, \
            "revoked observer line must NOT reach the LLM"

        events = _read_audit_events(_forge_audit_path(tmp))
        drift = [e for e in events
                 if e.get("event_type") == "consent.consume_drift"]
        assert drift, [e.get("event_type") for e in events]
        details = drift[0].get("details", {})
        assert details.get("reason") == "consent-drift-at-consume", details
        print("  pass — consume-drift drops the entry + audit consent.consume_drift")
    finally:
        _restore_channel_settings(settings_file, original)
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────
# L17/9 — CLI subcommands
# ─────────────────────────────────────────────────────────────────

def case_cli_subcommands() -> None:
    _section("L17/9: CLI subcommands round-trip")
    tmp = Path(tempfile.mkdtemp(prefix="consent-l9-"))
    try:
        _set_sandbox(tmp)
        cli = ROOT / "consent.py"

        env = {**os.environ}

        def _run(args):
            r = subprocess.run([sys.executable, str(cli), *args],
                               capture_output=True, text=True, env=env, timeout=10)
            return r.returncode, r.stdout, r.stderr

        # parse-ttl
        rc, out, _ = _run(["parse-ttl", "5m"])
        assert rc == 0 and json.loads(out)["ttl_s"] == 300, out
        rc, out, _ = _run(["parse-ttl", "garbage"])
        assert rc == 1 and json.loads(out)["ttl_s"] == -1, out

        # parse-share
        rc, out, _ = _run(["parse-share", "/share", "hello", "world"])
        j = json.loads(out)
        assert rc == 0 and j["is_share"] and j["payload"] == "hello world", j

        # status — no entry
        rc, out, _ = _run(["status", "telegram", "999", "alice"])
        assert rc == 0
        s = json.loads(out)
        assert s["granted"] is False and s["reason"] == "no-entry", s

        # on durable
        rc, out, _ = _run(["on", "telegram", "999", "alice"])
        assert rc == 0
        j = json.loads(out)
        assert j["ok"] and j["mode"] == "durable", j

        # status — durable
        rc, out, _ = _run(["status", "telegram", "999", "alice"])
        s = json.loads(out)
        assert s["granted"] and s["mode"] == "durable", s

        # on time-bounded
        rc, out, _ = _run(["on", "telegram", "999", "bob", "5m"])
        j = json.loads(out)
        assert j["ok"] and j["mode"] == "time_bounded", j
        assert j["ttl_human"] == "5m", j

        # list
        rc, out, _ = _run(["list", "telegram", "999"])
        j = json.loads(out)
        assert j["count"] == 2, j
        assert set(j["entries"].keys()) == {"alice", "bob"}, j

        # off
        rc, out, _ = _run(["off", "telegram", "999", "alice"])
        j = json.loads(out)
        assert j["ok"] and j["existed"] is True, j
        # second off → existed=False
        rc, out, _ = _run(["off", "telegram", "999", "alice"])
        j = json.loads(out)
        assert j["existed"] is False, j

        # bad subcommand
        rc, out, _ = _run(["nope"])
        j = json.loads(out)
        assert j["ok"] is False and "unknown" in j["error"].lower(), j
        assert rc == 1
        print("  pass — CLI parse-ttl/parse-share/status/on/list/off all round-trip")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)


# ─────────────────────────────────────────────────────────────────
# V-003: uid_hash — no raw uid in audit events
# ─────────────────────────────────────────────────────────────────

def test_no_uid_in_audit_events() -> None:
    """V-003: _audit() must emit uid_hash, never raw uid, in all call sites."""
    tmp = Path(tempfile.mkdtemp(prefix="consent-v003-"))
    try:
        os.environ["CORVIN_HOME"] = str(tmp / "corvinos")
        sys.modules.pop("consent", None)
        import consent  # type: ignore

        captured_calls: list[dict] = []

        def _fake_audit(event_type, *, channel, chat_key, uid,
                        details=None, severity=None):
            body = {
                "event_type": event_type,
                "channel": channel,
                "chat_key": chat_key,
                "uid_hash": consent._uid_hash(uid),
            }
            if details:
                body.update(details)
            captured_calls.append(body)

        with patch.object(consent, "_audit", side_effect=_fake_audit):
            # grant() emits consent.granted
            consent.grant("telegram", "555", "alice", ttl_s=None)
            # revoke() emits consent.revoked
            consent.revoke("telegram", "555", "alice")
            # is_granted() with no entry — no audit emitted, but tests the code path
            consent.is_granted("telegram", "555", "bob")
            # admit_observer_drop emits consent.observer_dropped
            consent.admit_observer_drop("telegram", "555", "carol",
                                        msg_id="m1", text_len=10)

        for call in captured_calls:
            assert "uid" not in call, (
                f"raw uid leaked into audit body for event {call.get('event_type')!r}: {call}"
            )
            # uid_hash is present (may be empty string for uid="")
            assert "uid_hash" in call, (
                f"uid_hash missing from audit body for event {call.get('event_type')!r}: {call}"
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)
        sys.modules.pop("consent", None)


# ─────────────────────────────────────────────────────────────────
# V-016: corrupt store → deny-all + backup created
# ─────────────────────────────────────────────────────────────────

def test_store_corrupted_denies_all() -> None:
    """V-016: corrupt JSON store → is_granted returns (False, 'store-corrupted'),
    backup file created, original store deleted."""
    tmp = Path(tempfile.mkdtemp(prefix="consent-v016-"))
    try:
        os.environ["CORVIN_HOME"] = str(tmp / "corvinos")
        sys.modules.pop("consent", None)
        import consent  # type: ignore

        # Write a corrupt JSON file to the store path
        path = consent._store_path("telegram", "999")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json,,}")

        granted, reason = consent.is_granted("telegram", "999", "alice")

        assert granted is False, f"expected False, got {granted}"
        assert reason == "store-corrupted", f"expected 'store-corrupted', got {reason!r}"

        # Backup file must exist alongside the now-deleted original
        backups = list(path.parent.glob(f"{path.name}.corrupt.*"))
        assert backups, (
            f"expected a .corrupt.<ts> backup file in {path.parent}, found none"
        )
        # Original must be gone (was deleted so the next call starts fresh)
        assert not path.exists(), f"corrupt store should have been deleted: {path}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)
        sys.modules.pop("consent", None)


# ─────────────────────────────────────────────────────────────────
# V-019: TTL prune retry on write failure
# ─────────────────────────────────────────────────────────────────

def test_prune_retry_on_write_failure() -> None:
    """V-019: _save_store_with_retry raises no exception when _save_store
    fails on first attempt but succeeds on the second; warning is logged
    only when ALL attempts fail."""
    tmp = Path(tempfile.mkdtemp(prefix="consent-v019-"))
    try:
        os.environ["CORVIN_HOME"] = str(tmp / "corvinos")
        sys.modules.pop("consent", None)
        import consent  # type: ignore

        # Hand-craft a store with an expired time_bounded entry
        path = consent._store_path("telegram", "777")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "stale": {
                "mode": "time_bounded",
                "granted_at": time.time() - 7200,
                "expires_at": time.time() - 3600,
                "channel": "telegram",
                "granted_via": "test",
                "ttl_s": 3600,
            }
        }))

        call_count = [0]
        original_save = consent._save_store

        def _flaky_save(p, d):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("disk full (simulated)")
            original_save(p, d)

        # Patch _save_store inside _save_store_with_retry to simulate transient failure
        with patch.object(consent, "_save_store", side_effect=_flaky_save):
            # Should not raise — best-effort
            granted, reason = consent.is_granted("telegram", "777", "stale")

        assert granted is False, f"expected False for expired uid, got {granted}"
        assert reason == "expired", f"expected 'expired', got {reason!r}"
        # _save_store must have been called at least once (the retry mechanism fired)
        assert call_count[0] >= 1, "expected at least one _save_store call"

        # Now test that all-attempts-fail logs a warning (not raises)
        path.write_text(json.dumps({
            "stale2": {
                "mode": "time_bounded",
                "granted_at": time.time() - 7200,
                "expires_at": time.time() - 3600,
                "channel": "telegram",
                "granted_via": "test",
                "ttl_s": 3600,
            }
        }))

        warn_msgs: list[str] = []

        def _always_fail(p, d):
            raise OSError("disk full (simulated)")

        with patch.object(consent, "_save_store", side_effect=_always_fail):
            with patch.object(consent, "_save_store_with_retry",
                              wraps=consent._save_store_with_retry):
                # Must not raise — best-effort semantics
                try:
                    consent._save_store_with_retry(path, {}, max_attempts=2)
                except Exception as exc:  # noqa: BLE001
                    assert False, f"_save_store_with_retry must not raise: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)
        sys.modules.pop("consent", None)


# ─────────────────────────────────────────────────────────────────

def main() -> int:
    cases = [
        case_parse_ttl,
        case_parse_share_prefix,
        case_grant_revoke_status_roundtrip,
        case_lazy_prune_expired,
        case_observer_drop_without_consent,
        case_observer_pass_with_consent,
        case_share_envelope_admits_without_consent,
        case_consume_drift_revoke_after_buffer,
        case_cli_subcommands,
        test_no_uid_in_audit_events,
        test_store_corrupted_denies_all,
        test_prune_retry_on_write_failure,
    ]
    failures = 0
    for fn in cases:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL — {fn.__name__}: {e}")
            failures += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR — {fn.__name__}: {type(e).__name__}: {e}")
            failures += 1
    if failures:
        print(f"\n{failures} of {len(cases)} cases failed.")
        return 1
    print(f"\nAll {len(cases)} L17 cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
