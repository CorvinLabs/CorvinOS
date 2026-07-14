# Image Generation — Zero-Config Tier (ADR-0191)

## Overview

CorvinOS ships a **first-party image-generation MCP server**,
`imagegen-zero-config` (`operator/mcp_manager/servers/imagegen-zero-config/`),
registered as a governed `mcp_manager` catalog entry and seeded automatically
on boot. It exposes ONE tool, `generate_image`, with two tiers:

- **Tier 0 (default, zero-config):** [Pollinations.ai](https://pollinations.ai) —
  free, no key, no signup. Best-effort community service, no uptime SLA
  (explicitly disclosed to the user, once per tenant).
- **Tier 1 (automatic upgrade):** the user's own OpenAI key (the SAME key
  already configured for Whisper/TTS), resolved via the canonical
  `provider_keys.resolve_key("openai_api_key")` chain — process env →
  `~/.config/corvin-voice/service.env`. If the configured key fails
  (expired/invalid/quota), the call degrades to Tier 0 with an explanatory
  note instead of erroring out.

The pre-ADR-0191 integration (persona-hardcoded `npx imagegen-mcp-server`)
has been **removed** from `assistant.json`/`forge.json`/`research.json`: it
was BYOK-only (broken without a key — the `${OPENAI_API_KEY}` template is
never resolved in MCP env, so it 401'd even WITH a key), and its
`api.openai.com` egress was invisible to L34/L35 governance.

## Architecture

### Catalog entry, not persona JSON

`mcp_manager/seed_builtin.py::ensure_imagegen_zero_config()` registers the
tool in the mcp_manager catalog with:

- `runtime.command = sys.executable` (never bare `python3` — PATH of the
  spawning process is not this venv's PATH)
- absolute `runtime.args` path to `main.py` (survives any cwd)
- `runtime.env = {CORVIN_HOME, CORVIN_TENANT_ID}` plaintext passthrough
  (an MCP subprocess does not reliably inherit these)
- `compliance.hosts = ["image.pollinations.ai", "api.openai.com"]` — so L35
  egress lockdown and L34 data classification gate it at activation AND
  spawn time like every other governed tool.

### Seeding — every boot path, operator intent respected

Seeding runs on BOTH server entry paths:

- gateway lifespan (`core/gateway/corvin_gateway/app.py`) — systemd/service
  path
- `corvin-serve` / `corvinos-serve` (`ops/launcher/corvin/serve_backend.py::
  _seed_builtin_tools`) — the primary pip/uv install path, which has no
  gateway lifespan (same class as the startup-ping/heartbeat workaround)

Seeding is marker-based (`<catalog_dir>/builtin-seeded.json`):

- first seed → install + tenant-activate + marker
- later boots → refresh stale interpreter/script paths only (upgrade case);
  **never** re-activate a tool the user deactivated, never clobber operator
  catalog edits, never re-install after an operator uninstall
- `_SEED_VERSION` bump → re-apply entry shape (still without forcing
  re-activation)

### Safety and compliance in `main.py`

- Every prompt passes the **L44 house-rules gate** (`check_l44`) BEFORE any
  provider sees it. Without a reachable classifier backend, L44 degrades to
  its deterministic Tier-0 regex floor (see Layer 44) — zero-config installs
  without Ollama still generate images.
- One-time **per-tenant disclosure** (`imagegen_disclosure.py`) marks its
  store BEFORE the prompt first leaves the machine and returns the notice as
  a real MCP text content block ahead of the image. English text (repo
  language policy); the relaying assistant localizes.
- Tier-0 hardening: prompt is a single URL path segment
  (`quote(safe="")`), 4 000-char prompt cap, 20 MB response cap, image
  format sniffed from magic bytes (Pollinations serves JPEG — never trust a
  hardcoded mime), non-image 200s rejected, redirects NOT followed (prompt
  travels in the URL; a redirect would leak it to an undeclared host),
  429/5xx/timeouts/connect errors all surface a friendly no-SLA message,
  never a raw stack trace.
- **Deadline-aware step budgets** (2026-07-14): `generate_image()` computes one
  shared deadline (`_TOTAL_TIMEOUT_S`, an ULTIMATE backstop for a true
  infinite hang) at the start of the call. Every bounded step below it — the
  L44 gate (`_L44_TIMEOUT_S`, its own bound since `check_l44()` takes none of
  its own and is shared by other callers with no analogous total budget),
  OpenAI, Pollinations, and the save step — is clamped to
  `min(its own default bound, time actually left before the deadline)`. Before
  this, each step got its own FULL static budget regardless of how much the
  earlier steps had already spent, so the legitimate (non-infinite) worst-case
  sum of every step could exceed the outer backstop on its own — three
  consecutive real calls hit exactly `_TOTAL_TIMEOUT_S`'s generic "please try
  again" message instead of one of the specific, more actionable per-step
  friendly messages (see `_remaining()` in `main.py`).

## Usage

```
User: "Generate an image of a surreal mouth floating in cosmic space"
Assistant: [calls generate_image] → relays the one-time disclosure (first
           use only) + the image
```

No setup, no key, works on a genuinely fresh install. Configure
`OPENAI_API_KEY` in `~/.config/corvin-voice/service.env` (or process env) to
upgrade to DALL-E 3 automatically.

## Testing

`operator/mcp_manager/tests/test_imagegen_zero_config.py` — seeding
idempotency + operator-intent (deactivation/uninstall/edit survival), stale
path refresh, disclosure once-per-tenant + path-injection + read-only-store
degradation, Tier-0 error taxonomy (429/500/redirect/non-image/connect),
MIME sniffing, prompt guards, broken-Tier-1-key fallback.

## Must NOT do

- Don't declare `OPENAI_API_KEY` as a catalog `secrets` entry — the
  `${VAR}` template is injected literally into the server env and breaks
  Tier-1 detection (`provider_keys` would treat the literal as a real key).
- Don't route any prompt to a provider before `check_l44` passes.
- Don't re-activate the tool on boot when the operator deactivated it.
- Don't follow provider redirects (prompt-in-URL leak class).
- Don't add hosts to the running entry without updating
  `compliance.hosts` in `seed_builtin.py` (L35 must see every egress host).
