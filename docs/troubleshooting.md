# Corvin — Troubleshooting

A quick-reference table of the most common problems, their causes, and how to fix them.
After the table, a section on reading bridge.sh tail output is provided.

---

## Symptom → Cause → Fix

| # | Symptom | Likely cause | Fix |
|---|---|---|---|
| 1 | Bot doesn't reply at all | Token missing or whitelist empty in settings.json | Check `operator/bridges/<channel>/settings.json`. Make sure `<channel>_token` is a real value (not the placeholder `"YOUR_TOKEN_HERE"`). Add your user ID to the `whitelist` array. Restart: `bash operator/bridges/bridge.sh restart`. |
| 2 | `claude: command not found` | Claude Code CLI not on PATH | Run: `export PATH="$HOME/.local/bin:$PATH"` and add that line to `~/.bashrc` (or `~/.zshrc`). Then: `source ~/.bashrc`. Or re-run `corvin-install` — it fixes the PATH automatically. |
| 3 | `No module named 'openai'` or `openai module not found` | pip install failed due to PEP 668 (externally managed environment) | Option A: `pip install openai --break-system-packages`. Option B (recommended): `python3 -m venv ~/.venv && source ~/.venv/bin/activate && pip install openai`. Setup.sh uses option B. |
| 4 | WhatsApp QR code expired before scanning | Session timed out (30-second window) | Re-run: `bash operator/voice/scripts/whatsapp_cli.sh pair`. Scan the new QR code before it expires. |
| 5 | Discord bot online but not responding to messages | MESSAGE CONTENT INTENT not enabled | Go to [discord.com/developers/applications](https://discord.com/developers/applications) → your app → Bot → scroll to **Privileged Gateway Intents** → enable **Message Content Intent** → Save Changes. Restart the Discord daemon: `bash operator/bridges/bridge.sh restart`. |
| 6 | Reply contains `[rate limited]` or similar | OPENAI_API_KEY exhausted or missing | Check [platform.openai.com/usage](https://platform.openai.com/usage) for your quota. Ensure `OPENAI_API_KEY` is set in `~/.config/corvin-voice/service.env`. Restart the adapter after editing that file. |
| 7 | No TTS voice attachment — bot replies in text only | `ffmpeg` not found (rare — a bundled `imageio-ffmpeg` binary is used automatically when no system ffmpeg is on PATH) | Usually self-resolves via the bundled fallback. If it still fails: Linux: `sudo apt install ffmpeg`. macOS: `brew install ffmpeg`. Windows: `winget install ffmpeg`. Then restart: `bash operator/bridges/bridge.sh restart` (or `.\bridge.ps1 restart` on Windows). |
| 8 | Systemd service fails to start after moving the repo | WorkingDirectory in the unit file points to the old path | Run `bash operator/bridges/bridge.sh up` — it regenerates unit files with the current path. Then: `systemctl --user daemon-reload && systemctl --user restart corvin-voice-bridge-adapter.service`. |
| 9 | `bridge.sh status` shows all components as red / stopped | `npm install` was never completed | Run `bash operator/bridges/bridge.sh up` — this always runs `npm install` before starting daemons. |
| 10 | `voice-audit verify` fails with "chain broken at event N" | `audit.jsonl` was manually edited or truncated | Never edit `audit.jsonl` by hand. If this is a fresh install with no real data, you can delete the file and let Corvin create a new chain. If this is a production system, restore from backup. The break point is logged — events before it are still valid. |
| 11 | Forge tools not appearing inside Claude (no `mcp__forge__*` tools) | Plugin not registered with Claude Code | Run: `claude plugin install voice@corvin-voice-local`. Requires Claude Code CLI and active login. Then restart Claude Code. |
| 12 | Web console returns HTTP 401 | Owner token expired (90-day TTL) | Re-generate a token: `python -m corvin_gateway.cli token issue _default`. Paste the new `atlr_...` token at the login screen. Old tokens can be revoked via Settings → Tokens. |
| 13 | Signal bridge not connecting | `signal-cli` not installed or not on PATH | Install `signal-cli` (requires Java 17+). See `operator/bridges/signal/README.md` for detailed instructions. Verify with: `signal-cli --version`. |
| 14 | Email bridge not picking up new mail | Provider does not support IMAP IDLE (push) | Switch to polling mode. In `operator/bridges/email/settings.json`, set `"imap_idle": false` and optionally `"poll_interval_seconds": 60`. Hot-reload applies immediately — no restart needed. |
| 15 | Bot replies but no TTS audio attachment in any bridge | `OPENAI_API_KEY` not set in service.env | TTS uses OpenAI's TTS API. Set `OPENAI_API_KEY=sk-...` in `~/.config/corvin-voice/service.env` (mode 0600 required). Restart the adapter. |
| 16 | Installer fails at `npm install` with `Unsupported engine` | Node.js version is below 20 | Use nvm: `nvm install 20 && nvm use 20 && nvm alias default 20`. If nvm is not installed: [github.com/nvm-sh/nvm](https://github.com/nvm-sh/nvm). |
| 17 | Warning: `ANTHROPIC_API_KEY not set` at startup | Anthropic API key not provided | This is advisory, not an error. Claude Code's browser login (`claude login`) is sufficient for the main agent. The key is only needed for optional background helpers (summarizer, dialectic judge). You can safely ignore this warning. |
| 18 | `bridge.sh up` says "no channels configured" | All bridge tokens are still the placeholder values | Edit at least one `operator/bridges/<channel>/settings.json` with a real token. See the per-bridge credential guide in [setup.md](setup.md). |
| 19 | Telegram bot replies but ignores all commands | Your Telegram user ID is not in the whitelist | Find your user ID via `@userinfobot` in Telegram. Add it to `operator/bridges/telegram/settings.json` under `whitelist`. Hot-reload applies — no restart needed. |
| 20 | Adapter crashes with `ModuleNotFoundError: No module named 'yaml'` | Python dependencies not installed | Run: `pip install -e ".[all]"` (use the venv if you set one up: `source ~/.venv/corvin/bin/activate && pip install -e ".[all]"`). |
| 21 | WhatsApp daemon says `Error: Session not found` on startup | Session files were deleted or never created | Re-run the pairing flow: `bash operator/voice/scripts/whatsapp_cli.sh pair`. |
| 22 | Slack bot receives messages but `chat:write` permission error appears | Bot was installed before adding `chat:write` scope | In the Slack app settings, go to OAuth & Permissions, confirm `chat:write` is listed, then reinstall the app to the workspace. |
| 23 | `bridge.sh doctor` reports `engine.claude_cli: CRITICAL` | Claude Code CLI not installed or not executable | Run `claude --version` manually to confirm. Re-install if needed: `npm install -g @anthropic-ai/claude-code`. |
| 24 | Bridge adapter starts but emits `[WARNING] path_gate self_test_failed` | A protected path has become accessible due to permission change | Run `bridge.sh doctor` for details. Usually caused by a `chmod` or `chown` applied to the `~/.corvin/` directory tree. Restore permissions: `chmod 600 ~/.config/corvin-voice/secrets.json ~/.corvin/global/memory/recall.db`. |
| 25 | Web console shows bridges as "unknown" / no data | Adapter not running (console backend is the adapter) | Start the adapter: `bash operator/bridges/bridge.sh up`. The console connects to the adapter's uvicorn server on port 8765. |
| 26 | `bridge.sh console` fails with "Console not set up" | The Python venv for the web UI has not been bootstrapped | Run: `bash core/console/bootstrap.sh`. This creates `core/console/.venv` and builds the frontend. Then retry `bridge.sh console`. |
| 27 | `http://127.0.0.1:8765` shows "connection refused" | `bridge.sh console` (or the systemd webui unit) is not running | Start the console: `bash operator/bridges/bridge.sh console`. On Linux with systemd: `systemctl --user start corvin-webui.service`. |
| 28 | `setup.ps1` on Windows says "Admin required" | WSL2 installation needs elevated privileges | Accept the UAC prompt (the script re-launches itself). If it fails, right-click PowerShell → "Run as administrator" and re-run `setup.ps1`. |
| 29 | `setup.ps1` says "reboot required" then nothing happens | WSL2 kernel was not loaded; reboot needed | Reboot, open Ubuntu from the Start Menu (first-run setup: choose username/password), then re-run `powershell -ExecutionPolicy Bypass -File setup.ps1`. |
| 30 | Console chat shows "Connection error — reconnecting…" (or "Reconnecting…") after a task | The console used to run with uvicorn `--reload`; a chat turn that edited repo code under `core/console`/`core/gateway` triggered a reload that closed the chat WebSocket (code 1012). | Fixed: the systemd `corvin-webui` unit and `bridge.sh console` no longer use `--reload`. After updating, regenerate the unit (`bash operator/bridges/bridge.sh up`) and `systemctl --user restart corvin-webui`. Apply new console/gateway code with a `systemctl --user restart corvin-webui` (no auto-reload). Developers who want auto-reload: `CORVIN_CONSOLE_RELOAD=1 bash operator/bridges/bridge.sh console` (accepting that live chat WebSockets drop on every code change). |
| 31 | Console chat artifacts download instead of showing inline (PDF/audio/video) | The workdir route used to send `Content-Disposition: attachment`. | Fixed: artifacts are served `inline` so images, PDFs, audio and video render in-place in the chat; the per-artifact Download button still saves the file via the HTML `download` attribute. |

---

## Reading the logs

### Using bridge.sh tail

```bash
bash operator/bridges/bridge.sh tail
```

This streams combined output from all running processes in a single terminal, with
each line prefixed by its source.

### Log line format

```
[adapter]   INFO     Session started for chat 12345 with persona assistant
[telegram]  INFO     Message received from 123456789: "hello"
[discord]   WARNING  Rate limit hit, retrying in 5s
[whatsapp]  ERROR    Failed to send message: session expired
```

The prefix in brackets tells you which component produced the line:

| Prefix | Component |
|---|---|
| `[adapter]` | The Python adapter (main orchestrator: routing, persona selection, engine spawn, audit) |
| `[telegram]` | The Telegram Node.js daemon |
| `[discord]` | The Discord Node.js daemon |
| `[whatsapp]` | The WhatsApp Node.js daemon |
| `[slack]` | The Slack Node.js daemon |
| `[email]` | The Email daemon |
| `[forge]` | The Forge MCP server |
| `[skill-forge]` | The SkillForge MCP server |

### Log levels

| Level | Meaning |
|---|---|
| `INFO` | Normal operation. No action needed. |
| `WARNING` | Something unusual happened but the system recovered or is degraded, not down. Worth reading but not urgent. |
| `ERROR` | A specific operation failed. The system is running but something did not work (e.g. failed to send a message, failed to parse a command). |
| `CRITICAL` | A structural invariant was violated. Examples: audit chain broken, vault permissions wrong, path-gate self-test failed. Requires immediate attention. |

### What a healthy startup looks like

```
[adapter]   INFO     Corvin adapter starting (version 0.9.x)
[adapter]   INFO     Self-test: all checks passed
[adapter]   INFO     Audit chain: 157 events, chain verified
[adapter]   INFO     Plugin: forge MCP server loaded
[adapter]   INFO     Plugin: skill-forge MCP server loaded
[adapter]   INFO     Uvicorn listening on 127.0.0.1:8765
[telegram]  INFO     Telegram daemon started
[telegram]  INFO     Bot @MyCorvinBot connected (username: mycorvinbot)
[discord]   INFO     Discord daemon started
[discord]   INFO     Logged in as MyBot#1234
[adapter]   INFO     Bridge adapter ready
```

### Common error patterns

**Token invalid:**
```
[telegram]  ERROR    Telegram API returned 401 Unauthorized — token is invalid or revoked
```
Fix: regenerate the token at @BotFather and update `settings.json`.

**Plugin not registered:**
```
[adapter]   WARNING  Forge MCP server not found — forge tools unavailable
```
Fix: run `claude plugin install voice@corvin-voice-local`.

**Claude Code not found:**
```
[adapter]   CRITICAL engine.claude_cli check failed: claude not found on PATH
```
Fix: see row 2 in the symptom table above.

**Audit chain issue:**
```
[adapter]   CRITICAL Audit chain verification failed at event 42 — hash mismatch
```
Fix: see row 10 in the symptom table above.

**ffmpeg missing (voice notes send as text):**
```
[adapter]   WARNING  ffmpeg not found — TTS output will be sent as text fallback
```
This should be rare: `_resolve_ffmpeg_bin()` falls back to the bundled
`imageio-ffmpeg` static binary automatically when no system ffmpeg is on
PATH, so edge-tts/Piper work out of the box on every platform (including a
fresh Windows install, where the installer intentionally skips installing
system ffmpeg). If you still see this warning, see row 7 above.

### Filtering logs to a specific component

If you only want to see adapter output:

```bash
bash operator/bridges/bridge.sh tail 2>&1 | grep '^\[adapter\]'
```

For real-time error monitoring:

```bash
bash operator/bridges/bridge.sh tail 2>&1 | grep -E 'ERROR|CRITICAL'
```
