"""ST9.6 E2E: structured audit events + sha256 hash-chain integrity.

Fictional scenario: an attacker with write access to ``audit.jsonl``
tries to scrub their tracks (delete a row, edit a field, append a
fake row). The chain catches all three.

We also validate:
  - ``forge audit-verify`` CLI exits 0 / 1 in the obvious cases
  - registry events (tool.created/deleted/promoted) are part of the chain
  - hash_chain=False mode writes events without chain fields, and
    ``verify_chain`` accepts them silently (back-compat)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forge.registry import Registry
from forge.security_events import write_event, verify_chain
from test_mcp import MCPClient


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- direct unit tests on write_event / verify_chain --------------

def test_writes_chain_with_prev_and_hash():
    print("\n[write_event: prev_hash + hash on every record]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "audit.jsonl"
        r1 = write_event(p, "tool.created", tool="x")
        r2 = write_event(p, "tool.created", tool="y")
        r3 = write_event(p, "tool.promoted", tool="x")
        t("first record's prev_hash is ''",
          r1.get("prev_hash") == "")
        t("second record's prev_hash = first.hash",
          r2.get("prev_hash") == r1.get("hash"))
        t("third record's prev_hash = second.hash",
          r3.get("prev_hash") == r2.get("hash"))
        ok, problems = verify_chain(p)
        t("verify_chain ok on clean file", ok and not problems)


def test_verify_detects_field_tamper():
    print("\n[verify: editing a field is caught]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "audit.jsonl"
        for ev in ("tool.created", "tool.created", "tool.deleted"):
            write_event(p, ev, tool="x")
        # Tamper line 2: change tool from "x" to "y" in the body but leave
        # the hash untouched
        lines = p.read_text().splitlines()
        rec = json.loads(lines[1])
        rec["tool"] = "y"  # dishonest edit
        lines[1] = json.dumps(rec)
        p.write_text("\n".join(lines) + "\n")
        ok, problems = verify_chain(p)
        t("verify reports NOT ok", ok is False)
        t("at least one problem", len(problems) >= 1)
        t("issue includes 'tampered' on tampered line",
          any(pr["issue"] == "tampered" and pr["line"] == 2
              for pr in problems))
        # Note: when the attacker leaves the *hash* field unchanged, the
        # downstream record's prev_hash still matches and the chain
        # *appears* intact past the edit. That's fine — verify localizes
        # the corruption to its origin, which is what we want.


def test_verify_detects_deleted_record():
    print("\n[verify: deleting a record breaks the chain]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "audit.jsonl"
        for ev in ("tool.created", "tool.created", "tool.created", "tool.deleted"):
            write_event(p, ev, tool="x")
        lines = p.read_text().splitlines()
        # Drop record 2 — record 3's prev_hash now points at the hash of
        # the OLD record 2, which is no longer present.
        del lines[1]
        p.write_text("\n".join(lines) + "\n")
        ok, problems = verify_chain(p)
        t("verify NOT ok after delete", ok is False)
        t("broken_chain on (now-)line 2",
          any(pr["issue"] == "broken_chain" and pr["line"] == 2
              for pr in problems))


def test_verify_detects_appended_fake():
    print("\n[verify: a hand-crafted fake append is caught]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "audit.jsonl"
        write_event(p, "tool.created", tool="x")
        # Append a record that pretends to be chained but has no hash
        with p.open("a") as fh:
            fh.write(json.dumps({
                "ts": time.time(), "event_type": "tool.created",
                "severity": "INFO", "tool": "ghost",
                "details": {}, "prev_hash": "deadbeef00000000",
                "hash": "feedface11111111",
            }) + "\n")
        ok, problems = verify_chain(p)
        t("verify NOT ok after fake append", ok is False)
        # both chain and tamper checks flag it (prev_hash mismatch + hash
        # mismatch since the body was made up)
        t("at least one violation flagged", len(problems) >= 1)


def test_invalid_json_line_does_not_crash_verifier():
    print("\n[verify: malformed line surfaces as invalid_json]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "audit.jsonl"
        write_event(p, "tool.created", tool="x")
        with p.open("a") as fh:
            fh.write("this is not json\n")
        write_event(p, "tool.deleted", tool="x")
        ok, problems = verify_chain(p)
        t("verify NOT ok", ok is False)
        t("invalid_json on line 2",
          any(pr["issue"] == "invalid_json" and pr["line"] == 2
              for pr in problems))


def test_non_dict_json_line_does_not_crash_verifier():
    print("\n[verify: JSON-valid-but-non-dict line surfaces as invalid_json, doesn't crash]")
    # A line can be syntactically valid JSON (json.loads succeeds, no
    # JSONDecodeError) while not being an object at all -- a bare number,
    # null, bool, string, or list. verify_chain used to do
    # `if "hash" not in rec:` on the parsed value with no isinstance guard,
    # which raises an uncaught TypeError for int/None/bool (and silently
    # mis-skips str/list) instead of returning a controlled (False, problems).
    for bad_line in ("42", "null", "true", '"just a string"', "[1, 2, 3]"):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            write_event(p, "tool.created", tool="x")
            with p.open("a") as fh:
                fh.write(bad_line + "\n")
            # A hand-crafted tampered record AFTER the bad line -- this must
            # still be reached and reported, not swallowed by an aborted scan.
            with p.open("a") as fh:
                fh.write(json.dumps({
                    "ts": time.time(), "event_type": "tool.created",
                    "severity": "INFO", "tool": "ghost",
                    "details": {}, "prev_hash": "deadbeef00000000",
                    "hash": "feedface11111111",
                }) + "\n")

            try:
                ok, problems = verify_chain(p)
            except Exception as exc:  # noqa: BLE001 - this is exactly what we assert against
                t(f"verify_chain does not raise on bad line {bad_line!r}",
                  False, detail=f"raised {type(exc).__name__}: {exc}")
                continue

            t(f"verify_chain returns cleanly for bad line {bad_line!r}", True)
            t(f"verify NOT ok for bad line {bad_line!r}", ok is False)
            t(f"invalid_json (or equivalent) flagged on line 2 for {bad_line!r}",
              any(pr["line"] == 2 for pr in problems))
            t(f"tampering on line 3 AFTER {bad_line!r} is still detected",
              any(pr["line"] == 3 for pr in problems))


def test_hash_chain_disabled_records_dont_break_verify():
    print("\n[hash_chain=False: events without hash are skipped silently]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "audit.jsonl"
        write_event(p, "tool.created", tool="x", hash_chain=False)
        write_event(p, "tool.deleted", tool="x", hash_chain=False)
        ok, problems = verify_chain(p)
        t("verify ok when no hash fields exist",
          ok and not problems)


# ---------- E2E through MCP server + CLI ----------------------------------

QUICK_IMPL = '''#!/usr/bin/env python3
import json, sys
print(json.dumps({"data":{"ok":1}}))
'''
NOOP_SCHEMA = {"type": "object", "properties": {}}


def _forge(client, name, **kwargs):
    args = {"name": name, "description": name,
            "input_schema": NOOP_SCHEMA, "impl": QUICK_IMPL}
    args.update(kwargs)
    return client.request(
        "tools/call", {"name": "forge_tool", "arguments": args}
    )


def _cli(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "forge.py"),
         "--root", str(root), *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_mcp_audit_chain_intact_after_typical_session():
    print("\n[MCP: forge + call + promote → audit verify clean]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "alpha")
            _forge(client, "beta")
            client.request("tools/call",
                            {"name": "alpha", "arguments": {}})
            client.request("tools/call",
                            {"name": "forge_promote",
                             "arguments": {"name": "alpha"}})
        finally:
            client.close()

        rc, out = _cli(td, "audit-verify")
        t("audit-verify exits 0", rc == 0)
        t("output says audit OK", "audit OK" in out)


def test_mcp_audit_detects_tamper():
    print("\n[CLI: tampering with audit.jsonl makes audit-verify exit 1]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            _forge(client, "alpha")
            _forge(client, "beta")
            _forge(client, "gamma")
        finally:
            client.close()

        # Edit one line in audit.jsonl
        audit_path = td / "audit.jsonl"
        lines = audit_path.read_text().splitlines()
        rec = json.loads(lines[1])
        rec["tool"] = "alpha-evil"
        lines[1] = json.dumps(rec)
        audit_path.write_text("\n".join(lines) + "\n")

        rc, out = _cli(td, "audit-verify")
        t("rc != 0", rc != 0)
        t("output mentions integrity violation",
          "integrity" in out.lower() or "tampered" in out.lower())


def test_security_event_appears_in_chain():
    print("\n[forbidden import → policy.import_denied event chained]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = MCPClient(td)
        try:
            client.initialize()
            # Try to forge a forbidden tool
            client.request("tools/call", {
                "name": "forge_tool",
                "arguments": {
                    "name": "evil",
                    "description": "uses socket",
                    "input_schema": NOOP_SCHEMA,
                    "impl": "import socket\nprint('{}')\n",
                },
            })
        finally:
            client.close()

        rc, out = _cli(td, "audit-verify")
        t("verify still ok after the security event", rc == 0)
        # Confirm the event_type is in the file
        events = [json.loads(l) for l in
                   (td / "audit.jsonl").read_text().splitlines()]
        types = [e["event_type"] for e in events]
        t("policy.import_denied present in audit",
          "policy.import_denied" in types)


def main() -> int:
    test_writes_chain_with_prev_and_hash()
    test_verify_detects_field_tamper()
    test_verify_detects_deleted_record()
    test_verify_detects_appended_fake()
    test_invalid_json_line_does_not_crash_verifier()
    test_non_dict_json_line_does_not_crash_verifier()
    test_hash_chain_disabled_records_dont_break_verify()
    test_mcp_audit_chain_intact_after_typical_session()
    test_mcp_audit_detects_tamper()
    test_security_event_appears_in_chain()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
