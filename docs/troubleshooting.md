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
| 32 | `corvin-webui.service` stuck in `failed` state; `journalctl --user -u corvin-webui` shows `[Errno 98] address already in use` on port 8765 | A restart raced the previous instance's socket release: the old `RestartSec=5` retried faster than the port could free up, and 5 rapid failures inside `StartLimitIntervalSec=60` tripped systemd's "start request repeated too quickly" — a **permanent** failed state with no further auto-retry. | Fixed (2026-07-12): the unit now actively waits (up to 15s) for port 8765 to free before binding, and the retry budget is wider (`StartLimitBurst=8`/`120s`) with exponential backoff. Regenerate the unit after updating: `bash operator/bridges/bridge.sh up` (or manually re-render `core/gateway/systemd/corvin-webui.service` and `systemctl --user daemon-reload`). To recover a currently-stuck instance right now: `systemctl --user reset-failed corvin-webui.service && systemctl --user start corvin-webui.service`. |
| 33 | Voice reply (esp. a fresh-install greeting) sounds like it's just reading out a command line — backticks, `--flags`, ALL_CAPS env-var names spoken aloud | A hardcoded engine-unreachable fallback message (e.g. "claude CLI is not installed... start `ollama serve`... set CORVIN_OS_ENGINE") is used as BOTH the visible chat text and — unmodified — the spoken text; this fires on a fresh install where Claude Code can't be reached (stripped-PATH auto-downgrade to Hermes, see #9/ADR-0159) and Ollama isn't running yet either. Also hit the exact same way on the primary Claude Code engine's own timeout/error fallbacks (not just the Hermes edge case), and on Codex/OpenCode. Separately, a `/task` (background) completion delivered through `completion_notify.py` never stripped the tag at all, so it could leak the raw `<voice>...</voice>` markup straight into the visible chat message — worse than the original bug. | Fixed (2026-07-12, hardened same day after adversarial review): every affected fallback string (`call_claude()`, `_call_claude_streaming_via_engine()`, `_call_codex_streaming_via_engine()`, `_call_opencode_streaming_via_engine()`, `_call_hermes_streaming_via_engine()`, `call_claude_streaming()`'s ClaudeCodeEngine guard — all in `operator/bridges/shared/adapter.py`) now builds its return value via `voice_tag.with_voice_override(visible, spoken)`, a shared helper (`operator/bridges/shared/voice_tag.py`) that (a) appends a `<voice>…</voice>` override with a natural English sentence — same mechanism a model-authored reply uses to speak something different from what's shown, and (b) neutralizes any literal `<`/`>` in the visible text first, so untrusted subprocess stderr / provider error text can never smuggle a stray `<voice>` tag that hijacks the extraction. `completion_notify.mark_done()` now also calls `extract_voice_override()` itself — the one choke point every producer (including `bg_task_worker.py`) already calls — so a background-task completion can never leak the raw tag regardless of which engine produced it. The spoken sentences are English, matching the pre-existing visible-text convention in these functions (CLAUDE.md: repository content is English; these are static source literals, not runtime language-adapted text, so the "user-facing runtime text" exception didn't apply to a hardcoded German literal). |
| 36 | `mcp__imagegen-zero-config__generate_image` hangs forever — spinner never resolves, no image, no error, no timeout (reported live on Windows 11) | Investigated in depth (timeout coverage across the whole call chain, MCP subprocess spawn on Windows, whether anything downstream ever bounds a hanging turn). Confirmed: `_save_image_bytes()`'s `mkdir`/`write_bytes` had NO timeout — only a `try/except`, which cannot interrupt a syscall stuck *inside the kernel* (e.g. a stalled OneDrive-synced or network-mapped folder backing the session workdir — both common on Windows, and made more likely to matter by the previous day's `CORVIN_IMAGE_OUTDIR` fix, which pointed the write target at the session workdir instead of an arbitrary cwd). Separately confirmed: **nothing downstream ever times out a hanging turn either** — the console's stdout-reading loop (`chat_runtime.py`) has no deadline, and the messenger bridge's `subprocess.communicate()` is explicitly documented as "no default time limit" unless an operator opts in via `CLAUDE_BRIDGE_TIMEOUT`. So a stuck write was invisible and unbounded all the way from the MCP server to the user's screen. | Fixed (2026-07-13): `operator/mcp_manager/servers/imagegen-zero-config/main.py` now bounds both (a) the file-save step (`_SAVE_TIMEOUT_S = 15s`) and (b) the entire `generate_image()` tool call as a holistic safety net (`_TOTAL_TIMEOUT_S = 150s`, generous enough for the worst-case L44 gate + provider HTTP call chain to legitimately finish). Both use a `threading.Thread(daemon=True)` + bounded `.join()` — NOT `signal.alarm`, which doesn't exist on Windows and would have made this a Linux-only fix contradicting "must run everywhere." A still-stuck operation is abandoned (its daemon thread never blocks process exit) rather than awaited; the tool call itself always returns within the bound, either with the real result or a clear "timed out" `ImageGenRefused` error instead of hanging silently forever. Verified with real simulated 5-second hangs in both spots — tests assert the call actually returns in under 2s despite the artificial hang, not just that the timeout constant exists. Tests: `test_imagegen_zero_config.py::test_save_image_bytes_abandons_a_stuck_write_instead_of_hanging`, `::test_generate_image_returns_timeout_error_instead_of_hanging_forever`, `::test_generate_image_still_works_normally_through_the_timeout_wrapper` (regression guard for the fast path). |
| 35 | Console chat: an image generated via the `imagegen-zero-config` MCP tool shows only as a generic downloadable file card (filename, size, download icon) instead of rendering inline as a photo | Investigated in depth (frontend `isImage` MIME check, backend `_artifact_mime()` classification, `sid`/URL construction, `_SAFE_SUBPATH` regex, CSP headers — all verified correct for a `.jpeg` file). Found one confirmed, real reliability gap: `operator/mcp_manager/servers/imagegen-zero-config/main.py::_save_image_bytes()` writes to `Path.cwd()/outputs` by default — relying on implicit cwd inheritance through the `claude` CLI subprocess, an assumption the server's own code comments already flagged as unverified for this exact spawn chain (the same class of gap that already needed an explicit `CORVIN_HOME`/`CORVIN_TENANT_ID` workaround). **Could not conclusively confirm this is what produced the reported screenshot** — no browser-automation tool was available in this session to reproduce live, and the exact file wasn't present on the machine used to investigate (the report was from a separate Windows installation). | Hardened regardless, since the gap is real and well-evidenced: `get_active_mcp_servers()` (`operator/mcp_manager/mcp_manager/activate.py`) gained an `image_outdir` parameter that explicitly sets `CORVIN_IMAGE_OUTDIR` on the `imagegen-zero-config` catalog entry's env, pointing it at the actual session/chat workdir's `outputs/` subdirectory instead of leaving it to guess. Wired at both call sites: the console (`chat_runtime.py::_persona_mcp_config`, now takes a `workdir` param) and the messenger bridges (`adapter.py::_resolve_spawn_inputs`). New tests close the blind spot that let this go unverified: `test_mcp_m4.py::TestImageOutdirInjection` (env injection), and `test_imagegen_zero_config.py`'s new round-trip tests — proving `_save_image_bytes()` actually honours the env var AND that the file it writes is classified `image/jpeg` by the real `chat_runtime._artifact_mime()` function, not a synthetic path. If this doesn't fully resolve the symptom, the next diagnostic step is the browser DevTools Network tab for the failed `<img>` request (exact HTTP status immediately narrows it further). |
| 34 | Fresh-install/console "Welcome" spoken greeting comes out in English even though `profile.display_language` is set to a non-de/en locale (e.g. `zh`) — not translated, not left in the user's actual language either | `operator/voice/i18n/` ships only `de.json` and `en.json` — no bundle exists for any other locale. `_welcome_check_lang()` (`core/console/corvin_console/routes/setup.py`) resolves the greeting language straight from `profile.display_language` via `i18n.resolve()`, which happily returns e.g. `zh-Hans` — but `i18n.t()`'s own lookup chain (exact locale → base locale → English → literal key, see `i18n.py::t`) then silently falls all the way through to English for every `welcome.*` key, with no warning logged anywhere. **Root cause (found 2026-07-12, same day):** the installer (`corvinOS/installer/steps/piper.py::_seed_profile_display_language`) correctly seeds the language picked at install time — but two OTHER write paths never validate what they store: the generic in-chat `/profile set display_language=<value>` (`operator/voice/scripts/profile_cli.py::cmd_set`) and the console's `PUT /v1/console/profile` (`core/console/corvin_console/routes/profile.py`, `IdentityFields.display_language`) both persisted the raw client value verbatim, unlike the purpose-built `/lang set` (`lang_cli.py`), which always runs it through `i18n.normalise()`. A bare, un-normalized `"zh"` (instead of the canonical `"zh-Hans"`) written through either of those two paths is what actually broke the greeting on this deployment — confirmed by `config.json`'s `lang_default: "de"` / `piper_model_de` showing the installer itself seeded German correctly. | **Fixed (2026-07-12):** both under-validated write paths now route `display_language` through the same `i18n.normalise()` call `/lang set` already uses — `profile_cli.py::cmd_set` special-cases the key and refuses an unrecognisable code instead of storing it; `routes/profile.py`'s `IdentityFields` gained a `field_validator` doing the same, returning HTTP 422 for a bad code. The live profile on this deployment was corrected back to `display_language: "de"` via the now-validated `/profile set` path. Tests: `operator/voice/scripts/test_profile_cli_lang.py` (5 cases) + 2 new cases in `core/console/tests/test_profile_routes.py` (`test_display_language_is_normalised_through_bcp47`, `test_display_language_rejects_unrecognisable_code`). **Residual gap also closed same day:** added a real, fully-translated `operator/voice/i18n/zh-Hans.json` bundle (all `lang`/`consent`/`welcome` keys) — a genuinely `zh-Hans`-preferring user now gets a real Chinese welcome greeting instead of the English fallback. `test_setup_welcome_check.py::test_zh_profile_greeting_is_now_real_chinese` proves it; the "no bundle at all" case is now covered via `ja` instead (still genuinely unbundled). |
| 36 | A chat/DM reply is visibly TRUNCATED mid-message — everything after some point (often where the reply *mentions* voice output, CLI, or code) silently vanishes from the chat text, while the spoken voice-note contains that missing tail | `extract_voice_override()` (`operator/bridges/shared/voice_tag.py`) used a leftmost `<voice>…</voice>` match: it paired the FIRST `<voice>` it found with the NEXT `</voice>`. When the visible prose legitimately *mentions* the literal token `<voice>` (e.g. a reply explaining the voice-override mechanism itself, or `` `<voice>` `` in backticks) and the producer ALSO appends its real trailing `<voice>…</voice>` block, the stray earlier mention paired with the real block's closing tag — so everything between the mention and the end got cut out of the chat text and misrouted into the spoken override. Reported 2026-07-13 (a reply cut off right after a "2. Code-Bug" heading that was immediately followed by "the `<voice>` path"). Note `with_voice_override()` already escaped `<`/`>` for *untrusted* strings, but MODEL-authored replies run unescaped, so the model could hijack its own block. | **Fixed (2026-07-13):** `extract_voice_override()` now pairs the LAST `</voice>` with the nearest preceding `<voice>` (rfind-based), so a stray EARLIER opening tag no longer swallows the real trailing block — the mention stays as literal text in the chat and the real block is extracted for TTS. `with_voice_override()`'s `<`/`>` escaping is kept as defense-in-depth against a stray CLOSING tag in untrusted content. Regression: `operator/bridges/shared/test_adapter_voice_override.py::test_literal_voice_mention_does_not_hijack_real_block`. |
| 37 | Fresh install (esp. Windows): the WELCOME greeting and the first CHAT voice come out in the WRONG language / inconsistent (welcome English, TTS German) — the install-time language choice is not preset for everything | `profile.display_language` (the SSOT every surface reads: `_welcome_check_lang` → English default, bridge `_resolve_voice_output_language` → German default, console `ttsLang` → English default) was seeded ONLY as a side effect of a SUCCESSFUL Piper model download — `_save_model_config` → `_seed_profile_display_language` at the very END of `_download_model`. Every other path returned WITHOUT seeding: the user skipping the voice model (`[0]`), an unparseable menu choice, a failed/partial ONNX fetch (Windows CDN reset / WinError 10054 / offline), or a model PREFETCHED by install.sh/.ps1 (which makes `_setup_model` early-return on "already configured"). Unseeded `display_language` → the three surfaces fall back to their three DIFFERENT hardcoded defaults, so welcome speaks English while the bridge TTS speaks German on the same box. Compounded on Windows by `_detect_language()` using the DEPRECATED `locale.getdefaultlocale()`, which returns `(None, None)` on some configs → a German box detected as `en`. 5-layer LDD root cause: language seeding was not a first-class step but a fragile, skippable byproduct of the TTS-model download (single-source-of-truth + explicit-over-implicit violation). | **Fixed (2026-07-13):** `_setup_model` now seeds `display_language` UNCONDITIONALLY and BEFORE the download, on every branch — interactive choice, non-interactive auto-detect, skip `[0]`, invalid choice, AND the prefetched-model early-return (seeded from `config.json`'s `lang_default` via new `_config_lang_default`). The seed is decoupled from download success (a failed fetch still presets the reply language). `_detect_language()` now uses the non-deprecated `GetUserDefaultLocaleName` (kernel32) first on Windows, with `getdefaultlocale()` only as fallback. `_seed_profile_display_language` now normalises through `i18n.normalise()` (closing the third un-normalised write path — cf. #34). Tests: 8 new cases in `tests/test_installer_piper.py` (seed fires on every branch incl. download-failure + prefetch, normalisation, empty-guard). Defence-in-depth follow-ups (closed same day, 2026-07-13): the three runtime fallbacks no longer DIVERGE when `display_language` is somehow still unset — a shared `i18n.system_language()` OS-locale tier is inserted BELOW the explicit profile pin and ABOVE each surface's constant, so `_welcome_check_lang` (`resolve(display_language, system_language(), default="en")`) and the bridge `_resolve_voice_output_language` (falls back to `system_language()` before the caller's `or "de"`) agree on the user's actual OS language; on Windows `GetUserDefaultLocaleName` makes every surface consistent. The console web chat's first-reply race is closed too: `chat.tsx`'s `ttsLang` now falls back to `navigator.language` (base subtag) instead of a hard `"en"` when the profile query hasn't resolved yet. Tests: 5 new `SystemLanguageTests` in `operator/bridges/shared/test_i18n.py`. |

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
