"""R3-C1 / R4-F4 (adversarial review rounds 3+4, 2026-09-07) — spawn-site
enumeration.

Rounds 1–2 hardened the `claude -p` spawn sites they happened to know about
and missed one: ``corvin_console/routes/assistant.py`` imported neither the
prompt-head sentinel nor the ``@``-neutraliser and passed the operator's
message as a POSITIONAL argv element, so ``--version`` was parsed as a CLI
FLAG (the binary printed its version, no model turn) and ``/pwn`` expanded a
project slash command from the spawn cwd.

That was a *discovery* failure, not a coding one — nothing enumerated the
spawn sites, so "did we get them all?" had no answer. This file walks the repo
and holds every module that starts the claude CLI against a LEDGER.

**Round 4 rebuilt the discovery, because round 3's was blind by construction.**
It required a quoted literal ``-p`` argv element in the source. Every module
that spawns through ``ClaudeCodeEngine.spawn()`` — the abstraction this repo
standardises on, and the one that appends ``-p`` itself inside ``_build_args``
— writes no ``"-p"`` at all and was therefore structurally invisible: the
gateway run dispatcher, the A2A worker, the delegate MCP tools, the bridge
adapter and four more. The ledger was green while three unguarded HIGH-severity
routes were live. Two things changed:

  * discovery now recognises BOTH shapes — a hand-built argv (``"-p"`` plus a
    claude-binary reference) **and** an engine-mediated spawn
    (``ClaudeCodeEngine`` / ``claude_code`` plus a ``.spawn(`` / ``.inject(``
    call) — and it scans the WHOLE repo minus ``_SKIP_DIRS`` instead of only
    ``core``/``operator``/``scripts`` (which had left three ``benchmark/``
    spawns unclassified);
  * the neutraliser moved INSIDE the engine (``_build_args`` / ``spawn`` /
    ``inject``), so an engine-mediated site is guarded by construction. That is
    what ``_ENGINE_GUARDED`` records, and ``test_the_engine_guards_every_payload_it_emits``
    pins the property those entries rest on.

Assertions:

  1. every module in ``_MUST_GUARD`` reaches ``guard_prompt_head`` (it builds
     the payload itself, so it must call the helper itself);
  2. every module in ``_ENGINE_GUARDED`` spawns ONLY through the engine — it
     may not hand-build a ``claude -p`` argv, which would step around the
     engine's guard;
  3. the engine really does guard ``_build_args`` / ``spawn`` / ``inject``;
  4. the discovered set equals the union of the ledger categories — a new file
     is a FAILURE, and removing one is also a failure so the ledger cannot rot.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

#: R4-F4: the whole repository, not three hand-picked roots. ``benchmark/``
#: alone carried three unclassified ``claude -p`` spawns.
_SCAN_ROOT = _REPO
_SKIP_DIRS = {
    ".git", ".claude", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "dist.next", "dist.prev", ".corvin", "outputs", "tests", "test",
    "e2e", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    "build", ".idea", ".tox", "htmlcov",
}

#: An argv element that is exactly ``-p`` (never ``--pin``, never ``ps -p``'s
#: neighbours — those are filtered by the claude-binary requirement below).
_DASH_P = re.compile(r"""(?<![\w-])["']-p["']""")

#: A reference to the claude CLI binary, in any of the forms this repo uses.
_CLAUDE_BIN = re.compile(
    r"""(["']claude["']|CLAUDE_BIN|claude_bin|_claude_argv|resolve_claude_bin"""
    r"""|ClaudeCodeEngine|_resolve_helper_claude_bin)"""
)

#: R4-F4 — the second, previously invisible shape: the module never writes
#: ``"-p"`` because ``ClaudeCodeEngine._build_args`` appends it. Discovery
#: needs a reference to the engine AND an actual ``.spawn(`` / ``.inject(``
#: call, so a module that merely imports the type is not dragged in.
_ENGINE_REF = re.compile(r"""(ClaudeCodeEngine|claude_code)""")
_ENGINE_SPAWN = re.compile(r"""\.\s*(spawn|inject)\s*\(""")

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
    # R4-F4 — the primary Discord/Telegram/WhatsApp/e-mail spawn path. It is
    # engine-mediated (so the engine guards it anyway) but it ALSO assembles
    # `_spawn_prompt` / `_stdin_prompt` itself and writes a raw JSONL user
    # message on the legacy `/btw` stdin path, so its own guard calls are
    # load-bearing and are pinned here rather than in _ENGINE_GUARDED.
    "operator/bridges/shared/adapter.py",
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
    "core/delegate/corvin_delegate/mcp_config_builder.py":
        "builds an MCP config dict; the single `engine.spawn(...)` occurrence "
        "is a docstring describing the kwargs it returns.",
    "operator/bridges/shared/engine_registry.py":
        "engine factory/registry — the `WorkerEngine.spawn()` occurrence is a "
        "docstring; the registry hands back engine INSTANCES, never a prompt.",
}

#: R4-F4 — sites that never build a claude argv of their own: they hand a
#: prompt to ``ClaudeCodeEngine.spawn()`` / ``.inject()`` and the ENGINE
#: neutralises it (``claude_code._build_args`` / ``spawn`` / ``inject`` all
#: call ``guard_prompt_head``). These were the invisible ones — every entry
#: below was an unguarded live route until round 4, and none of them could be
#: seen by round 3's ``"-p"``-shaped discovery.
#:
#: The invariant an entry here rests on is NOT "the author remembered", it is
#: "this file cannot emit a claude argv". ``test_engine_mediated_sites_never_
#: hand_build_a_claude_argv`` enforces exactly that: the moment such a file
#: grows a hand-built ``-p`` argv it is reclassified as ``argv`` by discovery,
#: leaves this category and fails the ledger until it calls the helper itself.
_ENGINE_GUARDED = {
    "core/console/corvin_core/aco/patch_generator.py":
        "ACO patch generator — `collect(engine.spawn(prompt, **kw))`.",
    "core/delegate/corvin_delegate/delegation.py":
        "R4-F5 — `worker.spawn(prompt)` for the `delegate_*` MCP tools; "
        "`_validate_prompt` is a type/length check, not an injection guard.",
    "core/gateway/corvin_gateway/dispatcher.py":
        "R4-F2 — `POST /v1/tenants/{tid}/runs` drives `engine.spawn(spec.input)` "
        "under `--dangerously-skip-permissions`, behind a TENANT jwt (ADR-0007: "
        "a tenant is not the operator).",
    "operator/bridges/shared/a2a_worker.py":
        "R4-F3 — remote-authored A2A instruction. The guard MUST run after "
        "`sanitize_instruction` (which strips U+2060); spawning through the "
        "engine is what makes that ordering automatic.",
    "operator/bridges/shared/awp_walker.py":
        "AWP walker — `engine.spawn(prompt=prompt)` over the engine protocol.",
}

#: Operator-run measurement harnesses under ``benchmark/``. Their prompt is
#: assembled from repo-checked-in fixture JSON by a human running the script;
#: no channel, network or tenant input reaches them. They are NOT guarded on
#: purpose: the sentinel line would change the very token counts and recall
#: rates they exist to measure. The `benchmark/` path prefix is asserted below
#: so this category cannot be used to excuse a `core/` or `operator/` site.
_OFFLINE_FIXTURE_HARNESS = {
    "benchmark/savings-vs-hallucination/run_benchmark.py":
        "context-pruning vs hallucination benchmark; prompt built from "
        "tasks JSON in the same directory.",
    "benchmark/savings-vs-hallucination/run_benchmark_v2.py":
        "v2 of the same harness, importing run_benchmark's fixtures.",
    "benchmark/token-savings/measure_tooldisabled.py":
        "tool-disabled token-savings measurement; prompt is the fixture "
        "context itself, which must reach the CLI byte-exact.",
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

#: Sites whose import fallback DEFINES a stand-in that raises. Same contract as
#: the shim (calling it refuses; nothing returns the caller's text) but written
#: inline, because these modules predate `prompt_guard.py` and are themselves
#: inside `operator/bridges/shared/`, where the shim would be a self-import.
_RAISING_STUB = {
    "operator/bridges/shared/adapter.py",
}


def _classify_source(src: str) -> str | None:
    """``"argv"`` (hand-built claude argv), ``"engine"`` (spawns through
    ``ClaudeCodeEngine``) or ``None`` (not a spawn site).

    ``"argv"`` wins when a file does both, because a hand-built argv is the
    shape that can step around the engine's guard.
    """
    if _DASH_P.search(src) and _CLAUDE_BIN.search(src):
        return "argv"
    if _ENGINE_REF.search(src) and _ENGINE_SPAWN.search(src):
        return "engine"
    return None


def _discover() -> dict[str, str]:
    """rel-path → ``"argv"`` / ``"engine"`` for every spawn site in the repo."""
    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(_SCAN_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py") or fn.startswith("test_") or fn.endswith("_test.py"):
                continue
            path = Path(dirpath) / fn
            try:
                src = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            kind = _classify_source(src)
            if kind is not None:
                found[path.relative_to(_REPO).as_posix()] = kind
    return found


def test_every_guarded_spawn_site_reaches_the_shared_helper():
    for rel in sorted(_MUST_GUARD):
        path = _REPO / rel
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

    Three admissible shapes, and nothing else:

    * the three round-1..3 sites import ``agents.claude_code`` directly and
      carry an explicit ``_guard_prompt_head is None`` refusal branch;
    * ``adapter.py`` defines a RAISING stand-in in its own import fallback
      (it lives inside ``operator/bridges/shared/``, where the shim would be
      a self-import);
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
        if rel in _RAISING_STUB:
            assert "_guard_prompt_head = None" not in src, (
                f"{rel} is listed as a raising stub but binds a None fallback"
            )
            idx = src.index("def _guard_prompt_head(")
            stub = src[idx: idx + 900]
            assert "raise" in stub, (
                f"{rel}'s fallback `_guard_prompt_head` does not raise — an "
                "unimportable helper must refuse, never pass text through"
            )
            assert "return text" not in stub, rel
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


def test_engine_mediated_sites_never_hand_build_a_claude_argv():
    """The property every ``_ENGINE_GUARDED`` entry rests on.

    Those files are safe because the neutraliser lives inside
    ``ClaudeCodeEngine`` — which holds exactly as long as they keep going
    THROUGH the engine. A hand-built ``claude … -p … <prompt>`` argv in one of
    them would step around the guard and would be invisible again, which is
    the failure this whole file exists to prevent. Discovery classifies such a
    file as ``"argv"``; this asserts none of them is.
    """
    found = _discover()
    for rel in sorted(_ENGINE_GUARDED):
        assert (_REPO / rel).exists(), f"{rel} disappeared — update the ledger"
        kind = found.get(rel)
        assert kind == "engine", (
            f"{rel} is ledgered as engine-mediated (the engine applies "
            f"guard_prompt_head for it) but discovery now classifies it as "
            f"{kind!r} — it builds a claude argv itself, so it must call the "
            "shared neutraliser and move to _MUST_GUARD"
        )


def test_the_engine_guards_every_payload_it_emits():
    """R4-F1/F2/F3/F5's structural fix: the guard is IN the engine.

    Rounds 1–3 applied ``guard_prompt_head`` per call site, which makes the
    invariant "did every author remember?" — and four live routes had not.
    ``_build_args`` covers the positional-argv transport, ``spawn`` covers the
    stream-json stdin transport (it writes the initial user message itself),
    and ``inject`` covers the SECOND user message of a live turn (``/btw``),
    which is neither of the other two and was completely unguarded.

    The behavioural proofs are
    ``operator/bridges/shared/test_engine_guarded_spawn.py`` (real subprocess,
    real pipe bytes) and ``core/gateway/tests/test_dispatcher_prompt_guard.py``
    (real FastAPI route); this is the cheap source-level tripwire.
    """
    src = (_REPO / "operator/bridges/shared/agents/claude_code.py").read_text(
        encoding="utf-8")
    for func in ("_build_args", "spawn", "inject"):
        start = src.index(f"    def {func}(")
        nxt = src.find("\n    def ", start + 1)
        body = src[start: nxt if nxt != -1 else len(src)]
        assert "guard_prompt_head(" in body, (
            f"ClaudeCodeEngine.{func}() no longer calls guard_prompt_head — "
            "every _ENGINE_GUARDED ledger entry silently becomes an unguarded "
            "spawn site the moment this is removed"
        )


def test_offline_harnesses_are_confined_to_the_benchmark_tree():
    """``_OFFLINE_FIXTURE_HARNESS`` is the one category with no guard at all.

    It is admissible only because the prompt comes from repo-checked-in
    fixtures under an operator's own hand, and because a sentinel line would
    corrupt the measurement. Confine it structurally so it can never be used
    to excuse a shipped code path.
    """
    for rel in sorted(_OFFLINE_FIXTURE_HARNESS):
        assert rel.startswith("benchmark/"), (
            f"{rel} is not under benchmark/ — an unguarded spawn outside the "
            "measurement harnesses is a finding, not a ledger category"
        )
        assert (_REPO / rel).exists(), f"{rel} disappeared — update the ledger"


def test_no_ledger_entry_is_in_two_categories():
    cats = [_MUST_GUARD, set(_ENGINE_GUARDED), set(_NO_CLI_TEXT),
            set(_OFFLINE_FIXTURE_HARNESS), set(_PENDING)]
    overlap: set[str] = set()
    for i, a in enumerate(cats):
        for b in cats[i + 1:]:
            overlap |= a & b
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
    known = (_MUST_GUARD | set(_ENGINE_GUARDED) | set(_PENDING)
             | set(_NO_CLI_TEXT) | set(_OFFLINE_FIXTURE_HARNESS))
    new = sorted(found - known)
    assert not new, (
        "new `claude -p` spawn site(s) with no ledger entry: " + ", ".join(new)
        + " — spawn through ClaudeCodeEngine (which neutralises the payload "
          "itself) and add the file to _ENGINE_GUARDED, or route the prompt "
          "through the fail-closed shim operator/bridges/shared/prompt_guard.py "
          "and add it to _MUST_GUARD, or (only when it hands NO text to the "
          "CLI at all) justify it in _NO_CLI_TEXT"
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
