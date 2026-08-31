# corvin-compute — Iterative Big-Data Compute Worker (ADR-0013)

**Status:** opt-in plugin. Default state of a fresh Corvin install is
**plugin not bootstrapped, worker not running, MCP tools not advertised**.

This plugin ships a long-running worker process that drives parameter
sweeps, optimisation and convergence tasks out-of-LLM-loop. The LLM
submits a run, gets a handle, and polls for progress / final result.
The driver owns the iteration logic; the LLM stays out of the loop and
sees only Top-K fingerprints, never raw parameter values.

See `docs/decisions/0013-compute-worker-plugin.md` for the design and
`docs/decisions/0013-implementation-plan.md` for the phased rollout.

## Opt-in installation

```bash
# Step 1 — bootstrap the plugin's own venv (separate from voice / forge / etc.)
bash core/compute/bootstrap.sh

# Optional — minimal install (no Bayesian strategy)
CORVIN_COMPUTE_MINIMAL=1 bash core/compute/bootstrap.sh

# Step 2 — enable the tenant's compute block in tenant.corvin.yaml
#
#   spec:
#     compute:
#       enabled: true

# Step 3 — launch the worker for the tenant (foreground or systemd-user)
core/compute/.venv/bin/python -m corvin_compute serve \
    --tenant _default \
    --corvin-home ~/.corvin
```

## MCP surface

When the worker socket is reachable, the Forge MCP server advertises
four extra tools:

- `mcp__forge__compute_run`
- `mcp__forge__compute_status`
- `mcp__forge__compute_result`
- `mcp__forge__compute_abort`

When the socket is unreachable, the tools are silently omitted — a
single `compute.worker_unreachable` WARNING audit event records the
miss per process boot.

## Cost contract

The driver costs **zero Anthropic tokens** during execution. The plugin
MUST NOT `import anthropic`; a CI lint enforces it via AST walk.
Future LLM-aware strategies authenticate via `claude -p` subprocess
(subscription-native, mirror of the Layer-11 dialectic pattern).

## Compliance

- Parameter values never enter the audit chain (Tier-1
  fingerprint-only; mirror of L23 voice-transcribe rule).
- Tier-2 artefact tree at `<corvin_home>/tenants/<tid>/compute/runs/`
  is path-gate-protected (Layer 10).
- Tier-3 per-field `x-sensitive: true` annotation in the tool schema
  hashes the value at the iteration-log layer.
