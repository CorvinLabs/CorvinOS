"""ADR-0213 — REAL end-to-end verification.

Runs an ACTUAL ACS delegation (real manager + worker `claude -p`
subprocesses via ACSRuntime), lets the real ADR-0213 sync call
(`_sync_acs_result_to_transcript`) write the result into the real claude
CLI transcript, then runs a REAL follow-up `--continue` turn asking "what
did you just delegate?" — and checks that the reply actually recalls the
delegated task and its result.

This is the load-bearing claim ADR-0213 makes: not "a system-prompt hint
says so" (Option B) but "the CLI's OWN on-disk transcript remembers it".
Only a real second `--continue` turn can prove that; nothing here is
mocked apart from the tenant delegation opt-in flag and the house-rules
gate (both bypassed the same way the unit tests in
test_adr0213_context_sync.py bypass them, to isolate this test from
those independent gates).

Costs API credits and takes a few minutes (ACS manager + >=1 worker +
the tool-less sync call + one follow-up turn = 4 real `claude -p`
invocations). Opt-in via CLAUDE_LIVE_E2E=1, mirroring
test_persona_uses_forge_live.py's convention.

Run: CLAUDE_LIVE_E2E=1 python3 core/console/tests/test_adr0213_context_sync_live.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "core" / "console"))
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

# Found during the ADR-0215 adversarial review (2026-07-24): this file's
# original entry point was `main()` + `if __name__ == "__main__":` only —
# no `def test_*` function — so `pytest core/console/tests/` (the normal
# way this suite is run) silently collected ZERO items from it despite the
# filename matching pytest's own `test_*.py` discovery convention. The
# load-bearing ADR-0213 claim this file verifies ("M1 verified end-to-end,
# a REAL run") could therefore never actually fire as part of the test
# suite — only someone who knew to invoke it directly
# (`python3 test_adr0213_context_sync_live.py`) would ever run it. Fixed
# below with a thin `test_`-prefixed wrapper, same `CLAUDE_LIVE_E2E=1` +
# `claude`-on-PATH skip gate as tests/test_tde_e2e_live.py, while keeping
# `main()`/`__main__` for direct standalone invocation (the docstring's
# documented "Run:" instruction still works unchanged).
live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
    reason="live ADR-0213 E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)


def _maybe_skip() -> bool:
    if os.environ.get("CLAUDE_LIVE_E2E") != "1":
        print("SKIP: set CLAUDE_LIVE_E2E=1 to run — spawns several real `claude -p` "
              "processes (ACS manager+worker, ADR-0213 sync call, verification "
              "turn), costs API credits, ~2-5 min runtime.")
        return True
    if shutil.which("claude") is None:
        print("SKIP: `claude` binary not on PATH — install Claude Code first.")
        return True
    return False


async def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    os.environ["CORVIN_HOME"] = tmp.name
    os.environ["CORVIN_TENANT_ID"] = "_default"
    os.environ.pop("VOICE_AUDIT_PATH", None)

    import importlib
    from corvin_console import chat_runtime
    importlib.reload(chat_runtime)
    try:
        import forge.paths as fp
        importlib.reload(fp)
        importlib.reload(chat_runtime)
    except ImportError:
        pass

    # Same two bypasses the unit tests use: the tenant delegation opt-in
    # (deny-by-default, ADR-0114) and the house-rules classifier (would
    # otherwise need a live confidence model call of its own). Everything
    # downstream of these two points — ACSRuntime, the real claude -p
    # spawns, the ADR-0213 sync call, the follow-up turn — is REAL.
    import house_rules as hr  # type: ignore
    hr._house_rules_classifier = (  # type: ignore[assignment]
        lambda task, rules, auth, **kw: ("", 0.0, "test clear")
    )

    cr = chat_runtime
    cr._delegation_enabled = lambda tenant_id: True  # type: ignore[assignment]
    sess = cr.create_session("_default")

    prompt = (
        "/delegate Write a three-line haiku about a lighthouse and save it "
        "to a file named lighthouse.txt. Keep the whole task to one worker."
    )

    print(f"[1/3] Running a REAL ACS delegation turn (session workdir: {sess.workdir})…")
    events = []
    async for ev in cr.stream_turn(sess, prompt):
        events.append(ev)
        if ev.get("type") in ("delta", "notice"):
            snippet = (ev.get("text") or ev.get("message") or "").strip()
            if snippet:
                print("    ", snippet[:160])

    result_events = [e for e in events if e.get("type") == "result"]
    if not result_events:
        print("FAIL: no result event from the delegated turn — events:", events[-5:])
        return 1
    final_text = result_events[-1].get("text", "")
    print(f"[1/3] Delegation result: {final_text[:200]!r}")

    if sess.turn_count != 1:
        print(f"FAIL: expected turn_count==1 after a successfully-synced delegation, "
              f"got {sess.turn_count} — the ADR-0213 sync call did not report success "
              "(C1 fallback engaged). Check the session's chat_debug.jsonl for "
              "os_turn.context_sync / acs.run.* events.")
        return 1
    print("[2/3] turn_count advanced to 1 — the ADR-0213 sync call reported success.")

    follow_up = (
        "In one short sentence: what did you just delegate to ACS workers, "
        "and what was the result? Do not delegate again, do not use any tools."
    )
    print("[3/3] Running a REAL follow-up --continue turn to probe transcript memory…")
    follow_events = []
    async for ev in cr.stream_turn(sess, follow_up):
        follow_events.append(ev)

    follow_result = [e for e in follow_events if e.get("type") == "result"]
    follow_text = (follow_result[-1].get("text", "") if follow_result else "")
    print(f"[3/3] Follow-up reply: {follow_text!r}")

    lowered = follow_text.lower()
    markers = ("haiku", "lighthouse", "leuchtturm")
    if not any(m in lowered for m in markers):
        print("FAIL: the follow-up turn shows no memory of the delegated task — "
              "the --continue transcript does not appear to contain the "
              "ADR-0213 sync turn. This is the failure ADR-0213 exists to fix.")
        tmp.cleanup()
        return 1

    print("PASS — the real --continue follow-up turn recalled the delegated "
          "task from the actual CLI transcript. ADR-0213 verified end-to-end "
          "with a real ACS run.")
    tmp.cleanup()
    return 0


@live
@pytest.mark.live
def test_live_adr0213_context_sync_end_to_end():
    """pytest-discoverable entry point — see module docstring for why this
    was added. Delegates to the same main() the standalone script uses."""
    rc = asyncio.run(main())
    assert rc == 0, "see printed PASS/FAIL diagnostic above for the failure reason"


if __name__ == "__main__":
    if _maybe_skip():
        sys.exit(0)
    sys.exit(asyncio.run(main()))
