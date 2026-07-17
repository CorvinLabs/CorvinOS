# Bridge Model Routing — Workload Classifier (ADR-0043)

Fast-chat routing: conversational turns can use the engine's fast model
tier; everything else keeps the user's chosen model / the adaptive tiers.
**Opt-in, default OFF, fail-closed.**

## Resolution chain (`adapter.py::_resolve_os_model`)

Tier order (top wins): 1 `CORVIN_OS_MODEL_OVERRIDE` · 2 explicit
`profile.model` pin · 1.5 persona pin (ADR-0123) · 2.5 per-engine tenant
default (ADR-0119) · **2.7 workload routing (ADR-0043, this doc)** ·
3 adaptive autoselect (ADR-0024/0112) · 4 None (CLI subscription default).

Tier 2.7 acts ONLY when all of these hold:

- the turn's classification is `chat` with confidence ≥ 0.7,
- fast-chat mode is enabled (see Opt-in below),
- the engine has a tier entry in `engine_models._MODEL_TIER_MAPPING`
  (currently `claude_code` only — entries must be real registry engine
  ids; model ids are validated FAIL-CLOSED against `load_registry()`).

`code` / `uncertain` classifications change NOTHING — they fall through
to the adaptive tiers. An explicit model pin always wins (Tier 2 returns
before 2.7 is reached).

**Operator gotcha:** a per-engine tenant `os_model` default (Tier 2.5,
ADR-0119) also outranks Tier 2.7 — setting `spec.engine_models.
claude_code.os_model` AND `spec.features.fast_chat_mode: true` in the
same tenant YAML leaves fast-chat silently inert. Remove the os_model
pin if you want workload routing to act.

**Surface note:** classification runs in `call_claude` /
`call_claude_streaming`, i.e. on the bridge surfaces (Discord/WhatsApp/
messaging, and every path that spawns OS turns through them). Console
web-chat turns that do not pass through these entry points are not
classified.

## Data flow

`call_claude` / `call_claude_streaming` classify the prompt via
`workload_classifier.classify_and_store_workload_hint()` and thread the
hint **as a function parameter** through `_build_claude_args` /
`_resolve_spawn_inputs` into `_resolve_os_model` (never via `os.environ`
— a process-global env channel is racy and cross-tenant in a daemon
serving parallel chats). `_build_spawn_env` additionally exports
`CORVIN_WORKLOAD_{CLASS,CONFIDENCE,TIMESTAMP}` into the child spawn env
for observability only; inherited values are stripped first.

## Classifier (`workload_classifier.py`)

Heuristic, engine-agnostic, no LLM. Asymmetric-risk design: a false CHAT
downgrades a coding turn (expensive), a false CODE/UNCERTAIN keeps the
user's model (free) — so CHAT requires the ABSENCE of every code signal:

1. rate-limited (sliding window, 120/min/process) / oversized (>1 MB) /
   empty → `uncertain 0.0`
2. syntax signal (code fence, `def`/`class`/imports, traceback,
   `SELECT…FROM`, file references, …) → `code`
3. coding-intent verb AND code noun (EN+DE: "write/schreib … a
   function/Skript") → `code 0.75`
4. verb XOR noun → `uncertain 0.4`
5. no code signal at all → `chat 0.9`

## Opt-in (default false)

- per profile: `fast_chat_mode: true`
- per tenant: `spec.features.fast_chat_mode: true` in the tenant YAML

There is deliberately NO env-var switch (a process-wide flag would apply
across tenants) and no console UI yet (ADR-0043 lists the settings-page
exposure as follow-up).

## Audit

Every fast-tier routing decision emits `bridge.workload_model_selection`
via the hash-chained `_audit_event` wrapper with `{workload_type,
confidence, selected_model, engine, tier}` — never message content.
Classification-time audit callbacks hash the message with sha256
(stable across processes), also content-free.

## Tests

`tests/test_workload_classifier.py`,
`tests/test_engine_models_workload_routing.py`,
`tests/test_adr0043_bridge_integration.py`, `tests/test_adr0043_integration.py`,
`tests/test_adr0043_e2e.py` (real path through `_resolve_os_model`,
including "flag off is fully inert", "coding request never fast tier",
audit emission, and top-level-module import mode).
