# Fresh-system E2E — install on a bare machine, then use it with real LLM calls

The README promises: `git clone … && cd CorvinOS && ./install.sh`, nothing else. This
directory proves that promise on a machine that has nothing from the dev host — and then
proves the product on it: chat, voice summary, peers and A2A, each through the installed
product's real HTTP/WebSocket/CLI surface, each with real model calls.

## The fresh system

`fresh.sh` builds a bare Ubuntu 24.04 container with systemd (so `systemctl --user`, the
watchdog and `corvin-webui.service` run as on a real desktop/server), one ordinary user
`tester` and ONLY `git`, `curl` and CA certificates. No python3, no node, no xz.

```bash
tests/e2e/fresh_system/fresh.sh build                 # once
tests/e2e/fresh_system/fresh.sh up corvin-a           # a bare system
tests/e2e/fresh_system/fresh.sh install corvin-a      # git clone from GitHub + ./install.sh --lan (~12 min)
tests/e2e/fresh_system/fresh.sh login corvin-a        # a Claude Code login for real model calls
tests/e2e/fresh_system/fresh.sh ex corvin-a 'curl -s http://127.0.0.1:8765/v1/console/healthz'
```

`install` clones `main` from GitHub by default (`fresh.sh install NAME <ref>` for a branch or
sha; `CORVIN_FRESH_REPO` for a fork) — so **push first, then test**: what is proven is what a new
operator gets. The console binds `127.0.0.1` inside the container; drive it from inside
(`fresh.sh ex`). Containers share the docker network `corvinnet` and resolve each other by
name, which is how two of them pair over A2A (`--lan` puts the A2A ingress on `0.0.0.0:8775`).

`login` copies the host's `~/.claude/.credentials.json` **without the refresh token**: the
system can call the model with the operator's own subscription but can never rotate the
host's login. When the access token expires (about 8 h), run `login` again.

## The live E2E tests

`test_fresh_*_live.py` run against an installed fresh system and are gated:

```bash
CORVIN_FRESH_E2E=1 CORVIN_FRESH_CONTAINER=corvin-a pytest tests/e2e/fresh_system -q
```

Each test goes through the real transport of the installed product (local-login cookie,
WebSocket chat, voice routes, `corvin-a2a`, the federation and A2A console routes), makes
real model calls and asserts on the answer, on the audit-chain records and on the persisted
artefacts. They are not run by the default CI (they need Docker, network and a login); they
are the release gate for "works on a fresh system".

`test_fresh_install_live.py` is the gate for the install and the first boot itself: ONE runtime
root (`<checkout>/.corvin`, nothing under `~/.corvin`) with ONE audit chain and no
`audit_chain_split` from the boot tripwire, `CORVIN_HOME` on every unit, the tenant migration a
no-op, step 17 reporting the console up with its seconds, `scripts/verify_install.py` passing
on the system, and the Claude Code the installer put there answering a real prompt. Pass the
install output as `CORVIN_FRESH_INSTALL_LOG=<host path>` (what `fresh.sh install` printed).

## Why a container with systemd, not a venv

`core/console/corvin_console/web-next/playwright.fresh-install.config.ts` already proves the
marketplace path on an empty `CORVIN_HOME`, but with the dev host's venv, node and python.
`tests/e2e/test-install-docker.sh` only checked the installer's syntax. Neither could have
found what the first run of this harness found on 2026-10-21: the Claude Code installer run
under `sh` (dash) instead of `bash`, and a Node bootstrap that needs `xz` (see CHANGELOG).

## Chat and voice summary (`test_fresh_chat_live.py`, `test_fresh_voice_summary_live.py`)

Driven from INSIDE the fresh system with the product's own interpreter (it has `httpx` and
`websockets`; the system has no python3), against `CORVIN_FRESH_BASE_URL` (default
`http://127.0.0.1:8765`). `fresh_console.py` is the shared driver: local-login cookie + CSRF,
one user turn over `/v1/console/chat/sessions/{sid}/stream`, the tenant audit chain
(`CORVIN_FRESH_TENANT_HOME`, default `<checkout>/.corvin/tenants/_default`).

```bash
tests/e2e/fresh_system/fresh.sh ex corvin-a 'cd ~/CorvinOS && CORVIN_FRESH_E2E=1 \
  uvx --with httpx --with websockets --with pytest python -m pytest tests/e2e/fresh_system -q -p no:cacheprovider'
```

What they prove with real model calls: capabilities/settings/engine probe need no operator
setup; a turn streams (`turn_started … result{outcome: ok} … done`), is persisted with its
`execution_context` (Haiku for conversation, ADR-0952), writes a paired
`os_turn.started`/`os_turn.completed` with model and token counts; context survives into the next
turn; German in → German out and English in → English out on an install whose locale seeded
`display_language`; `/help`, `/new`, `/btw`; cancel mid-turn; a secret in the prompt never reaches
the chain. Voice: a Voice-on turn owes and delivers the per-task recap and the session recap
(real `claude -p` text about the turn, real MP3 via edge-tts — no OpenAI key), history, the
on-demand session recap, `/voice/tts` in de and en, `/voice/status`; an English chat gets an
English recap. Each run takes ~8 minutes and spends real tokens.
