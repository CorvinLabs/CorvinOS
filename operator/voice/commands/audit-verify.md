---
description: Verify hash-chain integrity of the voice + forge audit log
argument-hint: ""
---

Walks the SHA-chained audit log at `~/.config/corvin-voice/forge/audit.jsonl`
end-to-end. Exits 0 when the chain holds, exits 1 (with line + issue) on
tampering, exits 2 on IO errors.

Run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/voice_audit.py verify
```

If the chain is intact, just confirm "audit OK". If tampering is reported,
list the affected line numbers and issue types ("tampered" — hash mismatch;
"broken_chain" — prev_hash gap; "invalid_json" — malformed line) and tell
the user the path to the audit file and the exit code.

This is the same check `bash operator/bridges/run-all-tests.sh`
covers via the `Python: audit unified` slot, but invocable on demand
without re-running every test.
