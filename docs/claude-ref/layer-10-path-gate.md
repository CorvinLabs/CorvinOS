# Layer 10 — Path-Gate (FS-write protection, fail-closed)

**Source:** `corvin_operator/voice/hooks/path_gate.py` · **Tests:** `corvin_operator/voice/hooks/test_path_gate.py`

The path-gate is a `PreToolUse` hook that inspects `Write`/`Edit`/`NotebookEdit`
and `Bash` tool calls and **denies writes** that would tamper with the corvin
runtime tree — most importantly the hash-chained GDPR `audit.jsonl`, the secret
vault, the license/consent state, and the Forge/SkillForge trees. It is
**fail-closed**: an unparseable or ambiguous command that references a protected
path is denied, not allowed.

## Scope — what it gates

- **It is a WRITE boundary.** It does NOT gate *reads*: `cat`/`less`/`grep` of a
  protected file extract no write target and are allowed. Read-side confinement
  of the vault belongs to the sandbox / tool-allowlist layer, not here.
- **Structured tools** (`Write`/`Edit`/`NotebookEdit`): the target path is
  matched directly against `is_protected_path()`.
- **Bash**: the command string is split into segments and each segment is parsed
  for write targets. Covered write vectors include redirects (`>`, `>>`, `cmd>f`),
  `tee`, `dd of=`, `sed -i`, scripted editors (`ex`/`ed`/`vi -es`), `truncate`,
  `ln`, `rm`/`rmdir`/`unlink`/`shred`, `chmod`/`chown`/`chgrp`/`chattr`,
  `mv`/`cp`/`install`/`rsync`, `find` mutating actions, `xargs`, and Python
  `open(..., 'w')` one-liners.
- **Learning state** (review 2026-11-05 DR-3): everything under
  `<corvin_home>/tenants/<tid>/learning/` (routing ledger, rollback judge
  state/pending, learned thresholds, event store) and
  `<corvin_home>/tenants/<tid>/skills/` (SkillAdapter learned configs such as
  `os_delegation_router_config.json`) is protected — a worker deleting
  `rollback_state.json` cleared a tripped L5 rollback without the audited
  operator reset, and appended ledger lines could pass the ADR-2092 readiness
  gate. The rule is anchored at the tenant root, so a project folder named
  `learning` inside a session workdir stays writable. Because it is anchored,
  the create/overwrite commands (`patch`, `xxd`, `split`, `touch`, `mkdir`, …),
  which are otherwise checked against the ledger stores only, check it too —
  `patch`/`xxd -r` rewrote the router config before (refutation 2026-11-05).
  Three learning-state files directly under `<corvin_home>/tenants/<tid>/global/`
  are covered by the same rule (LRN-10): `ce_stage_grades.json` (forged
  `operator` grades made a non-builtin CEL stage default-eligible),
  `usage_epoch.json` (resetting it reset the ADR-2259 Free-tier savings meter)
  and `model_confidence_stats.json` (L08), each with its `<name>.lock` and
  `.<name>.<x>.tmp` siblings; other files in `global/` keep their own rules.
  Not covered (gate-wide, every protected tree): `git -C <dir> init/clean`,
  a symlinked `CORVIN_HOME` or tenant directory (the target is resolved, the
  home is not), and case variants on a case-insensitive file system.

## Strict stores, gate sources and the hook's own registration (review 2026-11-19)

Three groups joined the protected set in the review of 2026-11-19 (rounds 1 and 2).
Tests: `tests/security/test_path_gate_review_20261119.py` (the real hook as a
subprocess, both directions).

**Strict stores** (`_is_strict_store_path`): the session ledger class and the audit
chain with its out-of-tree anchors — every `audit.jsonl` (and its rotation / lock
siblings) under the corvin home, the anchor key, the `audit_mac_active` sentinel and,
beside the key, `chain_ids/`, `chain_tails/`, `mac_active_chains/`, `chain_witness/`,
`chain_ids_retired/` and `chain_witness_retired/`. A command that NAMES one of them
must be a proven read, otherwise it fails closed. The key directory is wherever
`CORVIN_AUDIT_ANCHOR_KEY` points, not only `~/.config/corvin-voice`.

**What counts as a read (round 2, R2-SEC-5).** Round 1 decided "read" by a list of
command names, so `awk '{print $1}' audit.jsonl`, `sort`, `xxd | head`, `perl -ne`,
`tar czf /tmp/c.tgz audit.jsonl`, `gzip -c`, `python3 -m json.tool`, a `for` loop over
the file and `cp <gate source> /tmp/x` were all refused. `_is_reader_invocation()`
decides per INVOCATION, from its arguments:

| Command | Read when | Still a write (denied) |
|---|---|---|
| `cat`/`head`/`tail`/`grep`/`jq`/`wc`/`sha256sum`/`diff`/`tac`/… | always | a redirect into a protected path |
| `awk` | program text has no `print >`, `print \|`, `system(` | `-i inplace`, `-f <file>` (program not inspected) |
| `sort` | no output option, or `-o` / `--output` (spelled out) to a path outside every protected tree | `sort -o f f`, `sort -uo f`, `--out=f` / `--outp f` (abbreviations), `--compress-program`, `-T <dir>` |
| `uniq`, `xxd` | one file argument, or `INPUT OUTPUT` with OUTPUT outside every protected tree; no `xxd -r` | a second positional inside a protected tree |
| `sed` | every script (`-e` / `--expression` values, else the first positional) parses as a pure print / delete / transform program | `sed -i`, `--in-place` and its abbreviations (`--in-pl`, `--i`), `-f <file>`, any `w` / `W` / `e` command (`$w f`, `1,$w f`, `0~1w f`, `1e cmd`), `s///w f`, `s///e` |
| `perl` / `ruby` | inline `-e`/`-ne` (also glued `-e'…'`, every `-e` checked), text has no `open`/`unlink`/`>`/`system`/… outside a plain regex literal | `-i`, a script file, `-M`, any long option |
| `gzip` & co. | `-c` / `-l` / `-t` | in-place (also `-k`: it writes a sibling into the store), `--rm`, `zstd -o`, `lz4 IN OUT` |
| `tar` | create/list, archive outside every protected tree, only vetted options | extract, append, `--remove-files` / `--checkpoint-action` / `--to-command` and their abbreviations (`--rem`, `--checkpoint-ac`), `g` / `I` / `F` in a short cluster (`tar cfg`), archive inside the tree |
| `zip` | archive outside every protected tree, no `-m` | `-m` |
| `cp` | source is an audit chain FILE or a gate source FILE, destination outside every protected tree | a tree, a glob, the anchor key / marker trees / vault as source; any protected destination |
| `python3 -m json.tool <file>` | one positional | a second positional (the output file); every other `python …` |
| `for f in <store path>; do …` | every other segment is itself a proven read | `…; do rm "$f"` |

**Options are compared as prefixes, programs are parsed (round 3, R3-SEC-1 / -9).**
Round 2 recognised exact spellings only, and a "reader" skips the strict-store check, so
every GNU abbreviation was a bypass: `sed --in-pl`, `sed --i`, `sort --out=`,
`tar … --rem`. Three rules close the class instead of the instances:

1. *Vetted long options only.* For `sed`, `sort`, `uniq`, `awk`, `tar`, `cp`, `zip` and the
   compressors, a long option that is not spelled exactly like one on the per-command
   list of harmless options (`_SED_READ_LONG`, `_SORT_READ_LONG`, `_TAR_READ_LONG`, …)
   makes the invocation a non-read. Every abbreviation and every option the gate has not
   looked at lands there. The write-TARGET extraction (`_inplace_write_candidates`,
   the output-flag scan, `_target_dir_flag`) matches the dangerous options as PREFIXES
   (`_long_opt_is`), so a protected file that is not a strict store is covered too.
2. *sed scripts are parsed* (`_sed_script_reads_only`), not grepped. Addresses
   (`N`, `$`, `/re/`, `a,b`, `first~step`, `!`) are consumed, then the command letter
   decides: `p P d D q Q n N l h H g G x = z F`, `s` / `y` without the `w` / `e` flags,
   labels, `a i c` text and `r` are reads; `w`, `W`, `e` and anything unknown are not.
   Text inside `/…/` is a pattern, so `sed -n '/write w something/p' CHAIN` is a read.
   For perl / ruby a plain regex literal in operand position (`if /open/`) is removed
   before the write scan; a `/` after an operand is division and is kept
   (`1 / unlink / 1` runs `unlink`).
3. *A non-read is searched for store paths inside its tokens* (`_embedded_path_pieces`):
   `sed -n 'w <chain>'`, `sed '1e unlink <ledger>'`, an awk string,
   `--checkpoint-action='exec=rm <ledger>'`. Absolute pieces for every command but
   `git` / `gh` (free text; they have their own write check), cwd- and `cd`-relative
   pieces for the script-carrying commands (`sed`, `awk`, `perl`, `ruby`).

`tree -o`, `rg --pre`, `less -o` and `zstdcat -o` are no longer unconditional reads.
Known limits, stated: the segment splitter is not quote-aware, so a script containing
`;`, `|` or a newline inside quotes is split and then refused when it names a store
(`sed -n '/a/{p;q}' <chain>`, `awk '/a|b/' <chain>`) - conservative, use `-e` twice or
`grep -E`. A bracket expression holding the delimiter (`s/[/]x/y/`) is also refused.

Shell keywords are peeled first (`do` / `then` / `else` / `if` / `while` / `until` /
`!`): the command a segment RUNS is what is classified. Before 2026-11-19 the segment
`do rm -rf ~/.corvin` was keyed on `do`, extracted no target and was allowed — as was
`if true; then rm <chain>; fi`. Consequence, stated: `mv` / `cp` inside a loop are now
judged like `mv` / `cp` outside one (including the pre-existing rule that denies them
for any argument inside the runtime tree, which covers a session workdir).

After a `cd` into a read-only store only reads may follow, and a destination must be
absolute (a relative name would land in the store). Anything the function cannot prove
stays a non-read: conservative on purpose, `awk '$3 > 5'` is refused because `>` in a
`print` position is a redirect. `python -c` / `node -e` naming a protected path stay
fail-closed (an inline interpreter is opaque). The deny text says so
(`_fail_closed_message`): it names the reason and the read forms that work, instead of
the former "Bash command unparseable … use the forge MCP tools", which was wrong for a
perfectly parseable `awk`.

**Gate sources** (`_is_gate_source_path`): `corvin_operator/voice/hooks/**` (this gate,
`with_python.sh`, `hooks.json`, the sibling gates), `forge/forge/security_events.py`,
the three `tenant_audit_chain` mirrors (`forge/forge/paths.py`,
`bridges/shared/paths.py`, `core/paths/{tenant,__init__}.py`) the boot tripwire
`core/compliance/corvin_compliance_reports/tripwire.py`, and (round 3, R3-SEC-10) the
pre-spawn gate dispatcher `bridges/shared/spawn_gates.py` and the house-rules policy
`corvin_operator/policy/house_rules.yaml` — in the checkout the hook
runs from AND the checkout that holds the live runtime root.

*Editing a gate source — the worktree workflow.* These files are no longer editable
in place from a gated session; that is the point (a worker that can rewrite the audit
writer does not need to touch the chain). A legitimate change goes through git:

```bash
git -C <checkout> worktree add /tmp/gate-fix -b gate-fix   # a second checkout
# edit /tmp/gate-fix/corvin_operator/forge/forge/security_events.py there
# (the hook of the MAIN checkout protects only its own roots), run the tests with
# PYTHONPATH pointing at the worktree first, commit in the worktree,
git -C <checkout> merge gate-fix                            # the operator merges
```

Reading stays free in the main checkout (`cat`, `grep`, `git diff`, `cp <file>
/tmp/copy.py`). The hook protects the checkout it runs from and the one holding the
live runtime root; a session whose hook runs from the worktree itself (cwd inside it,
or a plugin cache built from it) cannot edit the worktree's copies either — start
that session from outside the worktree. `git checkout/restore/rm/mv -- <gate source>`
in a protected checkout is a write and is denied; a merge is not path-gated (it lands
COMMITTED content the operator reviewed).

**The hook's own registration (round 2, R2-SEC-3).** Protected against Write/Edit and
every Bash write form:

- `settings*.json` in `~/.claude` (or `CLAUDE_CONFIG_DIR`) AND in any `.claude/`
  directory at, below or above the worker's cwd and below the checkout root. Project
  and local settings are merged over the user file, so `{"disableAllHooks": true}` in
  `<cwd>/.claude/settings.local.json` removed the gate while only the user file was
  protected. Consequence, stated: in a gated session Claude Code's own "edit the
  project settings" flows (permission allowlists, the `update-config` skill) are
  denied for those files — the operator edits them in an editor.
- `~/.claude/plugins/**` — the plugin cache holds the hook copy an installed `voice`
  plugin actually runs, plus `installed_plugins.json` / `known_marketplaces.json`.
- **Guard roots** (`_guard_roots`, equal-or-ancestor only): `rm -rf` / `mv` / `chmod`
  / mutating `find` of `~/.claude`, a project `.claude/`, the anchor-key directory or
  any ancestor of one of them fails closed although no protected leaf is named. A
  path UNDER a guard root is decided by `is_protected_path` alone, so a session's own
  files below `~/.claude/projects/` or next to a non-default key stay writable.

## Protected-tree semantics (`_touches_corvin_tree`)

A **destructive** operation (recursive/glob/root `rm`/`mv`/`chmod`/... , a
mutating `find`, or archive extraction) fails closed when its target **is**, is
**under**, or is an **ancestor of** the corvin home. This closes the class where
`is_protected_path()` (which only flags specific leaf names / subdir tokens)
returned `False` for the home root, an intermediate dir, or a glob pattern:

- `rm -rf ~/.corvin`, `rm -rf ~/.corvin/tenants`, `rm ~/.corvin/*.jsonl` (glob),
  `chmod -R ~/.corvin/global`, `mv ~/.corvin/tenants /tmp/x` (source checked, not
  just dest), `find ~ -name '*.jsonl' -delete`, `find ~ … | xargs rm`.

## Hardening classes (2026-07 adversarial review, rounds 3–5)

1. **Exec-wrapper peeling (`_strip_cmd_wrappers`).** `env`, `command`, `busybox`,
   `nice`, `timeout`, `sudo`, `doas`, `setsid`, `nohup`, `stdbuf`, `ionice`,
   `time`, `unbuffer` are unwrapped (with their own arg grammar) so the gate keys
   on the REAL command, not the wrapper. Closes `env rm -rf ~/.corvin`,
   `busybox truncate -s0 …/audit.jsonl`, etc.
2. **Archive extraction (`_ARCHIVE_CMDS`).** `tar`/`unzip`/`cpio`/`7z` extraction
   whose destination (`-C`/`-d`/`-D`/`-o`) touches the tree fails closed
   (overwrite of `audit.jsonl`). Reading the tree *into* an archive is a read and
   stays allowed.
3. **chdir-into-tree backstop (`_seg_chdirs_into_tree`).** The hook runs in its
   own subprocess and cannot observe a `cd`; `cd ~/.corvin && rm -rf tenants`
   resolved the relative target against the hook's cwd. If any segment chdir's
   **into (at/under)** the tree — via `cd`, `pushd`, or a wrapper chdir flag
   (`env -C/--chdir`, `sudo -D/--chdir`, `doas -C`) — and any segment runs a
   destructive command, the whole command fails closed. The chdir test uses
   *at-or-under* (not ancestor) semantics, so ordinary `cd ~ && rm -rf
   Downloads/x` or `cd <repo> && rm -rf dist` (which descend AWAY from the tree)
   are NOT over-blocked.
4. **MCP-server self-start (`_MCP_SERVER_SELF_START_RE`, refutation 2026-11-05 RL-5).**
   A Bash command in which an interpreter (`python*`, `pypy*`, `py`, `uv`, `uvx`) is
   followed, in the same segment, by `-m skill_forge.mcp_server`,
   `…skill_forge/mcp_server.py`, `forge.py mcp` or `-m forge mcp` is denied (quotes and
   backslashes removed first): such a server takes its caller persona from the env the
   WORKER gives it (`CORVIN_CALLER_PERSONA=skill-forge python3 -m skill_forge.mcp_server`).
   Reading or testing the file (`grep`, `cat`, `pytest …/skill-forge/tests`) stays
   allowed. A naive-start guard only: the skill-forge server itself honours the
   operator's wildcard solely for a resolver-recorded launch
   (`skill_forge/launch_record.py`), and both are ATTRIBUTION, not isolation — a worker
   that writes and runs its own launcher is the residual below. Proof:
   `corvin_operator/voice/hooks/test_path_gate_mcp_self_start.py` (the real hook
   process) and `test_path_gate.py` § 3c.

## Known residual (by design)

Arbitrary-code interpreters (`python -c "os.remove(...)"`, `perl -e 'unlink ...'`,
`node -e`, write-a-script-then-run) are **out of a shell-pattern gate's reach** —
no regex can model them. The compensating controls are the **sandbox / tool
boundary** (the real perimeter), the **hash-chained audit log** (tamper-evident:
`voice-audit verify` fails non-zero on any break), and **audit-at-rest
encryption** (L37). The path-gate blocks the common and moderate-effort shell
destructive forms; the interpreter class is the sandbox's responsibility.

## Pinned trusted tool: the one exception to the Python AST gate

The AST gate (Gate 1, `_python_ast_check`) refuses any `python3 <file>.py` whose file imports `subprocess`, `socket`,
`importlib`, ... . Corvin-Knowledge's CLI `scripts/kb.py` imports `subprocess` for git, so every agent session was
locked out of `kb new` / `kb task` / `kb check`. `_PINNED_PYTHON_TOOLS` in `path_gate.py` is the **single** exception:

- the script must appear in the command as **exactly** `<projects>/Corvin-Knowledge/scripts/kb.py` (plain string
  comparison - no normalisation, no wildcard, no relative form, no `..`, no quoting, no symlink); the root is derived
  from the hook's own location (CorvinOS' sibling repo), so on an install without that sibling the table matches nothing;
- the whole bundle - `kb.py`, `kb_lib.py`, `kb_model.py` (what `kb.py` imports from its own directory, which Python
  puts first on `sys.path`) - must be regular files whose SHA-256, computed **fresh from the bytes on every call**,
  equals the value pinned in the source. Missing file, symlink, unreadable file or mismatch = **deny** (fail closed);
- refused even with a matching hash: `--reviewer-cmd` / `--drafter-cmd` (kb runs those commands by design) and
  `PYTHON*=`, `LD_*=`, `BASH_ENV=`, `sitecustomize` in the command (they re-route interpreter start-up). Exception:
  `PYTHONIOENCODING=`, `PYTHONUNBUFFERED=`, `PYTHONUTF8=`, `PYTHONDONTWRITEBYTECODE=` only change encoding / buffering /
  bytecode caching and load no code, so they pass (`_PIN_HARMLESS_PYTHON_ENV`);
- a command that names `kb.py` in any other form (`python3 scripts/kb.py` after a `cd`, `./scripts/kb.py`, `~`, `$HOME`)
  is still not exempted, but its denial now spells out the exact absolute command (`_pin_hint`) instead of the generic
  "use forge_exec" text, which sent sessions looking for a sandbox instead of fixing the path;
- nothing else is relaxed: write-target detection, protected paths and every other Python snippet of the same command
  are still checked. Audit: `code.exec_attempt` with `outcome=allowed_pinned`, or `code.exec_blocked` with the reason.

The pin lives in the hook's source on purpose - changing it is a reviewed commit to the hook, not an edit of a data
file next to the tool. **Re-pin** after a reviewed change of the tool: `sha256sum kb.py kb_lib.py kb_model.py` in
`scripts/`, update the three values in the same commit. Stated residual risk: the hash is checked when the hook runs,
not when python opens the file (an attacker who can already write that directory could swap a file in between).
Tests: `corvin_operator/voice/hooks/test_path_gate_pinned_tools.py` (31, each "allowed" case has an unpinned control;
five deliberate breakages of the mechanism were each caught).

Open, separate from the exception: a **relative** script path (or one after `cd`) is resolved against the hook's
working directory, and an unfound file is skipped ("do not block on absence"), so the AST gate can be sidestepped by
choosing a spelling the hook cannot resolve. Not changed here (it would also tighten every legitimate workflow).

## Enabling the hook (operator action — the platform cannot do this for you)

`corvin_operator/voice/hooks/hooks.json` registers `path_gate.py` on
`PreToolUse` (`Write|Edit|MultiEdit|NotebookEdit|Bash|WebFetch`) — but only when
Claude Code loads the `voice` plugin. The boot tripwire
`l10_hook_registered` (reporting-only, F-A8) checks `~/.claude/settings.json`,
`<project>/.claude/settings.json` and `settings.local.json` and logs a
CRITICAL compliance finding on every boot while the hook is missing. Enable it
either way:

```text
# in Claude Code (the repo's marketplace is corvin-voice-local, .claude-plugin/marketplace.json):
/plugin marketplace add <CorvinOS checkout>
/plugin install voice@corvin-voice-local
```

or register the hook directly in `~/.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash|WebFetch",
  "hooks": [{"type": "command", "timeout": 3,
             "command": "python3 /ABSOLUTE/PATH/CorvinOS/corvin_operator/voice/hooks/path_gate.py"}]}]}}
```

`check()` is fail-CLOSED (F-A8): any internal exception is a deny (exit 2),
never a non-blocking hook error.

**No interpreter is a deny too (review 2026-11-05, SEC-2).** `hooks.json` runs every
hook through `hooks/with_python.sh` (python3 → `CORVIN_PYTHON` → the corvinos tool
venv → `python`). The three PreToolUse gates — `path_gate.py`,
`auth_elevation_gate.py`, `web_trust_gate.py` — are invoked as
`with_python.sh --fail-closed <gate>.py`: when no Python 3 resolves, the launcher
exits **2** (the only blocking exit code) and names the gate as denied. Before, it
exited 1 for every hook — a non-blocking error, so on a host without an interpreter
every gated tool call ran ungated. Under `--fail-closed` the launcher does not `exec`
the interpreter: it runs the gate and passes through only 0 (allow) and 2 (deny); any
other status - a Python that resolves but cannot start (stale `PYTHONHOME`, a broken
venv stdlib, a stub that exits 1), a traceback, 126/127, a signal - becomes 2 (RS-2).
The best-effort hooks (`artifact_register`,
`notification_relay`, `ldd_auto_optin`) keep exit 1. A hand-registered hook in
`~/.claude/settings.json` that calls the gate through the launcher must pass
`--fail-closed` as well. Test: `tests/security/test_review_20261105_sec_e2e.py`
(`test_sec2_*`, `test_rs2_*`, the manifest's own command line through `/bin/sh -c`).

## Must NOT do

- Don't fail-open the gate (an unparseable protected-path command must deny —
  and so must an exception inside `check()`, and so must a missing interpreter:
  never drop `--fail-closed` from a PreToolUse gate's `hooks.json` command).
- Don't add an env kill-switch or "path-gate off" mode.
- Don't put a command NAME on the read list because one invocation of it reads:
  extend `_is_reader_invocation()` with the argument test that proves it, and add
  the vector in both directions (reader allowed, in-place writer denied).
- Don't edit a gate source in place "just this once" by weakening
  `_PROTECTED_REPO_FILES`; use the worktree workflow above.
- Don't add a second entry to `_PINNED_PYTHON_TOOLS` without pinning every sibling module the tool imports, and don't
  make the exception match on a basename, a prefix, a pattern or a normalised path.
- Don't narrow `_touches_corvin_tree` for *direct* targets (ancestor protection
  on `rm -rf ~` is load-bearing); the at-or-under narrowing applies ONLY to the
  chdir context.
