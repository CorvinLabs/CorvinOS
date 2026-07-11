# Image Generation — Zero-Config Tier (Concept)

Companion to [imagegen-mcp.md](claude-ref/imagegen-mcp.md) (current BYOK-only integration)
and the zero-config precedent in `Corvin-ADR/decisions/0185-cross-platform-voice-reliability.md`.

## 1. Problem

CorvinOS already ships an image-generation tool (`imagegen-mcp-server`, hardcoded into
`assistant.json`/`forge.json`/`research.json`), but it only works if the user has already
configured an `OPENAI_API_KEY` (or Replicate/Google credentials) — verified in
`docs/claude-ref/imagegen-mcp.md`. On a genuinely fresh install (`install.sh`/`install.ps1`,
no manual key setup), asking the assistant to draw something today either fails outright or
silently no-ops, depending on how the calling code handles the missing key. This is the same
"works for me, breaks on a clean machine" gap that ADR-0185 already closed for voice
(STT/TTS) — image generation never got the equivalent fix.

## 2. Current state (verified in code)

| Question | Answer | Where |
|---|---|---|
| Zero-config tier today | **None.** 100% BYOK — no key, no image. | `docs/claude-ref/imagegen-mcp.md` |
| How it's wired | Hardcoded `mcp_servers.imagegen` block in 3 persona JSON files, `npx`-fetches `imagegen-mcp-server@latest` (~50 MB) on first use | `operator/cowork/personas/{assistant,forge,research}.json` |
| Compliance gating | **None.** Bypasses the governed MCP tool catalog entirely — no `compliance.locality`/`hosts` declaration, so L34 (data classification) and L35 (egress lockdown) never see this outbound call | n/a — this is the gap |
| The correct governed path | `operator/mcp_manager/` (ADR-0096): a per-tenant catalog (`<corvin_home>/tenants/<tid>/global/mcp-tools/catalog.json`) where every tool declares `compliance.{locality,hosts}` and is checked by `mcp_manager/compliance.py` at activation **and** spawn time — this is where a first-class tool belongs, not persona JSON | `operator/mcp_manager/mcp_manager/{catalog,compliance,activate}.py` |
| Key storage if BYOK | `~/.config/corvin-voice/service.env` via the canonical `provider_keys.py` resolver (process env → service.env → legacy aliases) | `operator/bridges/shared/provider_keys.py`, `operator/agent/byok.py` |
| The doc's own warning | *"Don't add ImageGen to the `os`/`forge` personas without explicit ADR reasoning"* — the existing doc already anticipates that broadening this needs governance | `docs/claude-ref/imagegen-mcp.md:159` |

## 3. What ADR-0185 already proved works (the pattern to mirror)

Voice hit the identical problem and solved it with a **two-tier fallback resolver**, not a
config flag:

- **TTS:** Tier 0 = `edge-tts` (free, no key, needs internet). Tier 0b = `piper-tts` +
  a bundled tiny voice model, downloaded once at **install time** (visible progress, never
  fails install if offline), engaged automatically when edge-tts is unreachable. Tier 1 =
  optional BYOK cloud TTS.
- **STT:** Tier 0 = `pywhispercpp` + a bundled quantized model (fully offline, zero-config).
  Tier 1 = optional BYOK cloud Whisper.
- Selection is a **try/probe cascade inside a resolver** (`operator/voice/scripts/stt/resolver.py`
  and the mirrored TTS logic in `adapter.py`) — env vars only *pin* a provider, they don't
  choose the default path.
- User-facing surface: a per-provider status row in Console Settings (ready / not-configured
  / error, one obvious next action) plus a `corvin-voice doctor` CLI self-test — replacing a
  prior incident where a raw resolver exception got dumped into chat.

## 4. Research: what a Tier 0 for images could actually be

**Zero-config HTTP (no key, no signup):** [Pollinations.ai](https://pollinations.ai) — a
genuinely free, unauthenticated image endpoint (`GET image.pollinations.ai/prompt/<text>`).
No account needed; anonymous requests are rate-limited (roughly one request per ~15s) with
no uptime SLA, since it's a community-run service, not a commercial API with a contract.
Registering a free account raises the limit and drops the watermark, still with no payment.
This is the closest image-world analogue to `edge-tts`: free, zero-config, "just works,"
but explicitly **not** enterprise-grade.

**Fully offline (the Piper analogue):** FLUX.1-schnell, SD3.5, SANA, and similar
Apache/open-licensed models are the current best self-hostable options — but every one of
them needs a real GPU (12–16 GB VRAM for FLUX/SD3.5; SANA is the lightest at ~16 GB still)
and multi-GB weight downloads. **There is no image-generation equivalent of Piper's tiny,
CPU-friendly model** — this asymmetry matters (see §6): a bundled fully-offline tier is not
"free" the way it was for TTS, and bundling multi-GB weights into the installer would
directly contradict the install-time philosophy ADR-0185 established (defer gracefully,
never balloon the installer).

**Existing OSS MCP servers:** several already exist on GitHub (Together AI-backed
`mcp-image-gen`, OpenAI-only wrappers, a Stable-Diffusion-backed MCP server) — none of them
ship a no-key default tier; they're all thin wrappers around a paid or self-hosted backend,
same shape as CorvinOS's current `imagegen-mcp-server` dependency.

## 5. Dialectical pass: is a default-on external call even acceptable here?

**Thesis:** make Tier 0 default-ON like the anonymous telemetry ping — it's the whole point
of "works immediately," and matching the ping's opt-out pattern keeps the precedent simple.

**Antithesis:** the ping ships an anonymous uuid4 + closed enums — never user content. An
image prompt is **user-authored content**, sometimes descriptive of exactly what the user is
working on. Sending it to a third-party community service by default, with no disclosure,
is a materially different privacy posture than the telemetry table in CLAUDE.md — GDPR
purpose limitation and data minimization apply to prompt text the same way they apply to
voice transcripts (which this repo already treats as sensitive: L23 "Voice-transcribe audit
— metadata only, never text").

**Synthesis:** Tier 0 should be **default-available, not default-silent** — a one-time
disclosure the first time image generation is invoked (mirroring the existing Art. 50
bot-disclosure-card pattern: shown once per uid, not a modal every time), naming the actual
endpoint the prompt will leave the machine to. After that one disclosure, subsequent calls
proceed without re-prompting — same UX shape as the bot-disclosure card, not a fresh consent
dialog every time. A tenant that wants zero external calls by default (EU_PRODUCTION-style
presets already do this for other hosts) can set `forbidden_hosts` to block it outright —
L35 already fails closed on that.

## 6. Design

### Tier 0 — zero-config default, disclosed
- Wrap Pollinations' HTTP endpoint as a genuinely first-class MCP tool, registered through
  `operator/mcp_manager/`'s catalog — **not** persona-hardcoded JSON — so it's subject to
  L34/L35 like every other governed tool from day one (closing the gap in §2, not repeating it).
- Declare `compliance.hosts: ["image.pollinations.ai"]` so an EU_PRODUCTION tenant preset can
  forbid it the same way it already forbids `api.openai.com`.
- One-time disclosure on first invocation per uid (reuse the Art. 50 disclosure-card
  mechanism/pattern, not a new consent subsystem).
- Route the prompt text through the existing L44 house-rules gate before it ever leaves the
  machine — the same acceptable-use check that already gates other agent actions, so a
  disallowed-content prompt is blocked before hitting a third party, not after.
- Rate-limit-aware: on a 429/timeout, fail with a clear "Tier 0 is rate-limited, add your own
  key for higher throughput" message — never a raw stack trace (the exact incident ADR-0185
  already fixed for voice).

### Tier 1 — BYOK upgrade (mostly already exists)
- Keep OpenAI/Replicate/Google as the paid, higher-quality tier — but migrate them into the
  same `mcp_manager` catalog entry (not separate persona JSON blocks), reusing
  `provider_keys.py` as the single resolver, exactly like STT/TTS already do. This removes
  the current divergence where imagegen has its own bespoke env-var lookup instead of the
  canonical one.
- No behavior change for users who already configured a key today.

### Tier 0b — local/offline (explicit stretch goal, NOT a v1 default)
- For a tenant with a capable GPU, offer FLUX.1-schnell (or a smaller distilled model) as an
  **opt-in** enhancement, not a bundled default — given the VRAM/disk footprint asymmetry
  from §4, treating this as equivalent to Piper would be dishonest about the actual cost.

## 7. What this does NOT change

- Existing BYOK users see no behavior change — their configured key still wins.
- `check_egress`/L35 forbidden-host enforcement is untouched; a tenant can still block the
  Tier 0 host entirely via existing config.
- No change to the house-rules gate itself — image prompts route through the *existing* gate,
  no new bypass.

## 8. Open questions / risks (worth resolving before implementation)

- **Reliability contract:** Pollinations has no SLA — is a "free but sometimes down" default
  acceptable for a product install, or should Tier 0 explicitly market itself as "best-effort"?
- **Content moderation surface:** an image *result*, not just the prompt, could contain
  disallowed content the house-rules gate never sees (it only inspects the prompt) — is a
  post-generation check needed, or is prompt-side gating sufficient for v1?
- **Multi-tenant rate-limit sharing:** if many tenants on one CorvinOS instance all hit the
  same anonymous Pollinations rate limit, one tenant's usage could starve another's — needs
  a decision (per-tenant registered account? Accept the shared-fate risk for v1?).
- **ToS fit:** confirm Pollinations' terms permit the intended usage pattern (assistant-driven,
  potentially unattended generation) before shipping as a default, not after.

## 9. Phased delivery (proposed)

1. Register Tier 0 (Pollinations) as a first-class `mcp_manager` catalog entry with
   `compliance.hosts` declared — closes the L34/L35 gap for image generation entirely,
   independent of the disclosure/UX work.
2. One-time disclosure UX (reuses Art. 50 card pattern) + house-rules gate wiring for prompts.
3. Migrate existing BYOK (Tier 1) providers into the same catalog entry, replacing the
   persona-hardcoded blocks; update `docs/claude-ref/imagegen-mcp.md` accordingly.
4. Tier 0b (local/offline) as a later, clearly-opt-in enhancement — not blocking 1–3.

## 10. Governance note

This is a **concept only** — no ADR has been filed. If this moves to implementation, it
should get one: it introduces a new default external dependency (an egress path) plus a new
compliance-relevant mechanism (the disclosure gate), which are exactly this repo's own
ADR-gate triggers. `docs/claude-ref/imagegen-mcp.md`'s existing "don't broaden this without
an ADR" line already anticipated that.
