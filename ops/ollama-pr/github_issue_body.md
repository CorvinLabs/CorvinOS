## What is CorvinOS?

CorvinOS is an open-source AI assistant gateway that bridges messaging platforms
(Discord, Telegram, Slack, WhatsApp, Email) to local and cloud AI backends through
a single compliant, audited layer. Apache-2.0 licensed, designed for self-hosted deployment.

Key properties:
- Multi-bridge: one running instance covers all connected channels simultaneously
- GDPR + EU AI Act compliant out of the box: hash-chained audit log, per-user consent
  gate (deny-by-default), one-time AI-disclosure card, data-residency routing
- Cross-platform: Linux, macOS, Windows (ADR-0159)
- Installed via: `pip install corvinos`
- Repo: https://github.com/CorvinLabs/CorvinOS

## Existing Ollama integration

CorvinOS already ships a first-class Ollama integration called HermesEngine:

- Speaks Ollama's `/api/chat` streaming API natively
- Configurable via `CORVIN_HERMES_MODEL` (aliases: `hermes-fast` / `hermes-balanced`
  / `hermes-capable` / `hermes-large`, or any direct Ollama tag — default: `qwen3:8b`)
- Passes the system's data-classification and egress-lockdown compliance gates
  (`locality=local, network_egress=none`) — the only CorvinOS engine that qualifies
  for CONFIDENTIAL tasks without a cloud-egress exception
- Users switch to it per-chat with `/engine hermes`
- On a fresh install without Anthropic credentials, HermesEngine is auto-selected
  as the primary OS engine (ADR-0159 M1)

## What we're proposing

We'd like CorvinOS listed under **Integrations → Assistants** on docs.ollama.com,
alongside OpenClaw and Hermes Agent.

We've looked at the existing `cmd/launch/` implementations (hermes.go, openclaw.go)
and have all required files ready:

| File | Status |
|---|---|
| `docs/integrations/corvinos.mdx` | Ready |
| `docs/integrations/index.mdx` | +1 line under Assistants |
| `docs/docs.json` | +1 entry in Assistants group |
| `app/ui/app/public/launch-icons/corvinos.svg` | Ready |
| `cmd/launch/corvinos.go` | Ready |
| `cmd/launch/corvinos_test.go` | Ready |

## Install path

CorvinOS is published on PyPI as `corvinos`. The Go launcher calls:

```bash
pip install corvinos     # installs corvin-serve CLI
corvin gateway start     # starts the gateway + opens console
```

The `cmd/launch/corvinos.go` implementation follows the same pattern as
`hermes.go` (pip-based install, binary detection, auto-update on launch).

## Quick start preview

```bash
ollama launch corvinos
```

1. Install — `pip install --upgrade corvinos` (auto-update on every launch)
2. Setup wizard — model selector, Ollama endpoint configuration
3. Gateway starts — console opens at `http://localhost:8000/console/`

## Links

- Repo: https://github.com/CorvinLabs/CorvinOS
- PyPI: https://pypi.org/project/corvinos/
- Docs: https://corvin.ai/docs
