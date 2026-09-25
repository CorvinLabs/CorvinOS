"""Consent + disclosure gates on the SHARED tenant audit chain (2026-09-25).

Their `_audit_path()` now resolves the canonical tenant chain (it used to fall
back to the legacy <home>/global/forge/audit.jsonl, the writer behind the boot
tripwire's `audit_chain_split`). That chain is written by every subsystem, so
a CLAG gate keyed on a STATIC layer id ("nobody else wrote since my last
gate") failed after the first foreign write and locked every user out of
consent and the disclosure card. The gates now use a per-call layer id.
Each test runs in a subprocess with its own CORVIN_HOME (module-level state).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORGE = HERE.parents[1] / "forge"

_PRELUDE = r"""
import os, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
os.environ.pop("CORVIN_TENANT_ID", None)
from forge import security_events as se
canon = Path(os.environ["CORVIN_HOME"]) / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
canon.parent.mkdir(parents=True, exist_ok=True)
def foreign_write():
    se.write_event(canon, "A2A.envelope_received", severity="INFO", tool="", run_id="",
                   details={"task_id": "t"}, hash_chain=True)
"""


def _run(body: str) -> str:
    home = tempfile.mkdtemp(prefix="corvin-shared-chain-")
    env = {**os.environ, "CORVIN_HOME": home,
           "VOICE_AUDIT_PATH": str(Path(home) / "tenants" / "_default" / "global" / "forge" / "audit.jsonl")}
    out = subprocess.run([sys.executable, "-c", _PRELUDE + body, str(HERE), str(FORGE)],
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


class TestSharedChainGates(unittest.TestCase):
    def test_consent_survives_foreign_writes(self):
        out = _run(r"""
import consent
consent.grant("discord", "chat1", "u1")
print(consent.is_granted("discord", "chat1", "u1"))
foreign_write()
print(consent.is_granted("discord", "chat1", "u1"))
foreign_write()
print(consent.is_granted("discord", "chat1", "u1"))
print(consent._audit_path() == canon)
""")
        lines = out.strip().splitlines()
        self.assertEqual(lines[:3], ["(True, 'durable')"] * 3, out)
        self.assertEqual(lines[3], "True", "consent must write the canonical tenant chain")

    def test_disclosure_card_still_shown_after_foreign_writes(self):
        out = _run(r"""
import disclosure
disclosure.mark_seen("discord", "c1", "userA")
foreign_write()
print(disclosure.mark_seen("discord", "c1", "userB").get("action"))
""")
        self.assertEqual(out.strip(), "pending", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTruncationStillDetected(unittest.TestCase):
    """Round 3: per-call layer ids lost tail-truncation detection; the
    ancestor anchor restores it without breaking on foreign appends."""

    def test_truncating_the_chain_fails_the_consent_gate(self):
        out = _run(r"""
import consent
consent.grant("discord", "chat1", "u1")
print(consent.is_granted("discord", "chat1", "u1"))
foreign_write(); foreign_write()
print(consent.is_granted("discord", "chat1", "u1"))   # appends are fine
lines = canon.read_text().splitlines()
canon.write_text("\n".join(lines[:-4]) + "\n")      # drop the newest 4 records
print(consent.is_granted("discord", "chat1", "u1"))
""")
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "(True, 'durable')", out)
        self.assertEqual(lines[1], "(True, 'durable')", out)
        self.assertEqual(lines[2], "(False, 'chain-integrity-failed')", out)
