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
on the system, and the Claude Code the installer put there answering a real prompt. The install's
own output is read from `~/install.log` inside the system (`fresh.sh install` tees it there) or from
`CORVIN_FRESH_INSTALL_LOG=<host path>`; the test first asserts the `[Step 17]` block is present
(positive control) before asserting its wording — `/tmp/corvinos-install.log` holds only the awaited
sub-steps and would have failed that assertion for the wrong reason.

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

## Peers and A2A between two fresh systems (`test_fresh_a2a_two_instances_live.py`)

Driven from the HOST (it needs `docker` to reach both containers) against two installed fresh
systems on `corvinnet`, `CORVIN_FRESH_CONTAINER_B` / `CORVIN_FRESH_CONTAINER_C` (default
`corvin-b` / `corvin-c`), both logged in (`fresh.sh login`). Everything goes through the installed
product: the console API inside each container (local-login + CSRF, what the browser calls),
`corvin-a2a`, the A2A ingress on `0.0.0.0:8775`, and each container's own audit chain.

```bash
CORVIN_FRESH_E2E=1 .venv/bin/python -m pytest tests/e2e/fresh_system/test_fresh_a2a_two_instances_live.py -q -p no:cacheprovider -x
```

`a2a.network` is member-only (gate 28ad07b54): a fresh install is Free and cannot pair, ping or
exchange anything. The test proves THAT first — 402 `license_limit` / `a2a.network` /
`member_seat_required` on create, import and send, `network.allowed: false` on every read, the
CLI's `error_detail` — and then runs the rest as a member install: `a2a_member_pin.sh <name> [off]`
installs a systemd drop-in whose `sitecustomize` pins the two licence-tier resolvers to `member`
(the repo's own A2A E2E fixture, `a2a_test_tier.pin_member_tier`, does the same under pytest;
nothing in the product changes). What it then proves with real workers: token pairing ACTIVE on
both sides with the ingress on 8775; an observer send recorded on both chains and run nowhere; an
executor send (`corvin-a2a send`) answered `MARMOT` by the peer's real `claude` worker with a priced
`engine.span.end` (model + four-way tokens); a sha256-pinned attachment and the 1 MiB cap (422);
replay (`replay`), tamper and unknown origin refused opaquely (`a2a_tamper_driver.py`); L44 refusing
prohibited content (`house_rules`); federation register/share/catalog/select/delegate (`OTTER`) with
both chain anchors in the trace; a four-turn agent-to-agent conversation that agrees on a code word
with an operator message, pause and resume; `/agents`, `/ask @peer` (`WALRUS`), `/ask @mine`
(`BADGER`), `/talk` + `/stop`; a group `send-to-peer` mirrored on the peer; a restart mid-conversation
(`interrupted`); a peer whose console is stopped (`unconfirmed`, presence offline); revoke named on
the other side (`peer_revoked_at`, `ENDED BY PEER`) and re-pairing. ~12 minutes, ~12 worker runs.

Round 2 (refutation) added: `corvin-a2a create-token` / `import-token` refusing on the Free install
with nothing written; twenty status reads writing at most one deny record; the envelope edges at the
raw ingress (`a2a_edges_driver.py`: 16/17 attachments, digest mismatch, `../` name, duplicate name,
issued_at ±290 s / ±310 s, a replay after the envelope's TTL — all opaque but `replay`); and a peer
that lost its seat (C unpinned, B restarted): B names it (`peer_network_denied_at`, UNREACHABLE,
offline) instead of "online via relay", and clears it once the seat is back. The suite starts from
nothing (it removes every friendship on both sides first) and re-pins both containers, so it can be
re-run on the same pair; what accumulates is only each chain and the containers' feeds.
`a2a_chat_send_driver.py` (inside a container) drives one real chat turn that asks the worker to stage
an A2A message, for the chat-staged two-step confirm.

### Round 2 additions (2026-10-21)

`test_fresh_chat_live.py` also proves: the profile snapshot's `display_language_source` (and that the
page's autosave echo does not re-pin the installer seed), attachments read by the next turn, `/btw`
from a second socket steering the live turn (`btw_status: injected`), and a service restart inside a
background scope (ADR-2236) — answer kept with `[background work interrupted here]`, `needs_user`
notice, NO recovery re-run. `test_fresh_voice_summary_live.py` adds `POST /voice/summarize` without a
language (follows the text) and a Piper WAV through `/voice/transcribe` (local Whisper). The WAV is
made with the PRODUCT's interpreter — `CORVIN_FRESH_PYTHON` overrides the auto-detected
`~/.local/share/uv/tools/corvinos/bin/python` (`fresh_console.product_python`). A Playwright session
through the SPA is NOT part of the suite: on a bare Ubuntu 24.04 the downloaded Chromium cannot launch
without `playwright install-deps chromium` (root), which the installer now reports instead of "ready".
