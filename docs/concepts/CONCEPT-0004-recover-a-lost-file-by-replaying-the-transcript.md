---
id: CONCEPT-0004
title: Recover a lost working file by replaying the session transcript
status: ACTIVE
created: 2026-09-18
skills: []
relates_to: [CONCEPT-0003]
paths:
  - install.ps1
docs:
  - docs/windows-installation-errors.md
---

# CONCEPT-0004 — Recover a lost working file by replaying the session transcript

**Note on location:** this belongs in `Corvin-ADR/concepts/`. That repo does not
exist on the Windows box this was written on, so it lands in the documented
`CorvinOS/docs/concepts/` fallback. Move it when the two are on one machine.

## The situation this is for

Hours of uncommitted work in ONE file disappears. Not "was edited wrongly" —
gone from disk. On a managed Windows image the realistic cause is an antivirus
verdict: AMSI decided the script was malicious and the AV deleted the file.
Nothing in `git status` helps (` D install.ps1`), `git stash` never ran, and
`HEAD:<file>` is a much older, structurally different version, so replaying
edits onto it fails on every hunk.

Measured instance (2026-09-18): `install.ps1` grew from a 777-line rewrite to
~1650 lines across two sessions. Building a `cmd /c "<exe> ... > out 2> err"`
command line — a dropper shape — got the whole script blocked with "This script
contains malicious content and has been blocked by your antivirus software",
and the file was deleted. Defender cmdlets showed no detection, the recycle bin
was empty, PS 4104 script-block logs had nothing, and quarantine restore needs
admin. Every conventional recovery route was closed.

## The method

The agent's own transcript is a write-ahead log of the file. Every `Write`
carries the FULL content; every `Edit` carries `old_string` + `new_string`.
Replay them and the file comes back.

Implemented as `scripts/recover_file_from_transcript.js`:

```bash
node scripts/recover_file_from_transcript.js --list install.ps1   # what is recoverable
node scripts/recover_file_from_transcript.js install.ps1 -o /tmp/out.ps1
```

The steps below are what that script does, and what to do by hand when it is
not available (e.g. recovering the script itself).

1. **Find every operation on that path, across ALL sessions.** Scan
   `~/.claude/projects/<slug>/*.jsonl` for `tool_use` blocks whose
   `input.file_path` ends in the filename, and print `timestamp | kind | size`.
   Scanning only the current session is the mistake that makes this look
   impossible: the base `Write` is usually in an EARLIER session.
2. **Sort by timestamp and start from the LAST `Write`.** That is the only
   trustworthy base. `HEAD` is not a base unless the file was never rewritten.
3. **Apply each later `Edit`** as a literal string replacement, honouring
   `replace_all`, and **report every hunk that does not match** instead of
   skipping silently. Partial success is fine — a single failed hunk is a
   one-line fix; a silent one is a bug you ship.
4. **Verify against the language, not against your memory of the file.** Byte
   scan for the encoding contract, parse the file (`Parser::ParseFile` for
   PowerShell, `python -m py_compile`, `tsc -b`), and check any internal
   invariant the file states about itself — here `$script:TotalSteps` had to
   equal the number of `Write-Step` calls, and the ONE failed hunk was exactly
   that counter.
5. **Commit before anything else.** The cause that deleted it once is still
   installed.
6. **Re-prove the behaviour end to end.** The rebuilt file is a reconstruction,
   not the file. Run it for real and look for the same success lines.

Outcome of the measured instance: 36 of 37 hunks applied, 1650 lines, ASCII
clean, 0 parse errors, both installer test suites green, and a fresh-home run
reached `Console is serving`, `Local login works (HTTP 302 ...)`, `Opened
http://127.0.0.1:8791/console/` — i.e. full recovery in under an hour.

## Why it keeps paying off

- It is **not specific to antivirus**. The same replay recovers from an errant
  `git checkout --`, a failed disk write, a bad merge over uncommitted work, or
  a `Write` that truncated a file.
- It turns "how much did I lose?" into a **bounded, inspectable list** in one
  command, before any decision about whether to re-author from scratch.
- Re-authoring from memory looks similar in cost and is not: the replay
  restores comments explaining *why* each guard exists. Those comments are the
  expensive part — they encode measurements (which PS 5.1 quirk, which AMSI
  verdict, which orphaned PID) that cannot be re-derived by reading the code.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Re-author from `HEAD` + a summary of the changes | Works, costs hours, and silently drops the reasoning comments. Correct only when no `Write` exists in any transcript. |
| Restore from AV quarantine | `%ProgramData%\Microsoft\Windows Defender\Quarantine` needs admin; on a managed box the operator does not have it. Also assumes the verdict came from Defender. |
| Recycle bin / Previous Versions / OneDrive | An AV deletion does not go to the recycle bin; VSS needs admin; the repo was not in a synced folder. Checking all three costs two minutes — do it, but do not plan on it. |
| `git fsck --lost-found` / scan all blobs | Only finds content that was ever staged. Uncommitted, never-added work is not in the object store. Worth one command; do not wait on the full-object scan. |
| PowerShell script-block logging (event 4104) | Would contain the source if enabled; it is not, by default. |

## When NOT to use this

- **The file was committed.** Use git. Obviously.
- **The content was produced by a tool, not by edits** (a build artifact, a
  generated bundle, a download). Re-run the generator.
- **The file was never written through the agent's file tools** — e.g. created
  by a shell heredoc. The transcript then has the command, not the content;
  re-running the command is the recovery.
- **The deletion cause is still active and unidentified.** Recover, but do not
  recover *the same construct*: find out what tripped the verdict first, or the
  file will be deleted again. In the measured case the offending launch
  mechanism was replaced and the reason left in the source as a comment, so the
  next reader does not "fix" the cosmetic quirk by reintroducing it.

## Operator Notes

<!-- Append-only, human-authored. AI amendments never edit or remove anything
     under this heading. -->
