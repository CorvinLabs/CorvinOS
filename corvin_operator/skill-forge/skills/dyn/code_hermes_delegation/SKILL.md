---
name: code_hermes_delegation
description: When and how to use delegate_hermes — fully-local Ollama/Hermes worker for zero-egress and CONFIDENTIAL-class delegation tasks (ADR-0066 M1)
---

# Hermes Delegation (ADR-0066 M1)

Use `mcp__corvin_delegate__delegate_hermes` when:
- Data must not leave the host (CONFIDENTIAL classification, air-gapped, no cloud API key).
- Zero inference cost matters (batch tasks, high-volume summarisation).
- Anthropic/OpenAI rate-limits are hit and a local fallback is acceptable.
- Task is self-contained and does not require mid-stream injection (`/btw`) or forge tool-calls.

## Model aliases (pass via `model` field)

| Alias | Size | Use-case |
|---|---|---|
| `hermes-fast` | 7B | Quick summaries, classification, routing |
| `hermes-balanced` | 13B | General delegation (default) |
| `hermes-capable` | Hermes-3 8B | Structured output, basic tool-calling (M2) |
| `hermes-large` | 70B | Complex reasoning (needs GPU) |

Omit `model` to use `CORVIN_HERMES_MODEL` env var, or the configured default (`nous-hermes-2`).

## When NOT to use

- Complex multi-step coding — use `delegate_claude_code`.
- Tasks that need forge/skill-forge MCP tools — Hermes M1 has `mcp: false`.
- Tasks that need mid-stream `/btw` injection — Hermes has `mid_stream_inject: false`.
- When Ollama is not running — call fails gracefully with `hermes.ollama_unavailable` audit event; do NOT retry blindly.

## Prompt discipline

Pass a **self-contained** prompt. Hermes has no bridge state, no recall, no skills.
Include all context inline. Keep prompts under 32 KB for the 7B/8B variants.

## L34 data-classification note

`hermes` maps to `locality: local, network_egress: none` in the engine-compliance matrix.
It is the **only** engine that qualifies for `CONFIDENTIAL` tasks under the EU_PRODUCTION
preset without a compliance-zone exception.
