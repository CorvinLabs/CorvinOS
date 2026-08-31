#!/usr/bin/env python3
"""test_adapter_reset_prewarn.py — E2E for the `/reset` unpinned-artifact
pre-warn gate (Layer 33) inside adapter.process_one's `_reset` dispatch.

The `/reset` handler (adapter.py, "if msg.get('_reset'):" branch) parses
`reset_mode = str(msg.get("text") or "").strip().lower()` and computes
`is_ack = reset_mode.endswith(" ack") or reset_mode.endswith(" force")`.
When NOT ack and the chat has unpinned forge artifacts, it must:
  - write an outbox warning envelope listing the artifacts
  - emit a `bridge.reset_prewarned` audit event
  - NOT purge anything (session.reset audit absent, artifacts still there)

When ack (` ack` / ` force` suffix) — or when there are no unpinned
artifacts at all — it must proceed with the destructive purge:
  - emit a `session.reset` audit event
  - call forge.artifacts.purge_session, wiping the session artifact dir
  - write the normal "New session..." ack envelope

No existing test drives this branch through `adapter.process_one` with a
registered forge artifact (see the review notes in the task ticket) — the
underlying `session_reset.collect_unpinned_artifacts` has zero test
references anywhere in the repo prior to this file.

Run: python3 operator/bridges/shared/test_adapter_reset_prewarn.py
  or: pytest -q operator/bridges/shared/test_adapter_reset_prewarn.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]  # operator/bridges/shared -> repo root
FORGE_PKG = REPO / "operator" / "forge"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(FORGE_PKG))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _setup_sandbox():
    """Sandbox inbox/outbox/processed/CORVIN_HOME and reload adapter so it
    (and forge.artifacts / session_reset, imported lazily inside the /reset
    handler) all read from the same isolated tempdir."""
    base = Path(tempfile.mkdtemp(prefix="adapter-reset-prewarn-"))
    inbox = base / "inbox"
    outbox = base / "outbox"
    processed = base / "processed"
    corvin_home = base / "corvin_home"
    for p in (inbox, outbox, processed, corvin_home):
        p.mkdir(parents=True, exist_ok=True)
    os.environ["ADAPTER_INBOX"] = str(inbox)
    os.environ["ADAPTER_OUTBOX"] = str(outbox)
    os.environ["ADAPTER_PROCESSED"] = str(processed)
    os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
    os.environ["ADAPTER_DISABLE_VOICE"] = "1"
    os.environ["ADAPTER_ROUTING_MODE"] = "off"
    os.environ["VOICE_AUDIT_PATH"] = str(base / "audit.jsonl")
    os.environ["CORVIN_HOME"] = str(corvin_home)
    os.environ.pop("CORVIN_TENANT_ID", None)  # defaults to "_default"
    for mod_name in list(sys.modules):
        if mod_name in ("adapter", "session_reset") or mod_name.startswith("forge"):
            del sys.modules[mod_name]
    import adapter  # type: ignore  # noqa: E402
    adapter._house_rules_classifier = (
        lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    )
    return adapter, inbox, outbox, base


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


def _register_artifact(channel: str, chat_key: str, base: Path,
                        name: str = "report.txt") -> Path:
    """Register one unpinned forge artifact for this session, matching the
    exact session_key ("<channel>:<chat_key>") the /reset handler reads."""
    from forge import artifacts as art  # type: ignore

    src = base / f"src-{name}"
    src.write_text("some artifact content\n" * 10)
    session_key = f"{channel}:{chat_key}"
    root = art.session_artifacts_dir(session_key, tenant_id="_default")
    art.register(source_path=src, artifacts_root=root, name=name,
                  by_tool="test", run_id="prewarn-seed")
    return root


def _send_reset(adapter, inbox: Path, *, channel: str, chat_key: str,
                 msg_id: str, text: str = "") -> None:
    msg = {"id": msg_id, "channel": channel, "from": chat_key,
           "chat_id": chat_key, "_reset": True, "text": text,
           "ts": time.time()}
    msg_path = inbox / f"{msg_id}.json"
    msg_path.write_text(json.dumps(msg))
    adapter.process_one(msg_path, settings={"whitelist": [chat_key]})


# ── 1: unpinned artifacts + plain "/reset" (no ack) → warn, no purge ───────

def test_reset_without_ack_warns_and_does_not_purge() -> None:
    _section("1: unpinned artifact + /reset (no ack) → pre-warn, no purge")
    adapter, inbox, outbox, base = _setup_sandbox()
    try:
        channel = "sandbox-prewarn"
        chat_key = "prewarn-chat-1"
        art_root = _register_artifact(channel, chat_key, base)
        assert art_root.exists(), "artifact root should exist after registration"
        manifest_path = art_root / ".manifest.jsonl"
        manifest_before = manifest_path.read_text()
        assert "report.txt" in manifest_before

        _send_reset(adapter, inbox, channel=channel, chat_key=chat_key,
                    msg_id="reset-no-ack", text="/reset")

        # Artifact dir must still exist — no destructive purge happened.
        assert art_root.exists(), \
            "unpinned artifacts must survive a non-ack /reset"
        manifest_after = manifest_path.read_text()
        assert manifest_after == manifest_before, \
            "manifest must be untouched when the reset was pre-warned"

        # Outbox must carry a warning envelope naming the artifact.
        out_files = sorted(outbox.glob("reset-no-ack_*.json"))
        assert out_files, "expected a warning envelope in the outbox"
        warning = json.loads(out_files[0].read_text())
        assert "report.txt" in warning.get("text", ""), \
            f"warning text should list the artifact, got: {warning.get('text')!r}"
        assert "unpinned" in warning.get("text", "").lower()

        events = _read_audit_events(Path(os.environ["VOICE_AUDIT_PATH"]))
        prewarn = [e for e in events if e.get("event_type") == "bridge.reset_prewarned"]
        assert prewarn, \
            f"expected bridge.reset_prewarned audit event, got {[e.get('event_type') for e in events]}"
        assert prewarn[0].get("details", {}).get("unpinned_count") == 1, prewarn[0]

        destructive = [e for e in events if e.get("event_type") == "session.reset"]
        assert not destructive, \
            "session.reset must NOT be emitted on a pre-warned (non-ack) reset"
        print("  pass — warned, listed artifact, no purge, no session.reset audit")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── 2: unpinned artifacts + "/reset ack" → purge proceeds ──────────────────

def test_reset_with_ack_suffix_purges() -> None:
    _section("2: unpinned artifact + '/reset ack' → destructive purge proceeds")
    adapter, inbox, outbox, base = _setup_sandbox()
    try:
        channel = "sandbox-prewarn"
        chat_key = "prewarn-chat-2"
        art_root = _register_artifact(channel, chat_key, base)
        assert art_root.exists()

        _send_reset(adapter, inbox, channel=channel, chat_key=chat_key,
                    msg_id="reset-ack", text="/reset ack")

        # The purge removes the whole session artifact dir (rmtree in
        # forge.artifacts.purge_session).
        assert not art_root.exists(), \
            "artifact dir must be purged after an acked reset"

        events = _read_audit_events(Path(os.environ["VOICE_AUDIT_PATH"]))
        destructive = [e for e in events if e.get("event_type") == "session.reset"]
        assert destructive, \
            f"expected session.reset audit event, got {[e.get('event_type') for e in events]}"
        prewarn = [e for e in events if e.get("event_type") == "bridge.reset_prewarned"]
        assert not prewarn, "an acked reset must not emit the pre-warn event"

        out_files = sorted(outbox.glob("reset-ack_*.json"))
        assert out_files, "expected the normal reset ack envelope in the outbox"
        ack_envelope = json.loads(out_files[0].read_text())
        assert "New session" in ack_envelope.get("text", ""), ack_envelope
        print("  pass — purged, session.reset audited, normal ack envelope sent")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── 3: unpinned artifacts + "/reset force" → purge proceeds ────────────────

def test_reset_with_force_suffix_purges() -> None:
    _section("3: unpinned artifact + '/reset force' → destructive purge proceeds")
    adapter, inbox, outbox, base = _setup_sandbox()
    try:
        channel = "sandbox-prewarn"
        chat_key = "prewarn-chat-3"
        art_root = _register_artifact(channel, chat_key, base)
        assert art_root.exists()

        _send_reset(adapter, inbox, channel=channel, chat_key=chat_key,
                    msg_id="reset-force", text="please /reset force")

        assert not art_root.exists(), \
            "artifact dir must be purged after a force-suffixed reset"

        events = _read_audit_events(Path(os.environ["VOICE_AUDIT_PATH"]))
        destructive = [e for e in events if e.get("event_type") == "session.reset"]
        assert destructive, \
            f"expected session.reset audit event, got {[e.get('event_type') for e in events]}"
        prewarn = [e for e in events if e.get("event_type") == "bridge.reset_prewarned"]
        assert not prewarn, "a force-suffixed reset must not emit the pre-warn event"
        print("  pass — purged via ' force' suffix, session.reset audited")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── 4: no artifacts at all → plain /reset purges immediately (fast path) ───

def test_reset_without_artifacts_purges_immediately() -> None:
    _section("4: no unpinned artifacts → plain /reset purges without warning")
    adapter, inbox, outbox, base = _setup_sandbox()
    try:
        channel = "sandbox-prewarn"
        chat_key = "prewarn-chat-4"

        _send_reset(adapter, inbox, channel=channel, chat_key=chat_key,
                    msg_id="reset-empty", text="/reset")

        events = _read_audit_events(Path(os.environ["VOICE_AUDIT_PATH"]))
        destructive = [e for e in events if e.get("event_type") == "session.reset"]
        assert destructive, \
            "an empty-artifact-list reset must proceed straight to session.reset"
        prewarn = [e for e in events if e.get("event_type") == "bridge.reset_prewarned"]
        assert not prewarn, \
            "no pre-warn event should fire when there are no unpinned artifacts"

        out_files = sorted(outbox.glob("reset-empty_*.json"))
        assert out_files, "expected the normal reset ack envelope in the outbox"
        ack_envelope = json.loads(out_files[0].read_text())
        assert "New session" in ack_envelope.get("text", ""), ack_envelope
        print("  pass — empty-artifact fast path purges immediately, no false warn")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    test_reset_without_ack_warns_and_does_not_purge()
    test_reset_with_ack_suffix_purges()
    test_reset_with_force_suffix_purges()
    test_reset_without_artifacts_purges_immediately()
    print("\nAll /reset unpinned-artifact pre-warn tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
