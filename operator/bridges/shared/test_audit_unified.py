"""Drift fix E2E: voice-bridge events and forge events land in ONE
hash-chained file, exactly as voice/SKILL.md claims.

Three scenarios:

  1. With FORGE_ROOT and VOICE_AUDIT_PATH unset, both audit.audit_path()
     (voice side) and forge's <workspace>/audit.jsonl (forge side)
     resolve to the same file. The user reading voice/SKILL.md sees
     the single-file claim and the implementation matches it.

  2. A bridge event followed by a forge event produces two records in
     ONE file with prev_hash linking them — i.e. a unified chain.

  3. Concurrent writes from two processes do not interleave or
     corrupt records (filesystem flock guards write_event).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

import audit as _voice_audit
from forge import security_events as _se


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def test_voice_default_audit_path_matches_forge_workspace():
    """The single-file claim in voice/SKILL.md only holds if the voice
    side defaults to the forge workspace's audit.jsonl. Otherwise
    voice-audit verify only sees half the chain.

    J.1.4a: the unified default moved from
    ``~/.config/corvin-voice/forge/audit.jsonl`` to
    ``corvin_home()/global/forge/audit.jsonl`` — scope-independent
    so a single chain still covers all bridges + forge scopes.
    """
    print("\n[default audit_path() == forge workspace's audit.jsonl]")
    # ensure no env override
    saved = {}
    for k in ("VOICE_AUDIT_PATH", "FORGE_ROOT"):
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    try:
        import importlib
        importlib.reload(_voice_audit)
        # Resolve the expected default the same way audit.py does so
        # the test stays correct under CORVIN_HOME overrides too.
        try:
            from paths import corvin_home  # type: ignore
        except ImportError:
            sys.path.insert(0, str(REPO_ROOT / "operator/bridges/shared"))
            from paths import corvin_home  # type: ignore
        forge_default = corvin_home() / "global" / "forge" / "audit.jsonl"
        t("voice default audit_path is the forge workspace audit.jsonl",
          _voice_audit.audit_path() == forge_default,
          detail=f"got {_voice_audit.audit_path()}")
    finally:
        for k, v in saved.items():
            os.environ[k] = v
        importlib.reload(_voice_audit)


def test_bridge_then_forge_share_one_chain():
    """A bridge event followed by a forge event in the same file:
    the second record's prev_hash must be the first record's hash."""
    print("\n[bridge event + forge event = one continuous chain]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        unified = td / "audit.jsonl"
        os.environ["VOICE_AUDIT_PATH"] = str(unified)
        try:
            import importlib
            importlib.reload(_voice_audit)
            # 1) bridge writes via the voice wrapper
            _voice_audit.audit_event(
                "bridge.message_received",
                channel="discord", chat_key="cid:1",
                user="u-x", details={"msg_id": "m1"},
            )
            # 2) forge writes via security_events.write_event directly
            _se.write_event(
                unified, "tool.created", tool="csv.count",
                details={"sha": "abc1234567890abc"}, hash_chain=True,
            )

            lines = unified.read_text().splitlines()
            t("two records written", len(lines) == 2)
            r1 = json.loads(lines[0])
            r2 = json.loads(lines[1])
            t("record 1 is bridge.message_received",
              r1["event_type"] == "bridge.message_received")
            t("record 2 is tool.created",
              r2["event_type"] == "tool.created")
            t("record 1 prev_hash empty (chain start)",
              r1.get("prev_hash") == "")
            t("record 2 prev_hash == record 1 hash",
              r2.get("prev_hash") == r1.get("hash"))
            ok, problems = _voice_audit.verify_audit(unified)
            t("verify_audit ok over the unified chain",
              ok and not problems)
        finally:
            os.environ.pop("VOICE_AUDIT_PATH", None)


def test_concurrent_processes_do_not_corrupt_chain():
    """Two child processes write 50 events each into the same audit
    file. The chain must verify clean afterwards (no interleaving,
    no torn writes)."""
    print("\n[two concurrent processes, 100 events total → chain stays valid]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        path = td / "audit.jsonl"
        # each child writes 50 events
        child = (
            'import os, sys, time\n'
            'sys.path.insert(0, ' + repr(str(REPO_ROOT / "operator/forge")) + ')\n'
            'from forge import security_events as _se\n'
            'from pathlib import Path\n'
            'p = Path(sys.argv[1])\n'
            'tag = sys.argv[2]\n'
            'for i in range(50):\n'
            '    _se.write_event(p, "tool.created", tool=f"{tag}.t{i}",\n'
            '                    details={"i": i}, hash_chain=True)\n'
        )
        procs = []
        for tag in ("alpha", "beta"):
            procs.append(subprocess.Popen(
                [sys.executable, "-c", child, str(path), tag],
            ))
        for p in procs:
            p.wait()
            t(f"child rc=0 (tag exit)", p.returncode == 0)

        ok, problems = _voice_audit.verify_audit(path)
        lines = path.read_text().splitlines()
        t(f"got 100 records on disk",
          len(lines) == 100,
          detail=f"got {len(lines)}")
        t("chain verifies under concurrency",
          ok, detail=f"problems={len(problems)}")


def main() -> int:
    test_voice_default_audit_path_matches_forge_workspace()
    test_bridge_then_forge_share_one_chain()
    test_concurrent_processes_do_not_corrupt_chain()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


# ── pytest-compatible unit tests ──────────────────────────────────────────────

import unittest  # noqa: E402


class TestConsentAuditPiiGuard(unittest.TestCase):
    """V-003 / ADR-0072: consent.* audit events must use uid_hash, never uid.

    The consent module pseudonymises the platform uid before writing to the
    hash chain (``uid_hash = SHA-256[:8]``). A raw ``uid`` field in any
    ``consent.*`` event would constitute a GDPR Art. 5 violation.
    """

    def test_no_uid_in_consent_audit(self) -> None:
        """Granting consent emits ``consent.granted`` with ``uid_hash`` but
        without a plain ``uid`` field anywhere in the event."""
        import importlib
        import json
        import os
        import shutil
        import tempfile

        sb = tempfile.mkdtemp(prefix="consent-audit-uid-")
        saved_home = os.environ.get("CORVIN_HOME")
        saved_voice = os.environ.get("VOICE_AUDIT_PATH")
        try:
            os.environ["CORVIN_HOME"] = sb
            # Point the voice-side audit path at our sandbox too so the
            # importlib reload picks up the correct default.
            unified = (
                Path(sb) / "global" / "forge" / "audit.jsonl"
            )
            os.environ["VOICE_AUDIT_PATH"] = str(unified)
            importlib.reload(_voice_audit)

            # Import consent freshly so it resolves CORVIN_HOME from env.
            for mod in list(sys.modules.keys()):
                if "consent" in mod:
                    sys.modules.pop(mod, None)
            import consent as _consent  # type: ignore

            _consent.grant(
                "discord", "chat:test", "user-123", via="slash"
            )

            self.assertTrue(
                unified.exists(),
                f"audit.jsonl not created at {unified}",
            )
            events = [
                json.loads(line)
                for line in unified.read_text().splitlines()
                if line.strip()
            ]
            consent_events = [
                e for e in events
                if str(e.get("event_type", "")).startswith("consent.")
            ]
            self.assertGreater(
                len(consent_events), 0,
                "at least one consent.* event must have been written",
            )
            for ev in consent_events:
                # Flatten the full event dict (top-level + nested details).
                flat: dict = dict(ev)
                details = flat.pop("details", {}) or {}
                all_keys = set(flat.keys()) | set(details.keys())
                self.assertNotIn(
                    "uid",
                    all_keys,
                    f"raw 'uid' found in {ev.get('event_type')} event; "
                    "only uid_hash is permitted",
                )
                # uid_hash must be present (pseudonymisation must happen).
                uid_hash_in_event = (
                    "uid_hash" in flat or "uid_hash" in details
                )
                self.assertTrue(
                    uid_hash_in_event,
                    f"uid_hash missing from {ev.get('event_type')} event",
                )
        finally:
            # Restore environment and module state.
            if saved_home is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = saved_home
            if saved_voice is None:
                os.environ.pop("VOICE_AUDIT_PATH", None)
            else:
                os.environ["VOICE_AUDIT_PATH"] = saved_voice
            importlib.reload(_voice_audit)
            shutil.rmtree(sb, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
