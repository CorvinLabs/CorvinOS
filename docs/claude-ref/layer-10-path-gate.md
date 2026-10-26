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
# in Claude Code (the local marketplace is registered as claudeos-local):
/plugin install voice@claudeos-local
```

or register the hook directly in `~/.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash|WebFetch",
  "hooks": [{"type": "command", "timeout": 3,
             "command": "python3 /ABSOLUTE/PATH/CorvinOS/corvin_operator/voice/hooks/path_gate.py"}]}]}}
```

`check()` is fail-CLOSED (F-A8): any internal exception is a deny (exit 2),
never a non-blocking hook error.

## Must NOT do

- Don't fail-open the gate (an unparseable protected-path command must deny —
  and so must an exception inside `check()`).
- Don't add an env kill-switch or "path-gate off" mode.
- Don't add a second entry to `_PINNED_PYTHON_TOOLS` without pinning every sibling module the tool imports, and don't
  make the exception match on a basename, a prefix, a pattern or a normalised path.
- Don't narrow `_touches_corvin_tree` for *direct* targets (ancestor protection
  on `rm -rf ~` is load-bearing); the at-or-under narrowing applies ONLY to the
  chdir context.
