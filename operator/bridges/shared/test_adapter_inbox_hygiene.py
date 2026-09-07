"""Inbox/processed hygiene (adversarial hardening 2026-09-07, F-B3 + F-B12).

Driven through the REAL boundary — an envelope FILE in the sandboxed inbox and
``adapter.process_one`` / the cleanup-tick sweepers — never a direct call to
the JSON parser:

  1. an unparsable envelope is QUARANTINED to ``processed/poison/`` (never
     unlinked) and the chain records ``bridge.inbox_poison_quarantined`` with
     metadata only
  2. ``_sweep_processed`` removes only regular files older than the retention
     window, keeps ``poison/``, is audited as a count, and honours the
     opt-out (``0``)
  3. ``_sweep_sysprompt_tmp`` removes only STALE ``.corvin-sysprompt-*.txt``
     leftovers under the sessions root
  4. R2-B4 (round 2): the dispatcher's pre-submit peeks (``_route_key`` /
     ``_peek_side_channel``) never raise on a non-object or non-UTF-8
     envelope — ``submit_inbox_item`` still routes it, the runner quarantines
     it, and the msg_id is NOT pinned in ``_in_flight`` for 3600 s

Run: python3 operator/bridges/shared/test_adapter_inbox_hygiene.py
     (or pytest)
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _fresh_adapter(env_overrides: dict[str, str]):
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.modules.pop("adapter", None)
    return importlib.import_module("adapter")


def _audit_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _sandbox():
    base = Path(tempfile.mkdtemp(prefix="adapter-hygiene-"))
    inbox = base / "inbox"; inbox.mkdir()
    outbox = base / "outbox"; outbox.mkdir()
    processed = base / "processed"; processed.mkdir()
    (base / "bridges").mkdir()
    audit_path = base / "audit.jsonl"
    settings = base / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    env = {
        "CORVIN_HOME": str(base / "home"),
        "ADAPTER_SETTINGS": str(settings),
        "ADAPTER_INBOX": str(inbox),
        "ADAPTER_OUTBOX": str(outbox),
        "ADAPTER_PROCESSED": str(processed),
        "ADAPTER_FAKE_CLAUDE": "1",
        "ADAPTER_ROUTING_MODE": "off",
        "VOICE_AUDIT_PATH": str(audit_path),
        "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
    }
    os.environ.pop("ADAPTER_PROCESSED_RETENTION_DAYS", None)
    return base, inbox, processed, audit_path, _fresh_adapter(env)


def test_poison_envelope_is_quarantined_not_unlinked() -> None:
    base, inbox, processed, audit_path, adapter = _sandbox()
    bad = inbox / "poison-1.json"
    bad.write_text('{"id": "poison-1", "text": "SECRET USER TEXT", "channel": "discord"', encoding="utf-8")
    adapter.process_one(bad, settings={})
    assert not bad.exists(), "poison envelope left in inbox"
    kept = processed / "poison" / "poison-1.json"
    assert kept.exists(), "poison envelope was not quarantined (unlinked?)"
    assert "SECRET USER TEXT" in kept.read_text(encoding="utf-8"), "quarantined bytes altered"
    events = _audit_lines(audit_path)
    q = [e for e in events if e.get("event_type") == "bridge.inbox_poison_quarantined"]
    assert len(q) == 1, f"expected one quarantine audit event, got {[e.get('event_type') for e in events]}"
    assert q[0]["details"].get("reason") == "json-decode-error"
    assert "SECRET USER TEXT" not in audit_path.read_text(encoding="utf-8"), "content leaked into audit"

    # A valid JSON that is not an object is poison too.
    bad2 = inbox / "poison-2.json"
    bad2.write_text('["not", "an", "object"]', encoding="utf-8")
    adapter.process_one(bad2, settings={})
    assert (processed / "poison" / "poison-2.json").exists()


def test_processed_sweep_is_age_bounded_and_keeps_poison() -> None:
    base, inbox, processed, audit_path, adapter = _sandbox()
    now = time.time()
    old = processed / "old.json"; old.write_text("{}"); os.utime(old, (now - 40 * 86400, now - 40 * 86400))
    old_att = processed / "old.ogg"; old_att.write_bytes(b"x"); os.utime(old_att, (now - 40 * 86400, now - 40 * 86400))
    fresh = processed / "fresh.json"; fresh.write_text("{}")
    poison = processed / "poison"; poison.mkdir()
    pfile = poison / "p.json"; pfile.write_text("{"); os.utime(pfile, (now - 400 * 86400, now - 400 * 86400))

    assert adapter._processed_retention_days() == 30.0, "default retention is 30 days"
    removed = adapter._sweep_processed(now=now)
    assert removed == 2, f"expected old.json + old.ogg swept, got {removed}"
    assert not old.exists() and not old_att.exists()
    assert fresh.exists(), "fresh envelope must survive"
    assert pfile.exists(), "poison/ must never be swept"
    sw = [e for e in _audit_lines(audit_path) if e.get("event_type") == "bridge.processed_swept"]
    assert len(sw) == 1 and sw[0]["details"].get("removed") == 2
    assert "old.json" not in json.dumps(sw[0]), "file names must not be audited"

    # Opt-out: retention 0 disables the sweep; settings.json value is honoured.
    os.environ["ADAPTER_PROCESSED_RETENTION_DAYS"] = "0"
    stale = processed / "stale.json"; stale.write_text("{}"); os.utime(stale, (now - 900 * 86400, now - 900 * 86400))
    assert adapter._sweep_processed(now=now) == 0 and stale.exists()
    os.environ.pop("ADAPTER_PROCESSED_RETENTION_DAYS", None)
    adapter.SETTINGS_FILE.write_text(json.dumps({"processed_retention_days": 7}), encoding="utf-8")
    adapter._settings_cache = None
    assert adapter._processed_retention_days() == 7.0
    week_old = processed / "week.json"; week_old.write_text("{}"); os.utime(week_old, (now - 8 * 86400, now - 8 * 86400))
    assert adapter._sweep_processed(now=now) >= 1 and not week_old.exists()


def test_sysprompt_sweep_removes_only_stale_leftovers() -> None:
    base, inbox, processed, audit_path, adapter = _sandbox()
    sess = adapter.SESSIONS_ROOT / "discord" / "chat-1"
    sess.mkdir(parents=True, exist_ok=True)
    now = time.time()
    stale = sess / ".corvin-sysprompt-abc123.txt"; stale.write_text("memory + vault hints")
    os.utime(stale, (now - 7200, now - 7200))
    live = sess / ".corvin-sysprompt-live.txt"; live.write_text("in use")
    other = sess / "notes.txt"; other.write_text("keep"); os.utime(other, (now - 7200, now - 7200))
    assert adapter._sweep_sysprompt_tmp(now=now) == 1
    assert not stale.exists()
    assert live.exists(), "a fresh temp file may belong to a live spawn"
    assert other.exists(), "only .corvin-sysprompt-*.txt is swept"


def main() -> int:
    fails = 0
    tests = (test_poison_envelope_is_quarantined_not_unlinked,
             test_processed_sweep_is_age_bounded_and_keeps_poison,
             test_sysprompt_sweep_removes_only_stale_leftovers,
             test_non_object_and_non_utf8_envelopes_do_not_abort_dispatch,
             test_good_envelope_after_poison_still_routes)
    for fn in tests:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL: {fn.__name__}: {e}")
    print(f"\n{len(tests) - fails} passed, {fails} failed")
    return 1 if fails else 0


def _wait_in_flight_drained(adapter, msg_id: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with adapter._in_flight_guard:
            if msg_id not in adapter._in_flight:
                return
        time.sleep(0.05)
    raise AssertionError(f"{msg_id} still pinned in _in_flight after {timeout}s")


def test_non_object_and_non_utf8_envelopes_do_not_abort_dispatch() -> None:
    """R2-B4: drive the REAL dispatcher (`submit_inbox_item` → `_route_key` /
    `_peek_side_channel` → pool runner → `process_one`) with the two envelope
    shapes the old peeks did not catch. Before the fix `AttributeError` /
    `UnicodeDecodeError` escaped `submit_inbox_item` before the future was
    attached: the poll tick died and the msg_id stayed in `_in_flight` for
    IN_FLIGHT_TTL (3600 s)."""
    from concurrent.futures import ThreadPoolExecutor

    base, inbox, processed, audit_path, adapter = _sandbox()
    adapter._executor = ThreadPoolExecutor(max_workers=2)
    adapter._sidechannel_executor = ThreadPoolExecutor(max_workers=1)
    try:
        cases = {
            "list-envelope": b'["not", "an", "object"]',
            "string-envelope": b'"just a string"',
            "number-envelope": b'42',
            "non-utf8-envelope": b'{"id": "x", "text": "\xff\xfe\xfa bad bytes"}',
        }
        for name, raw in cases.items():
            f = inbox / f"{name}.json"
            f.write_bytes(raw)
            # the peeks themselves must be total functions
            assert adapter._route_key(f) == f"unknown:{name}", name
            assert adapter._peek_side_channel(f) is False, name
            assert adapter._peek_envelope(f) is None, name
            adapter.submit_inbox_item(f, settings={})  # must not raise
            _wait_in_flight_drained(adapter, name)
            assert not f.exists(), f"{name} left in inbox"
            assert (processed / "poison" / f"{name}.json").exists(), f"{name} not quarantined"
        events = _audit_lines(audit_path)
        reasons = sorted(e["details"].get("reason") for e in events
                         if e.get("event_type") == "bridge.inbox_poison_quarantined")
        assert reasons == sorted(["not-an-object"] * 3 + ["unicode-decode-error"]), reasons
        assert "bad bytes" not in audit_path.read_text(encoding="utf-8", errors="replace")
    finally:
        adapter._executor.shutdown(wait=True)
        adapter._sidechannel_executor.shutdown(wait=True)
        adapter._executor = None
        adapter._sidechannel_executor = None


def test_good_envelope_after_poison_still_routes() -> None:
    """A valid envelope in the same tick is unaffected by a poison neighbour
    (the old failure aborted the WHOLE tick)."""
    base, inbox, processed, audit_path, adapter = _sandbox()
    good = inbox / "good-1.json"
    good.write_text(json.dumps({"id": "good-1", "from": "u1", "chat_id": "c1",
                                "channel": "discord", "text": "hi"}), encoding="utf-8")
    assert adapter._route_key(good) == "discord:c1"
    assert adapter._peek_side_channel(good) is False
    side = inbox / "btw-1.json"
    side.write_text(json.dumps({"id": "btw-1", "from": "u1", "chat_id": "c1",
                                "channel": "discord", "_btw": True, "text": "note"}), encoding="utf-8")
    assert adapter._peek_side_channel(side) is True


if __name__ == "__main__":
    sys.exit(main())
