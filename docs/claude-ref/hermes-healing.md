# Hermes Healing System — ACO L5 Tier LOCAL

**ADR-0178, Tier LOCAL (Bounded, Reversible, Offline):** Automated restoration and monitoring of Hermes (Ollama + qwen3 model), the reliable local fallback engine for Claude Code when remote models are unavailable.

## Overview

The Corvin bridge must always have a working fallback engine to guarantee service continuity. **Hermes** (Ollama-hosted qwen3 model) is that fallback. The Hermes Healing system ensures it stays healthy:

- **Continuous monitoring:** every 5 minutes via systemd timer
- **Automatic repair:** restarts Ollama, re-pulls models (bounded, offline, never touches code)
- **Loss-gated:** repairs are rolled back if they don't restore reachability
- **Audited:** all actions logged to `aco_repair.jsonl` + L16 hash chain

## Architecture

### Three layers:

1. **hermes_healing.py** (operator/bridges/shared/)
   - Low-level health checks: is Ollama reachable? Are models installed?
   - Repair logic: start Ollama, pull qwen3 model
   - Diagnostics: human-readable status strings

2. **HermesHealthRepair** (core/console/corvin_console/aco/repair_actions.py)
   - Registered ACO L5 `RepairAction` (risk="risky")
   - Integrates with the unified repair loop in `run_local_repairs()`
   - Loss-gated execution: reverts if no progress

3. **systemd integration** (operator/bridges/systemd/)
   - `corvin-hermes-health.service` — one-shot repair executor
   - `corvin-hermes-health.timer` — runs every 5 minutes
   - Wired into `bridge.sh up` → installed automatically

### Setup

**First-time installation:**
```bash
bash operator/bridges/setup-hermes-pib.sh install
```

This:
- Detects platform (Linux / macOS / Windows) and available RAM
- Auto-installs Ollama if missing (via curl / brew / winget)
- Starts the Ollama server
- Pulls the recommended qwen3 model (8b for ≥6 GB RAM, 1.7b for < 6 GB)

**Verify:**
```bash
bash operator/bridges/setup-hermes-pib.sh check
```

Returns:
```
✓ Ollama is installed
✓ Ollama server is reachable
✓ Model qwen3:8b is installed
```

**Manual repair:**
```bash
bash operator/bridges/setup-hermes-pib.sh repair
```

### Runtime behavior

Once the timer is enabled (`bridge.sh up`), the system runs these checks every 5 minutes:

1. **Precondition:** Is Ollama reachable? Is the qwen3 model present?
2. **Apply (if faults found):**
   - Start Ollama server (if down)
   - Pull qwen3 model (if missing)
3. **Loss gate:** Re-check reachability. If still unhealthy, roll back (revert is a no-op for Ollama; we want it to stay running).
4. **Audit:** Log to `~/.corvin/aco_repair.jsonl` + L16 hash chain

### Environment variables

**Runtime:**
- `CORVIN_ACO_L5_OFF=1` — disable ALL L5 repairs (kills Hermes healing too)
- `CORVIN_ACO_L5_RISKY=1` — required to enable risky repairs (Hermes is marked risky because it starts a subprocess)

**Custom Ollama:**
- `OLLAMA_BASE_URL=http://custom.host:11434` — point to a remote Ollama (advanced)

### Config toggles (`tenant.corvin.yaml`) + console UI

The env vars above are the operator-level override and always take precedence.
As a persistent, per-tenant fallback the same two switches are read from
`<corvin_home>/tenants/<tid>/global/tenant.corvin.yaml`:

```yaml
aco:
  l5_enabled: true    # false → same effect as CORVIN_ACO_L5_OFF=1 (default: true)
  l5_risky:   false   # true  → same effect as CORVIN_ACO_L5_RISKY=1 (default: false)
telemetry:
  healing_traces: true  # upload anonymised healing events (ADR-0180; default: true)
```

Resolution order for the two ACO flags: **env var wins, then the config value,
then the built-in default**. `_kill_switch()` / `_risky_enabled()` in
`aco/repair_actions.py` implement this.

These three flags are also editable from the web console **Settings →
Self-healing** section (cards *Self-healing configuration* + *Send healing
telemetry*), backed by `GET`/`PATCH /v1/console/healing-config`
(`routes/healing_config.py`). The PATCH is a key-level merge — it never rewrites
unrelated keys in `tenant.corvin.yaml`. Note `telemetry.healing_traces` is only
the operator-level gate; an upload additionally requires the per-user GDPR Art. 7
`ConsentAct` (`aco/htrace_consent.py`).

### Integration with bridge services

When `bridge.sh up` runs:
1. Installs user systemd units for all channels + timers
2. **Enables Hermes health timer** — prints `✓ corvin hermes-health timer enabled (every 5 minutes)`
3. If it fails, prints `⚠ corvin hermes-health timer could not be enabled — run 'bash operator/bridges/setup-hermes-pib.sh --check' to diagnose`

The timer is **not** a channel itself; it's a passive health-check background task.

### Monitoring

**Check health manually:**
```python
from operator.bridges.shared.hermes_healing import get_health_status, diagnose_hermes
status = get_health_status()
print(diagnose_hermes())
# Output: "✓ Hermes healthy (2 models)" or "✗ Ollama unreachable — use /repair to start"
```

**View repair logs:**
```bash
tail -20 ~/.corvin/aco_repair.jsonl | jq '. | select(.event == "repair.applied" and .action_id == "hermes_health")'
```

**systemd journal (Linux only):**
```bash
journalctl --user -u corvin-hermes-health.service -f  # follow live
```

## Guarantees

### What this system provides:

- ✅ **Hermes always starts** — systemd ensures Ollama runs on boot
- ✅ **Continuous availability** — every 5 minutes, faults are detected and repaired
- ✅ **Bounded repairs** — only affects `~/.corvin/`, never touches code or system
- ✅ **Reversible** — rolls back if repair doesn't restore health (loss gate)
- ✅ **Offline** — zero network egress (local Ollama only)
- ✅ **Audited** — every action logged + hash chained (L16)
- ✅ **Cross-platform** — Linux, macOS, Windows (no bash dependencies in Python layer)

### What it doesn't do:

- ❌ **Heal Claude Code** — only Hermes (the fallback)
- ❌ **Modify repo code** — changes are sandbox to `~/.corvin/`
- ❌ **Auto-download large models** — model pull can take minutes (opt-in only)
- ❌ **Override user config** — if user disables Hermes via env var, healing respects it

## Troubleshooting

### "Hermes unreachable" after `bridge.sh up`

1. Check if Ollama is installed:
   ```bash
   bash operator/bridges/setup-hermes-pib.sh check
   ```

2. If not installed, install it:
   ```bash
   bash operator/bridges/setup-hermes-pib.sh install
   ```

3. If the timer didn't start (no output in `journalctl --user -u corvin-hermes-health.timer`):
   ```bash
   systemctl --user restart corvin-hermes-health.timer
   systemctl --user status corvin-hermes-health.timer
   ```

### "Model not installed" after timer runs

The healing system attempted to pull the model but it failed (slow download, disk full, etc.).
Manual pull:
```bash
ollama pull qwen3:8b  # for ≥6 GB RAM
# or
ollama pull qwen3:1.7b  # for < 6 GB RAM
```

### "No systemd" (macOS, WSL1)

Systemd timers don't work. Manual periodic repair:
```bash
# Add to your shell startup or cron:
export CORVIN_ACO_L5_RISKY=1
python3 -c "from corvin_console.aco import repair_actions; repair_actions.run_default()"
```

Or use the setup script in foreground mode:
```bash
bash operator/bridges/setup-hermes-pib.sh repair
```

## ADR references

- **ADR-0178** — Self-improvement (Tier LOCAL: bounded, reversible, offline)
- **ADR-0125** — Zero-config engine onboarding (Hermes bootstrap logic)
- **ADR-0143** — House-rules gate (acceptable-use checks; Hermes repair is safe)

## Testing

**Unit tests:**
```bash
pytest operator/bridges/shared/test_hermes_healing.py -v
pytest core/console/tests/test_aco_hermes_repair.py -v
```

**Integration test (requires Ollama installed and reachable):**
```bash
export CORVIN_ACO_L5_RISKY=1
python3 -c "from corvin_console.aco import repair_actions; results = repair_actions.run_local_repairs(dry_run=False); print(results)"
```

**Dry-run (no actual changes):**
```bash
export CORVIN_ACO_L5_RISKY=1
python3 -c "from corvin_console.aco import repair_actions; results = repair_actions.run_local_repairs(dry_run=True); print(results)"
```
