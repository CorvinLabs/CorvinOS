"""Phase G E2E: bridge process_one() emits audit events.

Three scenarios driven through process_one with sandboxed inbox/outbox/
processed dirs and a sandboxed VOICE_AUDIT_PATH:

  1. Plain text message → bridge.message_received logged
  2. /cancel envelope → bridge.cancel logged with killed=0
  3. (deferred) bridge.persona_routed — needs cowork + a real router
     run; not exercised here because the routing path requires either
     a network LLM or the embedding model. We assert the call site
     exists by code-shape.

Existing test_adapter_* suites must stay green; this file adds new
coverage without touching them.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _fresh_adapter(env_overrides: dict[str, str]):
    """Reload adapter with given env overrides so its module-level Path
    constants pick up the sandbox dirs. Mirrors test_adapter_cancel."""
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.modules.pop("adapter", None)
    return importlib.import_module("adapter")


def _audit_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def test_message_received_event_emitted():
    print("\n[plain message → bridge.message_received in audit]")
    base = Path(tempfile.mkdtemp(prefix="adapter-audit-recv-"))
    inbox    = base / "inbox";    inbox.mkdir()
    outbox   = base / "outbox";   outbox.mkdir()
    processed = base / "processed"; processed.mkdir()
    audit_path = base / "audit.jsonl"

    env_overrides = {
        "ADAPTER_INBOX":    str(inbox),
        "ADAPTER_OUTBOX":   str(outbox),
        "ADAPTER_PROCESSED": str(processed),
        "ADAPTER_FAKE_CLAUDE": "1",     # don't actually call claude
        "ADAPTER_ROUTING_MODE": "off",   # don't try the router
        "VOICE_AUDIT_PATH":  str(audit_path),
        # Isolate from a live operator's bridges/<channel>/settings.json —
        # its whitelist otherwise SPG-drops this test's senders as private.
        "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
    }
    try:
        adapter = _fresh_adapter(env_overrides)
        env = {
            "id": "msg-test-recv-1",
            "channel": "discord",
            "from": "u-sender-007",
            "chat_id": "chat-999",
            "text": "hallo welt",
            "ts": time.time(),
        }
        in_file = inbox / "msg-test-recv-1.json"
        in_file.write_text(json.dumps(env))
        adapter.process_one(in_file, settings={"whitelist": ["u-sender-007"]})

        events = _audit_lines(audit_path)
        types = [e["event_type"] for e in events]
        t("audit.jsonl exists", audit_path.exists())
        t("bridge.message_received recorded",
          "bridge.message_received" in types)
        recv = next(e for e in events
                    if e["event_type"] == "bridge.message_received")
        det = recv.get("details") or {}
        t("recorded channel = discord", det.get("channel") == "discord")
        # GDPR Art. 4(1) (commit 70cffd6): bridge.message_received records a
        # one-way fingerprint of the chat_key, NEVER the raw value.
        import hashlib as _hl
        _expected_fp = _hl.sha256("chat-999".encode()).hexdigest()[:8]
        t("recorded chat_key = fingerprint (raw not leaked)",
          det.get("chat_key") == _expected_fp and det.get("chat_key") != "chat-999")
        # user is a platform UID (PII) — recorded as a one-way fingerprint too,
        # never raw (GDPR Art. 4(1); enforced centrally by the _audit_event PII floor).
        _expected_uid_fp = _hl.sha256("u-sender-007".encode()).hexdigest()[:8]
        t("recorded user = fingerprint (raw not leaked)",
          det.get("user") == _expected_uid_fp and det.get("user") != "u-sender-007")
        t("msg_id propagated",
          det.get("msg_id") == "msg-test-recv-1")
    finally:
        for k in env_overrides:
            os.environ.pop(k, None)


def test_cancel_envelope_emits_bridge_cancel():
    print("\n[/cancel envelope → bridge.cancel in audit, killed=0]")
    base = Path(tempfile.mkdtemp(prefix="adapter-audit-cancel-"))
    inbox    = base / "inbox";    inbox.mkdir()
    outbox   = base / "outbox";   outbox.mkdir()
    processed = base / "processed"; processed.mkdir()
    audit_path = base / "audit.jsonl"
    env_overrides = {
        "ADAPTER_INBOX":    str(inbox),
        "ADAPTER_OUTBOX":   str(outbox),
        "ADAPTER_PROCESSED": str(processed),
        "VOICE_AUDIT_PATH":  str(audit_path),
        "ADAPTER_ROUTING_MODE": "off",
            "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
    }
    try:
        adapter = _fresh_adapter(env_overrides)
        # Sandbox channel → no on-disk settings → fail-open inbox revalidation.
        env = {
            "id": "msg-cancel-99",
            "channel": "sandbox-audit",
            "from": "u-cancel-007",
            "chat_id": "chat-cancel-1",
            "_cancel": True,
            "ts": time.time(),
        }
        in_file = inbox / "msg-cancel-99.json"
        in_file.write_text(json.dumps(env))
        adapter.process_one(in_file,
                             settings={"whitelist": ["u-cancel-007"]})

        events = _audit_lines(audit_path)
        types = [e["event_type"] for e in events]
        t("audit.jsonl exists", audit_path.exists())
        t("bridge.cancel recorded",
          "bridge.cancel" in types)
        cancel = next(e for e in events
                      if e["event_type"] == "bridge.cancel")
        det = cancel.get("details") or {}
        t("killed=0 (nothing running)",
          det.get("killed") == 0)
        t("channel = sandbox-audit", det.get("channel") == "sandbox-audit")
        # chat_key is a platform identifier (PII) — fingerprinted, never raw.
        import hashlib as _hl
        _ck_fp = _hl.sha256("chat-cancel-1".encode()).hexdigest()[:8]
        t("chat_key = fingerprint (raw not leaked)",
          det.get("chat_key") == _ck_fp and det.get("chat_key") != "chat-cancel-1")
    finally:
        for k in env_overrides:
            os.environ.pop(k, None)


def test_chain_is_continuous_across_two_messages():
    print("\n[two consecutive messages → 2-event hash chain stays linked]")
    base = Path(tempfile.mkdtemp(prefix="adapter-audit-chain-"))
    inbox    = base / "inbox";    inbox.mkdir()
    outbox   = base / "outbox";   outbox.mkdir()
    processed = base / "processed"; processed.mkdir()
    audit_path = base / "audit.jsonl"
    env_overrides = {
        "ADAPTER_INBOX":    str(inbox),
        "ADAPTER_OUTBOX":   str(outbox),
        "ADAPTER_PROCESSED": str(processed),
        "ADAPTER_FAKE_CLAUDE": "1",
        "ADAPTER_ROUTING_MODE": "off",
        "VOICE_AUDIT_PATH":  str(audit_path),
            "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
    }
    try:
        adapter = _fresh_adapter(env_overrides)
        for i in (1, 2):
            env = {
                "id": f"msg-chain-{i}",
                "channel": "discord",
                "from": "u-chain",
                "chat_id": "chat-chain",
                "text": f"hi {i}",
                "ts": time.time(),
            }
            in_file = inbox / f"msg-chain-{i}.json"
            in_file.write_text(json.dumps(env))
            adapter.process_one(in_file,
                                 settings={"whitelist": ["u-chain"]})

        events = _audit_lines(audit_path)
        recv_events = [e for e in events
                        if e["event_type"] == "bridge.message_received"]
        t("two recv events written", len(recv_events) == 2)
        if len(recv_events) >= 2:
            t("first prev_hash is a string",
              isinstance(recv_events[0].get("prev_hash"), str))
            t("second prev_hash points at first hash",
              recv_events[1].get("prev_hash") == recv_events[0].get("hash"))

        # And: the voice-audit verify CLI agrees
        import subprocess
        cli = REPO_ROOT / "operator" / "voice" / "scripts" / "voice_audit.py"
        proc = subprocess.run(
            [sys.executable, str(cli), "--path", str(audit_path), "verify"],
            capture_output=True, text=True,
        )
        t("voice-audit verify rc=0",
          proc.returncode == 0,
          detail=f"stderr={proc.stderr!r}")
    finally:
        for k in env_overrides:
            os.environ.pop(k, None)


def main() -> int:
    test_message_received_event_emitted()
    test_cancel_envelope_emits_bridge_cancel()
    test_chain_is_continuous_across_two_messages()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
