#!/usr/bin/env python3
"""What a new operator does in their tenant knowledge base, step by step — run by
knowledge-graph-default.spec.ts against the KB a fresh install created.

    python3 kb_gate_walkthrough.py <kb-dir> create   # initiative, concept, decision, epic, task
    python3 kb_gate_walkthrough.py <kb-dir> close    # back-edge + shared paths (the G2/G3 hand edits)
    python3 kb_gate_walkthrough.py <kb-dir> gates    # {"gates": [...G findings...], "ready": bool}

Only the KB's own `scripts/kb.py` and git are used — the same tools the README in that
directory names. The two hand edits (`close`) are frontmatter edits an operator makes in an
editor: a concept's link back to its decision (G2) and a `paths:` entry shared by the
decision and its epic (G3); they are committed like any edit.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

KB = Path(sys.argv[1]).resolve()
ENV = {**os.environ, "KB_ACTOR": "human:new-operator"}


def kb(*args: str) -> dict:
    r = subprocess.run([sys.executable, str(KB / "scripts" / "kb.py"), "--repo", str(KB), *args],
                       capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise SystemExit(f"kb {args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}


def git(*args: str) -> None:
    subprocess.run(["git", "-C", str(KB), "-c", "user.name=new-operator", "-c", "user.email=op@example.org",
                    *args], check=True, capture_output=True)


def add_frontmatter(path: Path, key: str, block: str) -> None:
    """Append items to a frontmatter list key (`key: []` or an existing block list)."""
    text = path.read_text(encoding="utf-8")
    head, sep, body = text[3:].partition("\n---")
    if re.search(rf"^{key}:\s*\[\]\s*$", head, re.M):
        head = re.sub(rf"^{key}:\s*\[\]\s*$", f"{key}:\n{block}", head, count=1, flags=re.M)
    elif re.search(rf"^{key}:\s*$", head, re.M):
        head = re.sub(rf"^({key}:\s*\n)", rf"\g<1>{block}\n", head, count=1, flags=re.M)
    else:
        head = head.rstrip("\n") + f"\n{key}:\n{block}\n"
    path.write_text("---" + head + sep + body, encoding="utf-8")


def one(pattern: str) -> Path:
    hits = sorted(KB.glob(pattern))
    assert len(hits) == 1, (pattern, hits)
    return hits[0]


def main() -> int:
    step = sys.argv[2]
    if step == "create":
        out = [kb("new", "initiative", "--title", "Onboarding"),
               kb("new", "concept", "--title", "Sign-in flow"),
               kb("new", "decision", "--title", "Sign in with OIDC", "--link", "formalized_as=CONCEPT-0001"),
               kb("new", "epic", "--title", "Build OIDC sign-in", "--initiative", "I-01",
                  "--link", "implements=ADR-0001"),
               kb("new", "task", "--title", "Callback route", "--epic", "E-001", "--dod", "route answers 200")]
        print(json.dumps({"created": [o.get("id") for o in out]}))
    elif step == "close":
        add_frontmatter(one("concepts/CONCEPT-0001-*.md"), "links", "- rel: formalized_as\n  to: ADR-0001")
        add_frontmatter(one("decisions/ADR-0001-*.md"), "paths", "- src/auth/oidc.py")
        add_frontmatter(one("kb/epics/E-001-*.md"), "paths", "- src/auth/oidc.py")
        git("add", "-A")
        git("commit", "-q", "-m", "link concept back to ADR-0001; plan paths")
        print(json.dumps({"closed": ["G2", "G3"]}))
    elif step == "gates":
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / "check.json"
            subprocess.run([sys.executable, str(KB / "scripts" / "kb.py"), "--repo", str(KB), "check",
                            "--json", str(report)], capture_output=True, env=ENV)
            res = json.loads(report.read_text())
        gates = sorted(k for k in res["issues"] if re.match(r"^G[1-4]_", k))
        ready = kb("show", "ADR-0001").get("implementation_ready")
        print(json.dumps({"gates": gates, "ready": ready, "blocking": res["blocking"]}))
    else:
        raise SystemExit(f"unknown step {step!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
