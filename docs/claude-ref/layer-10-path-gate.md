# Layer 10 — Path-Gate (FS-write protection, fail-closed)

**Source:** `operator/voice/hooks/path_gate.py` · **Tests:** `operator/voice/hooks/test_path_gate.py`

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

## Must NOT do

- Don't fail-open the gate (an unparseable protected-path command must deny).
- Don't add an env kill-switch or "path-gate off" mode.
- Don't narrow `_touches_corvin_tree` for *direct* targets (ancestor protection
  on `rm -rf ~` is load-bearing); the at-or-under narrowing applies ONLY to the
  chdir context.
