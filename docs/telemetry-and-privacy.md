# Telemetry & Privacy

CorvinOS ships three telemetry channels that are **ON by default** and can be
**turned off at any time** (opt-out). They exist so the project can see how many
installations exist and fix bugs across them. **Everything transmitted is strictly
anonymous and content-free** — no prompts, no message content, no transcripts, no
personal data. Legal basis: **GDPR Art. 6(1)(f)** (legitimate interest).

A **fail-closed scrubber** (`_assert_safe` / `_assert_safe_htrace`) re-checks every
record before it leaves the machine and **drops** anything that carries a PII or
secret shape (emails, home paths, IPs, tokens, long ids) rather than sending it.

## The three channels

| Channel | What is sent | Turn off |
|---|---|---|
| **Anonymous instance ping** | A random installation id (`uuid4`) + the CorvinOS version + an HMAC token, once per 24 h. Lets us count how many instances exist. | `spec.telemetry.ping_enabled: false` |
| **Error diagnostics** | Scrubbed, content-free crash signatures: error type (e.g. `ValueError`), the repo file + function where it happened, allow-listed stack-frame namespaces. Never prompts or user data. | env `CORVIN_TELEMETRY_OPTIN=false` **or** `spec.telemetry.error_traces: false` |
| **Self-healing traces** | Anonymised self-healing events (which repair ran, on which code layer, success/failure). No prompts, no message content. | `spec.telemetry.healing_traces: false` |

## How to opt out

### 1. Console (easiest — one click)
Open the web console → **Settings** → **Telemetry & privacy**. Each channel has its
own switch; flipping it off writes the flag below into `tenant.corvin.yaml` immediately.

### 2. Config file
Edit `<corvin_home>/tenants/_default/global/tenant.corvin.yaml` and set any of:

```yaml
spec:
  telemetry:
    ping_enabled: false      # anonymous instance ping
    error_traces: false      # error diagnostics
    healing_traces: false    # self-healing traces
```

A `false`-like string (`false` / `no` / `0` / `off`) also disables a channel; any
other value — or a missing key — keeps it **on**.

### 3. Environment (error channel)
`export CORVIN_TELEMETRY_OPTIN=false` disables the error-diagnostics channel for the
process (highest precedence).

## What is NEVER sent

- Prompts, questions, or any message/chat content
- Transcripts, voice audio, or STT text
- File contents, code you write, or data you process
- Emails, names, user ids, IP addresses, secrets, or API keys
  (the fail-closed scrubber drops any record that even *looks* like one)

## Where the code lives

- Ping gate: `aco/htrace_consent.py::ping_enabled`
- Error gate: `aco/telemetry.py::consent_granted`
- Healing gate: `aco/htrace_consent.py::healing_traces_enabled`
- Scrubber (fail-closed backstop): `aco/telemetry.py::_assert_safe` + `_LEAK`,
  `aco/htrace.py::_assert_safe_htrace`
- Console toggles: `routes/healing_config.py` ↔ Settings → *Telemetry & privacy*

See ADR-0179 (error/healing telemetry) and ADR-0180 (instance ping + healing-trace
aggregation) for the design rationale.
