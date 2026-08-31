# CorvinOS Web Console — Handbook

> **The complete guide to the CorvinOS Operator Console.**
> Every page explained with screenshots, UI element descriptions, and step-by-step walkthroughs.

---

## Mental Model

CorvinOS is a **multi-channel AI assistant runtime**. You install it once, connect it to your messaging platforms (Discord, Telegram, WhatsApp, Slack, Email), and it runs AI assistants on your behalf — with full audit trails, GDPR-compliant consent handling, and EU AI Act disclosure compliance built in as structural guarantees.

The **Operator Console** (`http://localhost:8765/console/app`) is your control panel. Think of it in three zones:

```
┌─────────────────────────────────────────────────────────────────┐
│  SETTINGS          What your system IS                          │
│  Voice & Profile · Messaging Channels · AI Engine ·            │
│  API Keys                                                       │
├─────────────────────────────────────────────────────────────────┤
│  WORKSPACE         Who does the work and where it lives         │
│  Personas · People · Files                                      │
├─────────────────────────────────────────────────────────────────┤
│  DATA & RETRIEVAL  What information assistants can access       │
│  RAG Providers · RAG Hub · Create Provider                      │
├─────────────────────────────────────────────────────────────────┤
│  (hidden in sidebar, accessible via nav)                        │
│  AUTOMATION        Workflows · Forge · Skills                   │
│  CONNECTIVITY      Connectors · Agent Hub · Space               │
│  DATA SOURCES      External database connections                │
└─────────────────────────────────────────────────────────────────┘
```

**Key concepts to understand first:**

- **Tenant** — An isolated namespace (`_default` unless you create more). All settings, audit logs, and data live under a tenant.
- **Bridge** — A connector to a messaging platform (Discord, Telegram, etc.). The AI assistant is reachable there.
- **Persona** — A named character configuration (system prompt, engine, tools). Different personas suit different tasks.
- **Forge tool** — A sandboxed, schema-bound executable the AI can call. Created at runtime, bwrap-isolated.
- **Skill** — A reusable instruction block injected into future AI turns. Distilled know-how.
- **Audit log** — A tamper-evident hash chain recording every system event. Cannot be disabled.

---

## Navigation

The left sidebar is always visible. It groups pages into sections:

| Section | Pages |
|---|---|
| Quick access | Chat, Dashboard |
| SETTINGS | Voice & Profile, Messaging Channels, AI Engine, API Keys |
| WORKSPACE | Personas, People, Files |
| DATA & RETRIEVAL | RAG Providers, RAG Hub, Create Provider |
| (unlabeled) | Workflows, Forge, Skills, Connectors, Agent Hub, Space, Data Sources |

The top bar shows:
- **Connected / Disconnected** badge — WebSocket status to the CorvinOS daemon
- **Tenant pill** (`_default`) — active tenant
- **Engine buttons** (Claude Code / Assistant) — quick engine switch
- **Theme toggle** (moon icon) — light/dark mode

The bottom of the sidebar shows your active tenant and logout button.

---

## Table of Contents

| # | Page | What you'll learn |
|---|---|---|
| [01](01-dashboard.md) | [Dashboard](01-dashboard.md) | System-at-a-glance: engine, API keys, bridges, audit events |
| [02](02-chat.md) | [Chat](02-chat.md) | Talk to your AI assistant directly from the console |
| [03](03-voice-profile.md) | [Voice & Profile](03-voice-profile.md) | TTS voice identity, system prompt, voice style |
| [04](04-messaging-channels.md) | [Messaging Channels](04-messaging-channels.md) | Connect Telegram, Discord, WhatsApp, Slack, Email |
| [05](05-ai-engine.md) | [AI Engine](05-ai-engine.md) | Choose and configure which AI engine powers your assistant |
| [06](06-engine-control-center.md) | [Engine Control Center](06-engine-control-center.md) | Live engine switching, capability matrix, ECI commands |
| [07](07-api-keys.md) | [API Keys](07-api-keys.md) | Store service credentials in the encrypted vault |
| [08](08-personas.md) | [Personas](08-personas.md) | Named AI identities: system prompts, engines, tools |
| [10](10-people.md) | [People](10-people.md) | User roster, roles (owner/admin/member/observer), quotas |
| [11](11-files.md) | [Files](11-files.md) | Browse and upload files in the CorvinOS home directory |
| [12](12-rag-providers.md) | [RAG Providers](12-rag-providers.md) | Manage vector databases for retrieval-augmented generation |
| [13](13-rag-hub.md) | [RAG Hub](13-rag-hub.md) | Cross-provider search across all knowledge sources |
| [14](14-create-provider.md) | [Create Provider](14-create-provider.md) | Register a new RAG vector database |
| [15](15-workflows.md) | [Workflows](15-workflows.md) | Multi-step automation sequences with AI steps |
| [16](16-forge.md) | [Forge](16-forge.md) | Runtime-generated sandboxed tools the AI can call |
| [17](17-skills.md) | [Skills — SkillForge](17-skills.md) | Runtime-generated skills injected into future AI turns |
| [18](18-connectors.md) | [Connectors](18-connectors.md) | External service integrations (Gmail, Drive, Spotify, GitHub, …) |
| [19](19-agent-hub.md) | [Agent Hub](19-agent-hub.md) | A2A peering: connect this instance to other Corvin agents |
| [20](20-space.md) | [CorvinSpace](20-space.md) | Public profile, ActivityPub federation, custom domains |
| [21](21-data-sources.md) | [Data Sources](21-data-sources.md) | Register external databases for AI-driven data analysis |

---

## How to use this handbook

- **New to CorvinOS?** Read pages 01 → 04 to get the system running, then jump to whatever feature you need.
- **Setting up for the first time?** Follow the [Quick Start](../setup.md) in the main README, then come back to pages 07 (API Keys), 04 (Messaging Channels), and 03 (Voice & Profile).
- **Looking for a specific feature?** Use the table above to jump directly to the relevant page.
- **Each page follows the same structure:** overview → screenshot → UI elements → typical actions.
