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
#:
#: R4 (2026-09-07) closed the whole `_PENDING` backlog. Sites outside
#: `operator/bridges/shared/` reach the helper through the fail-closed shim
#: `operator/bridges/shared/prompt_guard.py`, whose `guard_prompt_head` RAISES
#: when the underlying helper is unimportable — so "the guard was missing" can
#: never degrade into "we spawned on raw chat text".
_MUST_GUARD = {
    # round 1-3
    "core/console/corvin_console/chat_runtime.py",
    "core/console/corvin_console/routes/assistant.py",
    "core/console/corvin_console/task_worker_pool.py",
    "operator/bridges/shared/agents/claude_code.py",   # defines the helper
    "operator/orchestration/tde/worker_ipc.py",
    "operator/voice/scripts/summarize.py",
    # round 4 — bridge helper models fed raw public-channel chat text
    "operator/bridges/shared/acs_classify.py",
    "operator/bridges/shared/acs_gate_chain.py",
    "operator/bridges/shared/acs_runtime.py",
    "operator/bridges/shared/compute_narrator.py",
    "operator/bridges/shared/dialectic.py",
    "operator/bridges/shared/house_rules.py",
    "operator/bridges/shared/memory_bridge.py",
    "operator/bridges/shared/output_sentinel.py",
    "operator/bridges/shared/router.py",
    "operator/bridges/shared/ulo_compliance.py",
    "operator/bridges/shared/user_model.py",
    "operator/bridges/shared/user_style.py",
    # round 4 — console / delegate / workflow / orchestration / voice surfaces
    "core/compute/corvin_compute/fabric/oracle/oracle.py",
    "core/console/corvin_console/browser/agent.py",
    "core/console/corvin_console/routes/workflows.py",
    "core/delegate/corvin_delegate/output_judge.py",
    "core/delegate/corvin_delegate/prompt_safety.py",
    "core/workflows/corvin_workflows/engines_claude.py",
    "operator/context_engineering/stages/llm_synthesis.py",
    "operator/orchestration/tde/analysis_runner.py",
    "operator/orchestration/tde/loss_judge.py",
    "operator/orchestration/tde/tde_engine.py",
    "operator/skill_creator/llm_client.py",
    "operator/voice/hooks/artifact_register.py",
    "operator/voice/scripts/engine_canary.py",
    "scripts/run_spotify_workflow_demo.py",
}

#: Files the discovery regex finds because they MENTION the argv shape, but
#: which never hand any text to the CLI themselves — there is nothing here to
#: guard. Each entry names what the file actually contains. This is NOT a
#: backlog: a file only belongs here when it has no prompt at all.
_NO_CLI_TEXT = {
    "core/compute/corvin_compute/fabric/config.py":
        "dataclass default for `OracleConfig.subprocess_cmd` — an argv "
        "TEMPLATE with no prompt element. The spawn that uses it lives in "
        "fabric/oracle/oracle.py, which is in _MUST_GUARD.",
    "operator/bridges/shared/engines/system_prompt_injector.py":
        "pure argv-rewriting helper — the two `claude -p` occurrences are "
        "docstring examples; the module has no subprocess call of its own.",
}

#: Spawn sites NOT yet routed through the helper. EMPTY since R4 (2026-09-07).
#: Anything landing here is an OPEN finding, not a blessed exemption — the
#: reason string must say why the text cannot reach the CLI unguarded.
_PENDING: dict[str, str] = {}


#: Sites that import the guard defensively (`_guard_prompt_head = None` on a
#: failed import) predate the `prompt_guard` shim and carry their refusal
#: branch inline. Every other site imports from the shim, whose own contract
#: test (`operator/bridges/shared/test_spawn_prompt_guard.py::
#: test_prompt_guard_refuses_instead_of_returning_raw_text`) proves the call
#: raises rather than returning the caller's text.
_INLINE_NONE_CHECK = {
    "core/console/corvin_console/task_worker_pool.py",
    "core/console/corvin_console/routes/assistant.py",
    "operator/orchestration/tde/worker_ipc.py",
}

#: Sites that import ``agents.claude_code.guard_prompt_head`` DIRECTLY at the
#: call site with no ``except ImportError`` fallback at all — an unimportable
#: helper raises right there and the spawn never happens. Equally fail-closed;
#: the test below proves the file binds no ``None`` fallback.
_DIRECT_HARD_IMPORT = {
    "core/console/corvin_console/chat_runtime.py",
    "operator/voice/scripts/summarize.py",
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
    """A site that cannot reach the helper must REFUSE, not continue.

    Two admissible shapes, and nothing else:

    * the three round-1..3 sites import ``agents.claude_code`` directly and
      carry an explicit ``_guard_prompt_head is None`` refusal branch;
    * every other site imports ``guard_prompt_head`` from the
      ``prompt_guard`` shim, which RAISES on an unimportable helper — so
      there is no ``None`` to check and no pass-through to fall into.

    ``None`` + a later call is a TypeError deep in the spawn path; the
    contract is an explicit refusal.
    """
    for rel in sorted(_MUST_GUARD):
        if rel.endswith("agents/claude_code.py"):
            continue  # defines the helper; nothing to import
        src = (_REPO / rel).read_text(encoding="utf-8")
        if rel in _INLINE_NONE_CHECK:
            assert "_guard_prompt_head is None" in src, rel
            idx = src.index("_guard_prompt_head is None")
            window = src[idx: idx + 600]
            assert ("raise" in window or "return" in window), rel
            continue
        if rel in _DIRECT_HARD_IMPORT:
            assert "from agents.claude_code import guard_prompt_head" in src, rel
            assert "_guard_prompt_head = None" not in src, (
                f"{rel} is listed as a hard import but binds a None fallback"
            )
            continue
        assert "from prompt_guard import guard_prompt_head" in src, (
            f"{rel} must reach the guard through the fail-closed shim "
            "`operator/bridges/shared/prompt_guard.py` (or carry an inline "
            "`_guard_prompt_head is None` refusal and be listed in "
            "_INLINE_NONE_CHECK / _DIRECT_HARD_IMPORT)"
        )
        # the import fallback must never bind a pass-through stub
        tail = src.split("def _guard_prompt_head", 1)[-1][:600]
        assert "return _text" not in tail and "return text" not in tail, (
            f"{rel}'s fallback returns the caller's text instead of refusing"
        )


def test_the_shim_itself_never_returns_unguarded_text():
    """`prompt_guard.guard_prompt_head` has no branch that returns its input.

    This is the single assumption every non-inline site above rests on, so it
    is asserted structurally here as well as behaviourally in the bridge test.
    """
    src = (_REPO / "operator/bridges/shared/prompt_guard.py").read_text(encoding="utf-8")
    body = src[src.index("def guard_prompt_head("):]
    body = body[: body.index("\ndef ")]
    assert "raise PromptGuardUnavailable" in body, body
    assert "return text" not in body and "return body" not in body, body


def test_no_ledger_entry_is_in_two_categories():
    overlap = (_MUST_GUARD & set(_PENDING)) | (_MUST_GUARD & set(_NO_CLI_TEXT)) \
        | (set(_PENDING) & set(_NO_CLI_TEXT))
    assert not overlap, sorted(overlap)


def test_pending_is_empty():
    """R4 closed the backlog. A non-empty _PENDING is an OPEN finding."""
    assert not _PENDING, (
        "unguarded `claude -p` spawn site(s) still open: "
        + ", ".join(f"{k} ({v})" for k, v in sorted(_PENDING.items()))
    )


def test_no_unclassified_claude_spawn_site_exists():
    """The discovery gap that produced R3-C1 itself: a new module may not spawn
    the CLI without landing in this ledger."""
    found = set(_discover())
    known = _MUST_GUARD | set(_PENDING) | set(_NO_CLI_TEXT)
    new = sorted(found - known)
    assert not new, (
        "new `claude -p` spawn site(s) with no ledger entry: " + ", ".join(new)
        + " — route the prompt through the fail-closed shim "
          "operator/bridges/shared/prompt_guard.py and add the file to "
          "_MUST_GUARD, or (only when it hands NO text to the CLI at all) "
          "justify it in _NO_CLI_TEXT"
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
