# strace wrapper for the Claude CLI (diagnostics, Linux only)

`claude_strace_wrapper.py` is a diagnostic wrapper that can be put in front of the CLI through
`CORVIN_CLAUDE_BIN`. It records who signals the CLI with `kill`/`tgkill`/`tkill` and when it ends
with `exit_group`. It is **not** activated: the live console and the systemd units stay unchanged
until someone runs the activation command below deliberately.

The platform is Linux. On macOS and Windows the wrapper runs the real CLI without a trace and
writes a warning to the wrapper log (fail-open).

## How it works

- The CLI is a child of `strace -f`. Trace filter: `trace=kill,tgkill,tkill,exit_group,execve`
  plus `signal=all`, with timestamps (`-tt`).
- `-s 0` hides every string argument. `execve` then shows `[...]`, and no prompt or secret lands in
  the log. The target path is in the wrapper log.
- stdin, stdout and stderr are inherited, not buffered through pipes. The CLI output is
  byte-identical to a direct call.
- Signals sent to the wrapper are received with `sigtimedwait`, so the sender (`si_pid`) is known.
  The wrapper records it and forwards the signal to the CLI child. Tty signals
  (`si_code == SI_KERNEL`, e.g. Ctrl-C) are not delivered twice.
- The CLI's exit code and signal death are passed through.
- Fail-open: when strace is missing or ptrace refuses (probe `strace -o /dev/null -- true`), the
  wrapper writes `WARN strace inactive (...)` to the wrapper log and replaces itself with the real
  CLI via `exec`.

## Logs

Directory: `$CORVIN_HOME/diagnostics/strace/` (fallback `~/.corvin`), mode `0700`.
Two files per run, mode `0600`:

- `<stamp>-<pid>.strace.log`: syscalls, pids, signals, timestamps
- `<stamp>-<pid>.wrapper.log`: start (target path, `ptrace_scope`), signal forwarding with the
  sender pid, exit, warnings

The logs live outside the worker's cwd and outside `session_ledger`. They contain no prompt
content, no secrets and no arguments. Nothing of this reaches the CLI's stdout or stderr.

## Environment variables

| Variable | Meaning | Default |
|---|---|---|
| `CORVIN_CLAUDE_BIN` | pin to the wrapper (absolute path) | — |
| `CORVIN_CLAUDE_REAL_BIN` | the real CLI behind the wrapper | first `claude` on PATH that is not the wrapper |
| `CORVIN_CLAUDE_STRACE_BIN` | strace binary (test hook) | `strace` on PATH |
| `CORVIN_HOME` | runtime root for the logs | `~/.corvin` |

## Tests

```bash
env -u FORGE_ROOT CORVIN_HOME=<sandbox>/corvin_home HOME=<sandbox>/home \
    XDG_CONFIG_HOME=<sandbox>/xdg CORVIN_AUDIT_ANCHOR_KEY=<absolute path> \
    .venv/bin/python -m pytest tests/diagnostics/test_claude_strace_wrapper_e2e.py -v
```

The tests cross real process boundaries: a fake CLI (bash) with an adjustable delay
(`FAKE_DELAY`, default 3 s), the wrapper via `CORVIN_CLAUDE_BIN`, compared with a direct run.

## Activation (not executed)

Only on the operator's instruction, because it changes live behaviour:

```bash
mkdir -p ~/.config/systemd/user/corvin-webui.service.d
cat > ~/.config/systemd/user/corvin-webui.service.d/strace.conf <<'EOC'
[Service]
Environment=CORVIN_CLAUDE_BIN=/home/shumway/projects/CorvinOS/scripts/diagnostics/claude_strace_wrapper.py
Environment=CORVIN_CLAUDE_REAL_BIN=/home/shumway/.local/bin/claude
EOC
systemctl --user daemon-reload && systemctl --user restart corvin-webui.service
```

Check `REAL_BIN` with `command -v claude` first. The path above is an example.

## Rollback (not executed)

```bash
rm ~/.config/systemd/user/corvin-webui.service.d/strace.conf
systemctl --user daemon-reload && systemctl --user restart corvin-webui.service
```

Logs under `$CORVIN_HOME/diagnostics/strace/` stay until they are deleted.
