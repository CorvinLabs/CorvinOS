#!/usr/bin/env python3
"""Per-subtask E2E for operator/voice/hooks/web_trust_gate.py — the
PreToolUse hook that powers the Quiet Dialectic Layer (QDL).

Asserts:
  - classify_source resolves green/wiki/yellow/red/satire correctly
    via exact, suffix, and TLD-hint paths
  - unknown domains land in tier=unknown, not in a blocking state
  - non-http URLs (file://, mailto:, data:) are skipped gracefully
  - main() reads PreToolUse JSON from stdin, emits a stdout JSON
    block with hookSpecificOutput.additionalContext, exits 0
  - WebSearch with no allowed_domains gets the 'search-intent' marker
  - WebFetch on a green source emits one classification line
  - main() never denies (exit 0 in every code path) — QDL is a
    speech-modulator, not a gate
  - audit emission writes one event per classified source, with the
    expected shape (best-effort — failure is silent)

Run: python3 operator/voice/hooks/test_web_trust_gate.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "web_trust_gate.py"

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


# ── helpers ──────────────────────────────────────────────────────────

def run_hook(payload: dict, *, env: dict | None = None) -> tuple[int, str, str]:
    """Spawn the hook as Claude Code would: stdin = payload JSON,
    stdout = hookSpecificOutput JSON or empty, stderr = (unused).
    """
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True,
        env=full_env, timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def import_module():
    """Import the hook module so we can call classify_source() directly."""
    sys.path.insert(0, str(HERE))
    import web_trust_gate  # type: ignore
    # Reset the mtime cache between tests so a hot-reload of data.json
    # in test 4 doesn't get the stale dict.
    web_trust_gate._cache["mtime"] = 0.0
    web_trust_gate._cache["data"] = None
    return web_trust_gate


def main() -> int:
    wtg = import_module()

    # ── 1. classify_source: exact match (green) ─────────────────────
    c = wtg.classify_source("https://www.reuters.com/world/europe/foo")
    expect(c["tier"] == "green" and c["domain"] == "reuters.com"
           and c["source"] == "exact",
           "classify exact: reuters → green",
           f"got {c}")

    # ── 2. classify_source: suffix match (BBC subdomain) ────────────
    c = wtg.classify_source("https://news.bbc.co.uk/something")
    expect(c["tier"] == "green" and c["domain"] == "news.bbc.co.uk"
           and c["source"] == "suffix",
           "classify suffix: news.bbc.co.uk → green via bbc.co.uk")

    # ── 3. classify_source: TLD hint (.gov) ─────────────────────────
    c = wtg.classify_source("https://nasa.gov/exoplanet-x")
    expect(c["tier"] == "green" and c["source"] == "tld",
           "classify TLD: nasa.gov → green via .gov hint",
           f"got {c}")

    # ── 4. classify_source: yellow / red / satire / wiki ────────────
    expect(wtg.classify_source("https://www.bild.de/x")["tier"] == "yellow",
           "classify exact: bild.de → yellow")
    expect(wtg.classify_source("https://infowars.com/y")["tier"] == "red",
           "classify exact: infowars → red")
    expect(wtg.classify_source("https://der-postillon.com/x")["tier"] == "satire",
           "classify exact: postillon → satire")
    expect(wtg.classify_source("https://en.wikipedia.org/wiki/Foo")["tier"] == "wiki",
           "classify suffix: en.wikipedia.org → wiki")

    # ── 5. classify_source: unknown domain → tier=unknown, not blocked
    c = wtg.classify_source("https://random-novel-domain-xyz9999.com/")
    expect(c["tier"] == "unknown" and c["source"] == "unknown",
           "classify unknown domain → tier=unknown",
           f"got {c}")

    # ── 6. classify_source: non-http URL → skipped ───────────────────
    expect(wtg.classify_source("file:///etc/hosts")["source"] == "skipped",
           "classify file:// → skipped (non-http)")
    expect(wtg.classify_source("mailto:foo@bar.com")["source"] == "skipped",
           "classify mailto: → skipped")
    expect(wtg.classify_source("")["source"] == "skipped",
           "classify empty → skipped")

    # ── 7. main(): WebFetch → exit 0 + additionalContext on stdout ─
    rc, out, err = run_hook({
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://www.reuters.com/world/europe/foo"},
    })
    expect(rc == 0, f"WebFetch hook exits 0 (rc={rc})", err.strip())
    parsed = None
    if out.strip():
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            pass
    expect(parsed is not None, "WebFetch hook emits valid JSON on stdout",
           f"raw stdout: {out!r}")
    if parsed is not None:
        ctx_block = (parsed.get("hookSpecificOutput") or {}).get("additionalContext", "")
        expect("Source provenance" in ctx_block,
               "WebFetch context block has 'Source provenance' header")
        expect("reuters.com" in ctx_block,
               "WebFetch context names the resolved domain")
        expect("tier=green" in ctx_block,
               "WebFetch context shows tier=green")
        expect("[QDL — Source Trust Layer" in ctx_block,
               "WebFetch context carries the QDL hidden-context marker")
        expect("never name the trust layer" in ctx_block.lower()
               or "do not mention to user" in ctx_block.lower(),
               "WebFetch context tells the model NOT to mention the layer")

    # ── 8. main(): WebSearch with no allowed_domains → search-intent
    rc, out, err = run_hook({
        "tool_name": "WebSearch",
        "tool_input": {"query": "claude code 5"},
    })
    expect(rc == 0, "WebSearch hook exits 0", err.strip())
    parsed = json.loads(out) if out.strip() else None
    expect(parsed is not None, "WebSearch (open) emits a context block")
    if parsed:
        ctx = (parsed.get("hookSpecificOutput") or {}).get("additionalContext", "")
        expect("search-intent" in ctx
               or "results pending" in ctx,
               "WebSearch (open) marks results as search-intent")

    # ── 9. main(): WebSearch with allowed_domains → classified ──────
    rc, out, err = run_hook({
        "tool_name": "WebSearch",
        "tool_input": {"query": "EU AI Act enforcement",
                       "allowed_domains": ["reuters.com", "rt.com"]},
    })
    expect(rc == 0, "WebSearch (allowed_domains) exits 0", err.strip())
    parsed = json.loads(out) if out.strip() else None
    if parsed:
        ctx = (parsed.get("hookSpecificOutput") or {}).get("additionalContext", "")
        expect("reuters.com" in ctx and "tier=green" in ctx,
               "allowed_domains: reuters classified green")
        expect("rt.com" in ctx and "tier=red" in ctx,
               "allowed_domains: rt classified red")

    # ── 10. main(): non-matching tool_name → silent allow (no stdout)
    rc, out, err = run_hook({
        "tool_name": "Read",
        "tool_input": {"path": "/etc/hosts"},
    })
    expect(rc == 0 and not out.strip(),
           "non-matching tool → silent allow (rc=0, empty stdout)",
           f"rc={rc} stdout={out!r}")

    # ── 11. main(): empty stdin → exit 0 silent ─────────────────────
    proc = subprocess.run(["python3", str(HOOK)],
                          input="", capture_output=True, text=True)
    expect(proc.returncode == 0 and not proc.stdout.strip(),
           "empty stdin → exit 0 silent")

    # ── 12. main(): malformed JSON → exit 0 silent ──────────────────
    proc = subprocess.run(["python3", str(HOOK)],
                          input="{not json", capture_output=True, text=True)
    expect(proc.returncode == 0 and not proc.stdout.strip(),
           "malformed JSON → exit 0 silent")

    # ── 13. Hot-reload: edit data.json in a sandbox → next call sees it
    #       This sandboxes the data file, swaps in a custom domain,
    #       and re-imports to verify suffix matching still works.
    sandbox = Path(tempfile.mkdtemp(prefix="qdl-hot-reload-"))
    custom_data_file = sandbox / "source_trust_data.json"
    custom_data_file.write_text(json.dumps({
        "domains": {"sandbox-only-domain.test": {"tier": "yellow", "note": "test"}},
        "tld_hints": {},
    }))
    # Override the module's DATA_FILE pointer + reset cache.
    wtg.DATA_FILE = custom_data_file
    wtg._cache["mtime"] = 0.0
    wtg._cache["data"] = None
    c = wtg.classify_source("https://sandbox-only-domain.test/path")
    expect(c["tier"] == "yellow",
           "hot-reload: sandboxed data.json picked up",
           f"got {c}")
    # And a domain *not* in the sandbox file is unknown now.
    c2 = wtg.classify_source("https://reuters.com/")
    expect(c2["tier"] == "unknown",
           "hot-reload: real reuters.com is unknown when data file is sandboxed")

    # ── 14. Audit emission: smoke-test that a tempdir audit file
    #       gains an event after a WebFetch run. Best-effort: if the
    #       forge package isn't importable in the test environment,
    #       this case is skipped. We use CORVIN_HOME to redirect.
    audit_sandbox = Path(tempfile.mkdtemp(prefix="qdl-audit-"))
    rc, out, err = run_hook(
        {"tool_name": "WebFetch",
         "tool_input": {"url": "https://www.bbc.com/news/article-x"}},
        env={"CORVIN_HOME": str(audit_sandbox)},
    )
    expect(rc == 0, "audit-redirected hook still exits 0")
    audit_file = audit_sandbox / "global" / "forge" / "audit.jsonl"
    if audit_file.exists():
        lines = audit_file.read_text().strip().splitlines()
        if lines:
            try:
                ev = json.loads(lines[-1])
            except json.JSONDecodeError:
                ev = {}
            expect(ev.get("event_type") == "web.source_classified",
                   "audit event 'web.source_classified' emitted",
                   f"got {ev.get('event_type')}")
            details = ev.get("details") or {}
            expect(details.get("tier") == "green"
                   and details.get("domain") == "bbc.com",
                   "audit event details record the classified domain + tier",
                   f"got {details}")
        else:
            print("INFO: audit file empty — forge package likely not "
                  "importable in this env; audit emission silently skipped "
                  "(matches the best-effort contract)")
    else:
        print("INFO: no audit file written — forge package likely not "
              "importable in this env (best-effort contract)")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
