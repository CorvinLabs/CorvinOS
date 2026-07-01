<picture>
  <source media="(prefers-color-scheme: dark)"  srcset="docs/assets/banner.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/banner.svg">
  <img src="docs/assets/banner.svg" alt="CorvinOS — The compliance-enforcing AI agent runtime for regulated environments" width="100%">
</picture>

<p align="center">
  <a href="https://pypi.org/project/corvinos/"><img src="https://img.shields.io/pypi/v/corvinos?style=flat-square&label=PyPI&color=22c55e&labelColor=161b22" alt="PyPI"/></a>
  <a href="https://pypi.org/project/corvinos/"><img src="https://img.shields.io/pypi/dm/corvinos?style=flat-square&label=installs%2Fmo&color=22c55e&labelColor=161b22" alt="PyPI Downloads"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-f0b429?style=flat-square&labelColor=161b22" alt="Apache 2.0"/></a>
  <a href="docs/eu-ai-act/README.md"><img src="https://img.shields.io/badge/EU%20AI%20Act%202026-Enforced-3b82f6?style=flat-square&labelColor=161b22" alt="EU AI Act 2026 Enforced"/></a>
  <a href="docs/audit-and-compliance.md"><img src="https://img.shields.io/badge/GDPR-Art.%206%2C7%2C17%2C30%2C32-3b82f6?style=flat-square&labelColor=161b22" alt="GDPR Compliant"/></a>
  <img src="https://img.shields.io/badge/engines-Claude%20·%20Codex%20·%20OpenCode%20·%20Hermes%20·%20Copilot-a78bfa?style=flat-square&labelColor=161b22" alt="5 WorkerEngines"/>
</p>

<p align="center">
  <a href="docs/overview.md">Overview</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="docs/audit-and-compliance.md">Audit &amp; Compliance</a> ·
  <a href="docs/agent-communication.md">A2A Network</a> ·
  <a href="docs/engine-layer.md">Engine Layer</a> ·
  <a href="docs/security.md">Security</a> ·
  <a href="docs/eu-ai-act/README.md">EU AI Act</a> ·
  <a href="docs/ulo-learning-objectives.md">Learning Objectives</a>
</p>

---

**One install. Seven bridges. Any LLM.**

CorvinOS is a self-hosted agentic OS that connects **Ollama, Claude, GPT-4, and any OpenRouter model** to **Discord, Telegram, WhatsApp, Slack, Email, Teams, and Signal** — through a single pip package.

```bash
# macOS / Linux — no Python or package manager required
curl -fsSL https://raw.githubusercontent.com/CorvinLabs/CorvinOS/main/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/CorvinLabs/CorvinOS/main/install.ps1 | iex
```

- **Local-first** — run 100 % offline with Ollama and `--engine hermes`. No API key needed.
- **Agentic** — generates sandboxed tools and new skills at runtime; delegates subtasks across five AI engines.
- **Compliance by architecture** — EU AI Act 2026 (disclosure, consent, house-rules) and GDPR (audit chain, data residency, erasure) are load-bearing code, not policy documents. None can be disabled by a flag.
- **Multi-tenant** — one instance, multiple users, personas, and teams, all isolated.
- **Self-hostable anywhere** — Linux, macOS, Windows; Docker-ready; single `pip install`.

---

**CorvinOS enforces the EU AI Act in code, not in documentation.**

Every compliance requirement — disclosure, consent, audit integrity, data residency, egress control, GDPR erasure — is a structural constraint that cannot be disabled by a flag, env var, or config override. Regulated deployments get verifiable guarantees, not policy promises.

---

## Quick Start

See [INSTALLATION.md](INSTALLATION.md) for the complete setup guide.

**Recommended — one line, every platform, zero prerequisites:**

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/CorvinLabs/CorvinOS/main/install.sh | sh

# Windows (PowerShell)
irm https://raw.githubusercontent.com/CorvinLabs/CorvinOS/main/install.ps1 | iex
```

> Once the site is deployed these also work as the shorter
> `curl -fsSL https://corvin-labs.com/install.sh | sh` /
> `irm https://corvin-labs.com/install.ps1 | iex`.

The bootstrap installer brings its **own Python** (via [uv](https://docs.astral.sh/uv/)),
so you need **no system Python, no pip, and no package manager** installed first. It
puts `corvinos-serve` on your `PATH`, and it **also sets up Hermes** — installs Ollama
and pulls a local model (`qwen3:8b`, or `qwen3:1.7b` on <6 GB RAM) — so CorvinOS runs
**fully offline with `--engine hermes` from the first start**. The Windows one-liner uses
`irm | iex` (no `&&`), so it works in both PowerShell 5.1 and 7.

> Only want cloud engines (Claude/GPT)? Skip the local model with `--no-hermes`
> (`sh -s -- --no-hermes`) or `CORVIN_SKIP_HERMES=1`.

**Already have Python 3.10+?** A plain pip install works identically on all three OSes:

```bash
pip install corvinos
python -m corvinOS        # web console at http://localhost:8765
```

`python -m corvinOS` is **PATH-independent**: it starts the console on the first
try on every OS — including Microsoft Store / system Python, where `pip install`
falls back to a per-user scripts directory that is not on `PATH` (the usual reason
`corvin-serve` is "not found" on Windows). On Windows you can also use `py -m corvinOS`.

**Want the short `corvin-serve` command on your PATH?** Install with
[pipx](https://pipx.pypa.io) — it isolates the app and wires up `PATH`
automatically, on every platform:

```bash
pipx install corvinos
corvin-serve              # web console at http://localhost:8765
```

> `corvin-serve` from a plain `pip install` only works once its scripts directory
> is on `PATH`. Running `python -m corvinOS` once adds that directory to your PATH,
> so `corvin-serve` then works in a new terminal — but `pipx` (or `python -m
> corvinOS`) is the reliable cross-platform path.

The base install is pure-Python and cross-platform — it brings the web console
all the way up to setup on Linux, macOS, and Windows, with cloud/edge voice
(OpenAI + Microsoft Edge TTS) working out of the box. For **local, offline**
speech models add the optional extra:

```bash
pip install "corvinos[voice]"   # local Piper TTS + faster-whisper STT
```

> The `voice` extra is opt-in because its local-model dependencies (`piper-tts`,
> `faster-whisper`) lack Windows wheels for some Python versions; keeping them
> out of the base install means `pip install corvinos` reaches setup reliably on
> every supported platform.

**Requirements:** Python 3.10+ · Linux, macOS 12+, or Windows 10/11 · Node.js 20+ required only for bridges

Default engine: Claude Code (Claude Pro or Max subscription required).
For fully local, zero-egress deployment: `--engine hermes` (Ollama, no API key).

**Alternative package managers:**

```bash
# macOS / Linux — Homebrew
brew tap CorvinLabs/corvinos && brew install corvinos

# Windows — Scoop
scoop bucket add corvinos https://github.com/CorvinLabs/scoop-corvinos && scoop install corvinos

# conda / mamba (pending review)
conda install -c conda-forge corvinos
```

```bash
# Developer install from source
git clone https://github.com/CorvinLabs/CorvinOS.git && cd CorvinOS
pip install -e ".[all]" && corvin-install
```

Full documentation: [docs/overview.md](docs/overview.md)

### Uninstall

One command removes everything — services, config, data directories, and the package:

```bash
corvin-uninstall --purge   # removes all files without prompting
pip uninstall corvinos -y  # removes the Python package
```

Without `--purge` the uninstaller asks before deleting data directories (audit logs, API keys, session history). Use `--purge` for a fully non-interactive wipe.

**What gets removed:**

| What | Where |
|---|---|
| systemd services (`corvin-webui`, voice bridge, watchdog) | `~/.config/systemd/user/` |
| Claude Code plugins + cache | `~/.claude/plugins/` |
| Voice config, API keys, service.env | `~/.config/corvin-voice/` |
| Audit logs, sessions, forge tools | `~/.corvin/` |
| Bridge virtual environments | `~/.corvin/bridges/*/venv/` |
| Web console build artifacts (source install only) | `<repo>/core/console/.../web-next/dist/` |

After `pip uninstall corvinos -y` the only thing left is the cloned repo directory (source installs) — delete it with `rm -rf <repo>` if you no longer need it.

---

## EU AI Act 2026 + GDPR: Structural Enforcement

CorvinOS implements EU AI Act 2026 and GDPR as **structural design constraints**. Every feature must answer: *does this weaken a compliance guarantee?*

| Mechanism | Layer | Regulation | Enforcement property |
|---|---|---|---|
| **Bot Disclosure** | L19 | EU AI Act Art. 50 | One-time per uid · structurally fail-closed · no bypass path |
| **Consent Gate** | L16 | GDPR Art. 6 & 7 | Deny-by-default · TTL-capped · re-validated at every consume |
| **Hash-Chained Audit** | L16 | GDPR Art. 30 & 32 | SHA-256 chain · offline-verifiable · daily auto-verify · chain write failure blocks request |
| **Audit-at-Rest Encryption** | L37 | GDPR Art. 32 | Segment rotation · `age`/`gpg` sealing · RFC 3161 TSA timestamping (opt-in) · 7-year retention |
| **Data Classification + Flow Guard** | L34 | EU AI Act Art. 14 | 4-stage matrix (PUBLIC/INTERNAL/CONFIDENTIAL/SECRET) · fail-closed at every engine-spawn callsite |
| **Egress Lockdown** | L35 | EU AI Act Art. 14 | Declarative `allowed_hosts` / `forbidden_hosts` · `default_action=deny` EU production preset |
| **GDPR Art. 17 Erasure** | L36 | GDPR Art. 17 | Cross-layer erasure orchestrator · pseudonymous subject IDs · audit trail de-linked, not deleted |
| **Acceptable-Use Gate** | L44 | EU AI Act Art. 5 & 50 | SHA-256-anchored house-rules policy · no disable switch · no tenant override |
| **Compliance-Zone Routing** | ADR-0007 | EU AI Act Art. 14 | `allowed_engines` / `forbid_engines` per tenant · `data_residency` in `tenant.corvin.yaml` |

**Absolute constraints — no env var, flag, or config can disable these:**
disclosure is structurally locked · consent gate has no bypass · every audit event traverses the hash chain before any response · L34 blocks non-compliant engine spawns · L38 audit write failure blocks the A2A request · L44 house-rules gate has no kill-flag.

```bash
voice-audit verify              # walk the full hash chain; exits 1 on any break
bridge.sh doctor                # boot self-test with audit chain verification
python -m corvin_compliance_reports.cli generate processing-records   # GDPR Art. 30
```

Full compliance reference: [docs/eu-ai-act/README.md](docs/eu-ai-act/README.md) · [docs/audit-and-compliance.md](docs/audit-and-compliance.md)

---

## Core Features

### Swap the LLM Without Touching the Compliance Stack

CorvinOS decouples the AI backend from the compliance runtime via the `WorkerEngine` protocol (L22). Every engine shares path-gate, audit chain, and artifact registration through the Tool Execution Broker — swap providers without changing your compliance setup.

| Engine | Provider | Key property |
|---|---|---|
| **Claude Code** | Anthropic Claude (Pro/Max) | Full feature set — hooks, skills, MCP, mid-stream inject |
| **Codex CLI** | OpenAI | MCP + stream JSON |
| **OpenCode** | Ollama, OpenRouter, Google | Provider-agnostic |
| **Hermes** | NousResearch via local Ollama | Zero network egress · L34 CONFIDENTIAL-capable · no API key |
| **Copilot CLI** | GitHub Copilot Business/Enterprise | Zero incremental cost · worker/delegation only |

### Multi-Agent Mesh — CorvinOS Instances Talk to Each Other

Multiple CorvinOS instances form a decentralized agent network. Every cross-instance call carries a cryptographic signature, bidirectional attestation, nonce replay protection, and binary attachment verification. Audit-first invariant: the envelope is written to the hash chain **before** any response is sent.

### 39-Layer Security Stack

Path-gate (write-protection) · secret vault with bwrap env-injection · sandboxed Forge tool generation · SkillForge with fail-closed linter · multi-tenant session isolation · conversation recall with PII-redaction · session artifact memory · external data sources with k-anonymised sampling.

### Data Never Leaves Without Your Explicit Permission

Three-layer defence: per-tenant engine allowlist → data classification matrix (PUBLIC / INTERNAL / CONFIDENTIAL / SECRET) → egress host allowlist. EU_PRODUCTION presets ship out of the box. Raw data rows never enter the LLM context — only schema + aggregate stats + anonymised sample.

### Web Console — Manage Everything From the Browser

Control plane at `http://localhost:8765`. Manage sessions, personas, bridges, forge tools, and audit logs from a single dashboard. Five-scope tenant model: one instance handles multiple users, projects, and teams in full isolation. Full REST API at `/v1/console/`.

```bash
bridge.sh console     # start web console
bridge.sh doctor      # health check + audit verify
```

---

## Architecture

<img src="docs/assets/arch.svg" alt="CorvinOS architecture — channels → Bridge Adapter → WorkerEngine" width="100%"/>

Seven bridge daemons (WhatsApp, Telegram, Discord, Slack, Email, Teams, Signal) funnel messages into a shared inbox. The Bridge Adapter enforces ACL, routes to the right persona, runs the TTS pipeline, and grades skills — per-chat-sequential, cross-chat-parallel. The WorkerEngine abstraction swaps the LLM backend without touching the compliance stack.

Full layer breakdown: [docs/layer-model.md](docs/layer-model.md) · Architecture diagrams: [docs/diagrams/](docs/diagrams/) · Full documentation: [docs/overview.md](docs/overview.md)

---

## Testing

```bash
bash operator/bridges/run-all-tests.sh
```

Tests span the Python adapter, Node daemon-boot smoke tests, cowork, forge, skill-forge, and all security layers. Tests run hermetically — Claude stubbed via `ADAPTER_FAKE_CLAUDE=1`, real `bwrap` where namespace isolation is the subject under test.

---

## Contributing

By opening a pull request you accept [`CLA.md`](CLA.md). Every merged contribution requires a corresponding entry in [`CLA-SIGNATORIES.md`](CLA-SIGNATORIES.md). See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full workflow.

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

**Relicense right (CLA §3):** The Maintainer retains the right to release future versions of CorvinOS under a different license — including source-available licenses (Business Source License, Functional Source License, Elastic License v2) or a commercial license — without requiring further consent from contributors. This right is granted by every contributor as a condition of the [`CLA.md`](CLA.md). Already-published Apache-2.0 releases are not affected; they remain Apache-2.0 forever. See [`CLA.md § 3`](CLA.md#3-relicense-right-the-load-bearing-clause) for the full terms.

"CorvinOS" and "Corvin" are project identifiers per Apache § 6 — the license does not grant trademark rights.
