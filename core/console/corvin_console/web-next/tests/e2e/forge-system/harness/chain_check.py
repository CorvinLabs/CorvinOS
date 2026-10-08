"""Verify an isolated install's audit chain with the production verifier.

    chain_check.py HOME [SINCE_LINE]
        -> {"ok": bool, "problems": [...], "lines": N,
            "events": [{"line", "event_type", "details"}...]  (from SINCE_LINE)}

``forge.security_events.verify_chain`` is the same walk the ADR-0232 boot
tripwire performs; the anchor key and CORVIN_HOME are pointed at the
throwaway install so the live chain is never read.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

home = Path(sys.argv[1]).resolve()
since = int(sys.argv[2]) if len(sys.argv) > 2 else 0
os.environ["CORVIN_HOME"] = str(home)
os.environ["CORVIN_TENANT_ID"] = "_default"
os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(home / "audit-anchor.key")
os.environ["XDG_CONFIG_HOME"] = str(home / "xdg")
repo = Path(__file__).resolve().parents[8]
sys.path[:0] = [str(repo / "corvin_operator/forge"), str(repo)]

from forge.security_events import verify_chain  # noqa: E402

chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
ok, problems = verify_chain(chain)
events = []
lines = chain.read_text().splitlines() if chain.exists() else []
for n, line in enumerate(lines[since:], start=since):
    try:
        rec = json.loads(line)
    except ValueError:
        continue
    events.append({"line": n, "event_type": rec.get("event_type"), "details": rec.get("details", {})})
print(json.dumps({"ok": ok, "problems": problems[:20], "lines": len(lines), "events": events}))
