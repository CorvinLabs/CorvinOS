# Boot self-test — full reference

Module: `operator/bridges/shared/self_test.py`
Tests:  `operator/bridges/shared/test_self_test.py`
CLAUDE.md anchor: section "Boot self-test (load-bearing)"

This document covers what the file body itself doesn't: the per-check
rationale, the audit-event schema, the exit-code matrix, and the rules
for adding or modifying a check.

---

## Three call sites, one logic

| Call site | Mode | Where | What happens on `CRITICAL` |
|---|---|---|---|
| Adapter boot | full | `adapter.py::main()`, right after `path_gate_self_test()` | Logged with `self-test: FAILED — N CRITICAL`; audit event written; boot continues. |
| `bridge.sh doctor` | full (default), `--quick` forwarded | `cmd_doctor()` in `bridge.sh` after HTTP probes | Exit code 1; operator sees per-check status on stdout. |
| Docker `HEALTHCHECK` | `--quick --json` | `ops/healthcheck.sh` after `/readyz` + heartbeat | Exit 1 → container `unhealthy` after 3 retries (90 s); JSON payload tee'd to `/tmp/corvin-self-test.json`. |

Quick mode is the same orchestrator with three slow checks elided:

- `audit.chain_verified` (full hash-chain walk via `voice-audit verify`) → degraded to `audit.file_readable` (path exists + readable).
- `mcp.*_constructible` (instantiate the MCP server class) → omitted.

Quick budget: ~200 ms on a warm interpreter. Full budget: ~1–2 s.

---

## Check catalog

### `tenant.*` — tenant tree

| Check | Severity | Pass condition |
|---|---|---|
| `tenant.resolved` | CRITICAL | `forge.tenants.current_tenant()` returns a validated id, or env / fallback yields one. |
| `tenant.home_exists` | CRITICAL | `<corvin_home>/tenants/<tid>/` is a directory. |
| `tenant.home_writable` | CRITICAL | `os.access(home, W_OK)`. |
| `tenant.subdirs_present` | WARNING | Each of `global, sessions, forge, skill-forge, voice, cowork` exists. Created on first use by the migration helper, so absence is non-fatal. |

### `memory.*` — recall DB + user model

| Check | Severity | Pass condition |
|---|---|---|
| `memory.dir_present` | WARNING | `<global>/memory/` exists. |
| `memory.recall_db` | INFO | `recall.db` absent OR present. INFO regardless — DB is created on first turn. |
| `memory.recall_db_mode` | **CRITICAL** | If DB exists: mode is exactly `0o600`. Anything else is a structural privacy breach. |
| `memory.recall_db_openable` | WARNING | If DB exists: `sqlite3.connect()` works and an FTS5 virtual table can be created. |
| `memory.user_model_writable` | WARNING | If `user_model/` exists, it's writable. |

### `audit.*` — audit hash chain

| Check | Severity | Pass condition |
|---|---|---|
| `audit.path` | INFO | Always emits; surfaces the resolved path in the human report. |
| `audit.file_readable` (quick) | CRITICAL | If file exists and is non-empty: `os.access(path, R_OK)`. |
| `audit.chain_verified` (full) | CRITICAL | `voice-audit --path <p> verify` exits 0. rc 1 (integrity violation) and rc 2 (IO error) both flip the verdict. |

### `vault.*` — secrets vault

| Check | Severity | Pass condition |
|---|---|---|
| `vault.present` | INFO | Always emits. Absence is normal for deployments without forged tools. |
| `vault.mode_0600` | **CRITICAL** | If vault file exists: mode is exactly `0o600`. |

### `engine.*` — CLI engines

| Check | Severity | Pass condition |
|---|---|---|
| `engine.claude_cli` | CRITICAL | `claude --version` rc 0 within 5 s. |
| `engine.codex_cli` | INFO | Optional; never flips verdict. |
| `engine.opencode_cli` | INFO | Optional; never flips verdict. |

### `mcp.*` — MCP servers

| Check | Severity | Pass condition |
|---|---|---|
| `mcp.forge_importable` | CRITICAL | `import forge.mcp_server` succeeds. |
| `mcp.skill_forge_importable` | CRITICAL | `import skill_forge.mcp_server` succeeds. |
| `mcp.forge_constructible` (full) | WARNING | `MCPServer(tmp_root)` returns an object with a callable `serve` attribute. |
| `mcp.skill_forge_constructible` (full) | WARNING | `SkillForgeMCPServer()` returns an object with a callable `serve` attribute. |
| `mcp.third_party.<exec>` (full) | WARNING | Every distinct executable referenced in any `mcp_servers[*].command` of `operator/cowork/personas/*.json` resolves via `shutil.which`. `python`/`python3`/`node`/`npx` intermediates are skipped (covered by engine probe). |

Why no JSON-RPC `initialize` probe: in production the MCP servers are
stdio-spawned **children of the engine** for the duration of a turn —
never long-running daemons. Replicating that handshake without a real
engine attached is fragile and depends on protocol-version constants
that drift outside this module's control. Constructibility is the
strongest verifiable contract from the self-test's vantage point.

### `egress.*` — L35 egress policy (full mode only)

Implemented by `_check_egress()` in `self_test.py`. Runs only in full
mode (not in the HEALTHCHECK quick-path).

| Check | Severity | Pass condition |
|---|---|---|
| `egress.preset_loaded` | INFO | Tenant YAML loadable and `spec.egress` present; count of `allowed_hosts` reported. |
| `egress.preset_loaded` | WARNING | Tenant YAML present but `spec.egress` key absent (policy not configured). |
| `egress.preset_loaded` | CRITICAL | Tenant YAML unreadable or malformed. |
| `egress.preset_consistency` | WARNING | `validate_preset_consistency()` reports a mismatch (e.g. `default_action: deny` but no `allowed_hosts`). |

The check calls `EgressGate.validate_preset_consistency()` — a pure
static analysis with no network calls, so it stays fast and
side-effect-free.

### `artifacts.*` — L33 session artifact memory

| Check | Severity | Pass condition |
|---|---|---|
| `artifacts.library_importable` | **CRITICAL** | `from forge import artifacts` succeeds. Required by the PostToolUse auto-register hook; failure aborts remaining artifact checks. |
| `artifacts.config_readable` | WARNING | If a config file is present: `artifacts.load_config()` succeeds. Absent = INFO (defaults apply). |
| `artifacts.global_root_writable` | WARNING | If `<global>/artifacts/` exists: `os.access(root, W_OK)`. Not CRITICAL — the directory is created on first `artifact_pin` call. |
| `artifacts.mcp_handlers_registered` | **CRITICAL** | An ephemeral `MCPServer` instance advertises all six expected tools: `artifact_list`, `artifact_search`, `artifact_get`, `artifact_extract`, `artifact_register`, `artifact_pin`. A missing tool means the documented LLM surface is broken. |
| `artifacts.auto_register_hook` | INFO | `operator/voice/hooks/artifact_register.py` exists and is referenced in `hooks/hooks.json`. Verifies post-tag-bump wiring. |

---

### `layer_integrity.*` — L Integrity Protocol (ADR-0141)

| Check | Severity | Pass condition |
|---|---|---|
| `layer_integrity.capabilities` | **CRITICAL** | Tier 3 — `security_capabilities.bootstrap_core_capabilities()` registers all mandatory layers. A missing capability (deleted/tamper-removed layer) is CRITICAL; emits `security.capability_missing`. |
| `layer_integrity.manifest` | **CRITICAL** / WARNING | Tier 1 — on-disk layer hashes match the RS256-signed `operator/security/layer-manifest.json`. Absent manifest = WARNING (pre-rollout, no brick); present+bad-sig = CRITICAL; layer hash mismatch = CRITICAL. Emits `layer_integrity.{verified,manifest_absent,manifest_invalid,mismatch}`. |

→ Full details: `docs/claude-ref/layer-integrity-protocol.md`

---

## Audit event schema

Two events; both go into the unified hash chain at
`<global>/forge/audit.jsonl`. Emit is **best-effort** — a missing
`forge.security_events` import is silently swallowed, because the
self-test must never crash boot on its own observability.

```json
{
  "event_type": "boot.self_test_passed",
  "severity":   "INFO",
  "details": {
    "total_checks":      <int>,
    "critical_failures": [],
    "warnings":          ["<check.name>", ...],
    "quick":             false
  }
}
```

```json
{
  "event_type": "boot.self_test_failed",
  "severity":   "CRITICAL",
  "details": {
    "total_checks":      <int>,
    "critical_failures": ["<check.name>", ...],
    "warnings":          ["<check.name>", ...],
    "quick":             false
  }
}
```

**Privacy rule (enforced by `test_self_test.py::AuditEmissionPrivacyTests`):**
detail fields carry only check **names** and **counts**. Paths, error
strings, version strings, license-key bytes, and any user-visible
content never enter the chain.

---

## Exit-code matrix

| Mode | Critical failures | Warnings | Exit |
|---|---|---|---|
| default | 0 | any | 0 |
| default | ≥ 1 | any | 1 |
| `--strict` | 0 | 0 | 0 |
| `--strict` | 0 | ≥ 1 | 1 |
| `--strict` | ≥ 1 | any | 1 |
| `--json` | (output is JSON to stdout; exit code same as above) | | |

Adapter-boot path always runs in `quick=False` mode — full audit verify
matters at boot. The HEALTHCHECK runs `--quick` because it executes
every 30 s and must finish well inside the 10 s timeout.

---

## Adding or modifying a check

1. Decide the severity by asking: *"if this is broken at boot, do we want
   the container to be marked unhealthy?"* If yes → CRITICAL. If "operator
   should know but the system still works" → WARNING. If "purely
   informational" → INFO.

2. Make the check **side-effect-free**. Use temp dirs; never write
   outside `/tmp`; never modify `recall.db`, audit chain, vault, or
   tenant tree.

3. Add a test in `test_self_test.py` covering both the pass and fail
   paths — sandbox the filesystem with the `_Sandbox` helper so the
   host's real `~/.corvin` is never touched.

4. If the check probes a slow external resource, add a `quick` branch
   that skips it so the HEALTHCHECK budget is respected.

5. If the check name is new, mention it in this catalog. Do **not**
   change a check's name once it ships — audit consumers index events
   by `critical_failures[*]`.

6. Don't import `anthropic` from `self_test.py`. The module ships in
   the Apache core.

---

## Anti-patterns

- **Spawning a real MCP server with a JSON-RPC `initialize` handshake.**
  Tried during initial development; abandoned because (a) `forge.mcp_server`
  has no `__main__` block, (b) protocol-version drift would make the
  test flap, and (c) production never spawns the MCP server this way —
  the engine does, with a different orchestration.

- **Reading the user's vault to verify its content.** Mode check is
  enough; reading would risk leaking key material into the next layer
  if a check ever logs `detail` verbosely.

- **Treating absent optional engines as failures.** Codex and OpenCode
  are advertised as optional swappable workers in ADR-0017; their
  absence is the dominant case for free-tier installs.

- **Adding a `compliance-off` env flag to skip checks.** Forbidden by
  CLAUDE.md compliance baseline — there is no kill switch.
