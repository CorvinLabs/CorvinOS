#!/usr/bin/env python3
"""E2E: a bridge turn whose claude child is SIGTERMed is classified C1 by the shared classifier.

CONCEPT-0112 P3 (PLAN-0958). Drives the real boundary: an inbox envelope goes through
``adapter.process_one``, the real streaming dispatcher starts a real subprocess (a fake
``claude`` binary pointed at by CORVIN_CLAUDE_BIN), the test sends SIGTERM to that child, and the
verdict is read back from the tenant audit chain. No call to ``classify_failure`` from the test.

Known limits, stated rather than hidden:
* The L44 house-rules classifier is a test double (it would otherwise spawn ``claude -p``).
* ADAPTER_FAKE_CLAUDE is NOT set: that switch short-circuits the spawn and never produces the
  exit status under test.
* Only the streaming dispatcher path is driven; the legacy ``call_claude`` path is not.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAKE_CLAUDE = """#!/usr/bin/env bash
# Fake claude: answers --version, otherwise records its pid and waits to be signalled.
if [[ "$*" == *"--version"* ]]; then echo "claude 0.0.0-fake"; exit 0; fi
echo $$ > "$FAKE_CLAUDE_PIDFILE"
exec sleep 60
"""


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter(env: dict[str, str]):
    os.environ.update(env)
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


def _chain_records(sandbox: Path, event_type: str) -> list[dict]:
    """Records of one event type from the sandbox audit file(s). In a test sandbox the adapter
    defaults VOICE_AUDIT_PATH to INBOX.parent/audit.jsonl (adapter.py, _IS_TEST_SANDBOX), so the
    whole sandbox is searched, not only CORVIN_HOME."""
    out: list[dict] = []
    for chain in sandbox.rglob("audit.jsonl"):
        for line in chain.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event_type") == event_type:
                out.append(rec)
    return out


def test_sigtermed_bridge_child_is_classified_c1() -> None:
    _section("bridge: SIGTERM on the claude child of a process_one turn -> class C1 on the chain")
    base = Path(tempfile.mkdtemp(prefix="bridge-failure-class-"))
    saved = dict(os.environ)
    try:
        inbox, outbox, processed = base / "inbox", base / "outbox", base / "processed"
        for p in (inbox, outbox, processed):
            p.mkdir()
        corvin_home = base / "corvin_home"
        fake = base / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        pidfile = base / "fake.pid"

        adapter = _fresh_adapter({
            "ADAPTER_INBOX": str(inbox), "ADAPTER_OUTBOX": str(outbox),
            "ADAPTER_PROCESSED": str(processed),
            "CORVIN_HOME": str(corvin_home), "CORVIN_TENANT_ID": "_default",
            "CORVIN_CLAUDE_BIN": str(fake), "FAKE_CLAUDE_PIDFILE": str(pidfile),
            "CORVIN_USE_ENGINE_LAYER": "1",
            # The documented dual-env bypass of the chat-turn quota gate (test_adapter_engine_switch):
            # the license package is not on this suite's path, so the gate would refuse fail-closed.
            "CORVIN_AGENTS_SKIP_LIVE": "1", "CORVIN_INTEGRATION_TEST": "1",
        })
        os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
        adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")

        in_file = inbox / "msg-fc-1.json"
        in_file.write_text(json.dumps({
            "id": "msg-fc-1", "channel": "sandbox-fc", "from": "u123",
            "chat_id": "chat-fc", "text": "please do it", "ts": time.time(),
        }), encoding="utf-8")

        worker = threading.Thread(
            target=adapter.process_one, args=(in_file,),
            kwargs={"settings": {"whitelist": ["u123"]}}, daemon=True)
        worker.start()
        deadline = time.time() + 60
        while time.time() < deadline and not (pidfile.exists() and pidfile.read_text().strip()):
            time.sleep(0.05)
        assert pidfile.exists(), (
            "the fake claude child never started; outbox: "
            + repr([json.loads(p.read_text(encoding="utf-8")).get("text") for p in outbox.glob("*.json")]))
        os.kill(int(pidfile.read_text().strip()), signal.SIGTERM)
        worker.join(timeout=60)
        assert not worker.is_alive(), "process_one did not return after the child was signalled"

        recs = _chain_records(base, "turn.failure_classified")
        assert len(recs) == 1, f"expected one turn.failure_classified record, got {len(recs)}"
        details = recs[0].get("details") or {}
        assert details.get("failure_class") == "C1", f"unexpected class: {details!r}"
        assert details.get("source") == "bridge", f"unexpected source: {details!r}"
        assert details.get("exit_code") in (-15, 143), f"unexpected exit: {details!r}"
        print(f"PASS: SIGTERM on the bridge child -> {details}")
    finally:
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_sigtermed_bridge_child_is_classified_c1()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    print("All 1 tests passed")
