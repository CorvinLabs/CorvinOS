"""R3-C1 (adversarial review round 3, 2026-09-07) — spawn-site enumeration.

Rounds 1–2 hardened the `claude -p` spawn sites they happened to know about
and missed one: ``corvin_console/routes/assistant.py`` imported neither the
prompt-head sentinel nor the ``@``-neutraliser and passed the operator's
message as a POSITIONAL argv element, so ``--version`` was parsed as a CLI
FLAG (the binary printed its version, no model turn) and ``/pwn`` expanded a
project slash command from the spawn cwd.

That was a *discovery* failure, not a coding one — nothing enumerated the
spawn sites, so "did we get them all?" had no answer. This file walks the repo
for every module that spawns the claude CLI with ``-p`` and holds it against a
LEDGER. A new module that spawns the CLI fails the test until it is classified,
so a fourth unguarded route cannot be added silently.

Two assertions:

  1. every module in ``_MUST_GUARD`` reaches ``guard_prompt_head`` (it forwards
     user/operator text to the model);
  2. the discovered set of spawn sites equals ``_MUST_GUARD | _PENDING`` — a
     new file is a FAILURE, and removing a pending one is also a failure so the
     ledger cannot rot.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

_SCAN_ROOTS = ("core", "operator", "scripts")
_SKIP_DIRS = {
    ".git", ".claude", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "dist.next", "dist.prev", ".corvin", "outputs", "tests", "test",
    "e2e",
}

#: An argv element that is exactly ``-p`` (never ``--pin``, never ``ps -p``'s
#: neighbours — those are filtered by the claude-binary requirement below).
_DASH_P = re.compile(r"""(?<![\w-])["']-p["']""")

#: A reference to the claude CLI binary, in any of the forms this repo uses.
_CLAUDE_BIN = re.compile(
    r"""(["']claude["']|CLAUDE_BIN|claude_bin|_claude_argv|resolve_claude_bin"""
    r"""|ClaudeCodeEngine|_resolve_helper_claude_bin)"""
)

#: Modules that forward user- or operator-authored text into `claude -p` and
#: therefore MUST route it through the ONE shared neutraliser
#: (`agents.claude_code.guard_prompt_head`): byte-0 sentinel (R2-E1) plus the
#: `@<path>` client-side file-expansion joiner (R3-C2).
_MUST_GUARD = {
    "core/console/corvin_console/chat_runtime.py",
    "core/console/corvin_console/routes/assistant.py",
    "core/console/corvin_console/task_worker_pool.py",
    "operator/bridges/shared/agents/claude_code.py",   # defines the helper
    "operator/orchestration/tde/worker_ipc.py",
    "operator/voice/scripts/summarize.py",
}

#: Spawn sites NOT yet routed through the helper, each with the reason it is
#: still open. Round 3's remit covered the console/bridge/voice/TDE surface
#: above; these are the same finding class on other surfaces and are tracked
#: rather than blessed. Shrinking this set is the goal — GROWING it (or adding
#: a brand-new file) fails the test below.
_PENDING = {
    # bridge helper models that DO receive chat text
    "operator/bridges/shared/acs_classify.py": "ACS classifier sees the raw user message",
    "operator/bridges/shared/acs_gate_chain.py": "gate chain sees the raw user message",
    "operator/bridges/shared/acs_runtime.py": "ACS runtime sees the raw user message",
    "operator/bridges/shared/compute_narrator.py": "narrates compute results back to the user",
    "operator/bridges/shared/dialectic.py": "dialectic helper is fed the user's task",
    "operator/bridges/shared/house_rules.py": "L44 classifier is fed the raw user message",
    "operator/bridges/shared/memory_bridge.py": "recall helper is fed recalled user text",
    "operator/bridges/shared/output_sentinel.py": "sees the model output it screens",
    "operator/bridges/shared/router.py": "auto-router is fed the raw user message",
    "operator/bridges/shared/ulo_compliance.py": "fed user-authored text",
    "operator/bridges/shared/user_model.py": "fed user-authored text",
    "operator/bridges/shared/user_style.py": "fed user-authored text",
    "operator/context_engineering/stages/llm_synthesis.py": "synthesises over user context",
    # operator-triggered, not public-facing
    "core/compute/corvin_compute/fabric/config.py": "argv template only, no prompt",
    "core/compute/corvin_compute/fabric/oracle/oracle.py": "operator-run oracle",
    "core/console/corvin_console/browser/agent.py": "operator-run browser agent",
    "core/console/corvin_console/routes/workflows.py": "operator-authored workflow steps",
    "core/delegate/corvin_delegate/output_judge.py": "judges model output",
    "core/delegate/corvin_delegate/prompt_safety.py": "classifier over the prompt",
    "core/workflows/corvin_workflows/engines_claude.py": "operator-authored workflow steps",
    "operator/bridges/shared/engines/system_prompt_injector.py": "docstring examples only",
    "operator/orchestration/tde/analysis_runner.py": "operator-run analysis",
    "operator/orchestration/tde/loss_judge.py": "judges model output",
    "operator/orchestration/tde/tde_engine.py": "operator-run planner",
    "operator/skill_creator/llm_client.py": "operator-run skill creation",
    "operator/voice/hooks/artifact_register.py": "operator-run hook",
    "operator/voice/scripts/engine_canary.py": "fixed canary prompt",
    "scripts/run_spotify_workflow_demo.py": "demo script, fixed prompt",
}


def _discover() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in _SCAN_ROOTS:
        base = _REPO / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if not fn.endswith(".py") or fn.startswith("test_") or fn.endswith("_test.py"):
                    continue
                path = Path(dirpath) / fn
                try:
                    src = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if _DASH_P.search(src) and _CLAUDE_BIN.search(src):
                    found[path.relative_to(_REPO).as_posix()] = path
    return found


def test_every_guarded_spawn_site_reaches_the_shared_helper():
    found = _discover()
    for rel in sorted(_MUST_GUARD):
        path = found.get(rel) or (_REPO / rel)
        assert path.exists(), f"{rel} disappeared — update the ledger"
        src = path.read_text(encoding="utf-8")
        assert "guard_prompt_head" in src, (
            f"{rel} spawns `claude -p` on user text but never mentions "
            "guard_prompt_head — the prompt reaches the CLI unneutralised, so "
            "`@/etc/passwd` in a chat message is a file read and a byte-0 "
            "`/`/`!`/`#` is a client-side command"
        )


def test_guarded_sites_fail_closed_when_the_helper_is_missing():
    """A site that imports the helper defensively must REFUSE, not continue.

    ``None`` + a later call is a TypeError deep in the spawn path; the contract
    is an explicit refusal, so grep for a raise/return next to the None check.
    """
    for rel in ("core/console/corvin_console/task_worker_pool.py",
                "core/console/corvin_console/routes/assistant.py",
                "operator/orchestration/tde/worker_ipc.py"):
        src = (_REPO / rel).read_text(encoding="utf-8")
        assert "_guard_prompt_head is None" in src, rel
        # the None branch must not fall through to a spawn
        idx = src.index("_guard_prompt_head is None")
        window = src[idx: idx + 600]
        assert ("raise" in window or "return" in window), rel


def test_no_unclassified_claude_spawn_site_exists():
    """The discovery gap that produced R3-C1 itself: a new module may not spawn
    the CLI without landing in this ledger."""
    found = set(_discover())
    known = _MUST_GUARD | set(_PENDING)
    new = sorted(found - known)
    assert not new, (
        "new `claude -p` spawn site(s) with no ledger entry: " + ", ".join(new)
        + " — route the prompt through agents.claude_code.guard_prompt_head "
          "and add the file to _MUST_GUARD, or justify it in _PENDING"
    )
    gone = sorted(known - found)
    assert not gone, (
        "ledger entries that no longer spawn `claude -p`: " + ", ".join(gone)
        + " — drop them from the ledger so it keeps meaning something"
    )


def test_the_assistant_route_no_longer_puts_the_prompt_on_argv():
    """R3-C1's second half: the prompt travelled as a POSITIONAL argv element,
    so `--version` was parsed as a flag. It must go over stdin.

    The behavioural proof is in ``test_assistant_route_prompt_guard.py`` (real
    router + real exec of a recording binary); this is the cheap source-level
    tripwire that fails the moment someone puts it back.
    """
    src = (_REPO / "core/console/corvin_console/routes/assistant.py").read_text(encoding="utf-8")
    # the `-p` spawn, not the `--version` availability probe of /assistant/ping
    idx = src.index('"-p",')
    spawn = src[src.rindex("subprocess.run(", 0, idx):]
    spawn = spawn[: spawn.index("capture_output")]
    argv_literal = spawn[spawn.index("[") + 1: spawn.index("]")]
    for banned in ("full_prompt", "body.message", "ctx_tag", "history_block"):
        assert banned not in argv_literal, (
            f"{banned} is still a positional argv element in routes/assistant.py: "
            + argv_literal
        )
    assert "input=full_prompt" in spawn, spawn
