# Data & Compute Reference (Layers 24, 25, 32)

> Load when working on forge data registration, compute worker, or strict anonymisation.
> Quick summary in CLAUDE.md § Layer 24.

## Layer 24 — Large-Data Snapshot Layer (ADR-0012, PII-aware data locality)

Closes the data-locality gap on Forge (Layer 6). Before Layer 24,
a 1 GB CSV registered with a forged tool would either (a) blow the
LLM's context window on a `Read` to "understand the shape" or
(b) leak PII through an intermediate `head -20` pipe into the
chain. Layer 24 routes large datasets through a **deterministic
snapshot pipeline**: only an aggregated, redacted projection
reaches the LLM, while the raw bytes stay sandbox-side and the
forged tool computes the real answer over the full data via
ro-bind mount.

**Package:** `operator/forge/forge/corvin_data/` — pure-Python,
zero LLM dependencies, no pandas/polars (CSV/JSON/JSONL via
stdlib; Parquet via DuckDB read-only).

```
corvin_data/
├── format_sniffer.py    # magic-byte + extension → Format enum
├── snapshot.py          # schema + sample + stats (HyperLogLog distinct,
│                        # P5/P95 quantiles, never raw min/max)
├── pii_detector.py      # 3-layer cascade: header heuristics → value regex
│                        # → optional Presidio NER
├── pii_presidio.py      # opt-in NER backend (gated, ~150 MB models)
├── redactor.py          # 6 strategies: drop / redact / pseudonymize /
│                        # mask_partial / aggregate_only / hash
├── pseudonymize.py      # per-tenant deterministic seed via secret-vault
├── data_policy.py       # operator policy loader (data_policy.yaml/.json)
├── schema_extension.py  # x-data + x-snapshot Forge-schema keys
├── data_registry.py     # handle store @ <forge_root>/data/handles/
└── mcp_handlers.py      # data_register / data_snapshot / data_unregister
```

### MCP surface

Three tools on the forge MCP server, advertised next to
`forge_tool` / `forge_promote` / `forge_list`:

| Tool | Purpose |
|---|---|
| `data_register(path?, format?, snapshot_options?, notes?)` | Sniff format, mint a `data_<22>` handle, generate + PII-redact a snapshot. Returns `{data_handle, snapshot, oversized}`. |
| `data_register(connection?, ...)` | **DSI v1 path** (ADR-0106 M4): register an external connection by name; returns adapter-metadata snapshot without any raw data. `path` and `connection` are mutually exclusive. |
| `data_snapshot(data_handle, options?)` | Regenerate the snapshot with different options (more sample rows, different stats, different redaction); bounded by operator policy. |
| `data_unregister(data_handle)` | Drop a handle (idempotent — returns `{ok, found}`). |

The forged tool itself receives the **real path** as `ro-bind`
mount; Pandas/Polars/DuckDB inside the bwrap subprocess work
over the full data and compute the real answer. The snapshot
is purely the AGENT-FACING view; SANDBOX-FACING data stays
unmodified.

### Operator policy — `<corvin_home>/global/data_policy.yaml`

```yaml
apiVersion: corvin/v1
kind: DataPolicy
spec:
  pii_backend: regex+headers        # or "presidio"
  default_strategy: redact          # 6 strategies; default per-column
  class_strategies:                 # per-PII-class override
    email: pseudonymize
    iban:  drop
  column_overrides:                 # per-column wins on conflict
    customer_email: pseudonymize
    notes: aggregate_only
  column_pii_class:                 # operator-tagged free-text columns
    notes: name
  noise:
    rowcount_jitter:           5     # ±N when rowcount < threshold
    rowcount_jitter_threshold: 100
    distinct_jitter:           3
    extremes:                  p05_p95  # min/max never raw
  strict_mode: false                # fail-loud on unknown column types
  snapshot_token_cap: 4000          # prompt-token budget; oversize → schema-only
```

Loader resolves in order: explicit path → `CORVIN_DATA_POLICY`
env → `<corvin_home>/global/data_policy.yaml` → `.json`
fallback → permissive defaults. Strict (`extra="forbid"`-style)
validation via `_validate()`; malformed file → `PolicyError`
fail-closed.

### Path-gate protection (Layer 10 extension)

`data_policy.yaml` / `.yml` / `.json` are operator-only files —
the LLM overwriting them could flip every PII strategy to `drop`
and disable redaction silently. Three new path-gate vectors:

| File-name match | Protected from |
|---|---|
| `data_policy.yaml` (last path component) | Write / Edit / Bash redirect / sed -i |
| `data_policy.yml` | same |
| `data_policy.json` | same |

Plus three new `_PROTECTED_HINTS` strings (`data_policy.yaml`
etc.) so fail-closed Bash detection (eval / heredoc / cmd-subst
referencing `data_policy`) trips even when the actual write
target is opaque. Read still allowed (operator diagnostics via
`cat data_policy.yaml` stay free).

### Audit-chain events (6 types, all metadata-only)

Registered in `forge/security_events.py::EVENT_SEVERITY`:

| Event | Severity | Details |
|---|---|---|
| `data.registered` | INFO | data_handle, format, size_b, rowcount (noised), rowcount_exact |
| `data.snapshot_generated` | INFO | data_handle, columns, rows, redacted, resnapshot? |
| `data.pii_detected` | INFO | data_handle, classes (count-per-class dict, **NEVER values**) |
| `data.unregistered` | INFO | data_handle, found |
| `data.policy_violated` | WARNING | reason (curated set), path_hint (basename only), format_hint |
| `data.snapshot_oversized` | WARNING | data_handle, cap_tokens, estimated_tokens, columns |

Mirror of the L23 voice-transcribe rule: **the snapshot itself
never lands in the chain.** Audit details carry schema-shape +
class counts only. The regression gate
(`test_data_register_audit_carries_no_values`) walks every audit
event and fails the suite if a raw value leaks.

### Prompt-token cap + degradation

`snapshot_token_cap` (operator-configurable in `data_policy.yaml`,
default 4 000 tokens) is enforced in `_apply_token_cap` after
redaction. ~4 chars/token heuristic via `json.dumps` length.
Oversize → payload degrades to `{file, schema, sample: [],
stats: {}, truncated: true}` and emits `data.snapshot_oversized`.

The handle stays valid — the agent can call `data_snapshot` with
tighter options (fewer rows, smaller distinct caps) to retry.
Sandbox-side compute over the full data is unaffected; only the
LLM-facing projection degrades.

`snapshot_token_cap = 0` is the operator opt-out (gate disabled);
values < 100 are rejected at policy-load time.

### Strict-mode policy gate

When `strict_mode: true`, `data_register` emits
`data.policy_violated` (WARNING) on every rejection — currently
two reasons: `unsupported-format`, `register-failed`. The
operator's Prometheus / Grafana dashboards see the cadence;
the chain records `path_hint` (basename only — never the full
path) for forensic correlation. Permissive default
(`strict_mode: false`) skips the audit emit; the ToolError
still fires.

### Prometheus metric families (6 total, ADR-0007 Phase 6 surface)

Aggregated in `corvin_gateway/audit_metrics.py`:

| Metric | Labels | Source event |
|---|---|---|
| `corvin_data_registered_total` | `format` | `data.registered` |
| `corvin_data_snapshots_generated_total` | — | `data.snapshot_generated` |
| `corvin_data_pii_detected_total` | `pii_class` | `data.pii_detected` |
| `corvin_data_unregistered_total` | — | `data.unregistered` |
| `corvin_data_policy_violated_total` | `reason` | `data.policy_violated` |
| `corvin_data_snapshot_oversized_total` | — | `data.snapshot_oversized` |

Label allow-lists:
- `format`: `csv` / `tsv` / `json` / `jsonl` / `parquet`; everything
  else collapses to `"other"`.
- `pii_class`: 11 curated classes (email, phone, iban, credit_card,
  us_ssn, ch_ahv, de_steuer_id, name, date_of_birth, address,
  opaque_id, national_id); `<no_pii>` dropped at projection time.
- `reason`: `unsupported-format` / `register-failed` (Phase 12.8
  set); other reasons fall through to the existing reason
  allow-list.

### Grafana panels

Three new panels on the security dashboard
(`docs/observability/grafana/corvin-security.json`):

| Panel | Purpose |
|---|---|
| Data registrations & PII | Combined timeseries of `data_registered` (by format) and `data_pii_detected` (by class) |
| Snapshots oversized / 1h | Stat panel with yellow > 1, red > 20 — surfaces a misconfigured tenant cap |
| Data-policy violations | Timeseries by reason — non-zero baseline = strict tenant policy vs current tool authors |

### What you, as Claude Code, must NOT do (Layer 24)

- **Don't put the snapshot content into any audit-event field.**
  The regression test
  (`test_data_register_audit_carries_no_values`) walks every
  emitted audit event and fails the suite if any raw value
  appears. Schema-shape + class counts only. DSGVO Art. 5
  baseline.
- **Don't lower `snapshot_token_cap` below 100.** The
  `_validate()` rejects values under 100; raising the lower
  bound would silently degrade every snapshot to schema-only
  on operators who never opted in to that mode.
- **Don't widen the `format` label allow-list to free-form
  strings.** Curated values keep cardinality bounded; a
  pathological tool author registering 10 000 "test.fmt.v3"
  formats would otherwise saturate the metrics surface.
- **Don't bypass the `_apply_token_cap` gate when calling
  the MCP handler from outside.** Phase 12.8 makes the gate
  load-bearing — the only path to a payload that exceeds the
  cap is `snapshot_token_cap = 0` (operator opt-out).
- **Don't move the snapshot-cache outside the path-gate
  protected forge tree.** Today `DataRegistry` writes
  `<forge_root>/data/handles/*.json` which is automatically
  covered by path-gate's `forge` rel-part check. Moving to
  `<corvin_home>/global/data/` (per the ADR sketch) would
  require widening path-gate; the current placement is the
  right call structurally.
- **Don't make `strict_mode` the default.** Operators who
  want fail-loud opt in explicitly; the permissive default
  preserves backward compatibility for forge tools written
  before Layer 24 landed.
- **Don't emit `data.policy_violated` from the permissive
  path.** The audit event is the operator's signal that
  strict-mode kicked in; firing on every rejected file
  (regardless of strict_mode) would saturate the chain on
  operators who never opted in to the loud path.
- **Don't run snapshot generation OUTSIDE the engine-zone gate
  (ADR-0007 Phase 3.3).** Snapshot generation happens
  adapter-side, in the same MCP-server process that already
  inherits the tenant's compliance zone. Moving the snapshot
  pipeline to a sidecar process would re-introduce the
  zone-routing question this layer was designed to side-step.
- **Don't add a `pii_backend: <custom>` value without an
  ADR-level review.** The dichotomy regex+headers vs presidio
  is the contract — adding a third backend without thinking
  about the metrics + audit ergonomics breaks dashboard parity
  AND test coverage.

**References:**
- `Corvin-ADR: decisions/0012-large-data-snapshot-layer.md` — ADR
- `operator/forge/forge/corvin_data/` — package (10 modules)
- `operator/forge/tests/test_corvin_data_*.py` — 7 suites, 333 cases
- `operator/voice/hooks/path_gate.py` — protection layer
- `operator/voice/hooks/test_path_gate.py` — 9 new data_policy cases
- `core/gateway/corvin_gateway/audit_metrics.py` — 6 metric families
- `docs/observability/grafana/corvin-security.json` — 3 dashboard panels
- L23 (voice-transcribe) — metadata-only-audit precedent generalised.

## Layer 25 — Compute Worker (opt-in iterative big-data, ADR-0013)

Out-of-LLM-loop iteration driver for parameter sweeps, optimisation and
convergence tasks. The LLM submits one `compute_run` call and reads
back a handle; the driver owns the iteration loop, evaluates stop
criteria deterministically, parallelises batches via a thread pool,
and exposes only Top-K progress + a final scalar via three additional
MCP tools. The Forge runner is reused verbatim — no second sandbox.

**Opt-in by design** — the plugin lives under `core/compute/`
with its own venv (`bootstrap.sh`) and is skipped from
`run-all-tests.sh` when the plugin tree is absent. A fresh Corvin
install ships **plugin not bootstrapped, worker not running, MCP tools
not advertised**.

### What's now true that wasn't before Layer 25

- An LLM running against the Forge MCP server with the compute worker
  running sees four extra tools: `compute_run`, `compute_status`,
  `compute_result`, `compute_abort`. With the worker stopped the
  tools silently disappear (one-shot `compute.worker_unreachable`
  WARNING per process).
- A 100-iter parameter sweep costs **three MCP tool calls** of LLM
  tokens (submit + poll + read), not 100. The driver pays for the
  actual compute in CPU seconds.
- Iterations parallelise cleanly via a ThreadPoolExecutor; the strategy
  protocol's `suggest_batch(history, n)` is the batching contract.
  Grid is trivially batched; Bayesian uses constant-liar q-EI; random
  emits N independent samples.
- Bayesian-Opt (sklearn `GaussianProcessRegressor` + EI) is bundled
  as the third strategy. Warm-up = 2 × n_axes random samples; per-
  `suggest_batch` CPU budget = 5 s; CPU exceed → run terminates with
  `error_class: "StrategyTimeout"`.
- Crash recovery is automatic: a worker that died mid-run rebuilds
  history from `iterations/*.json` and continues from `iter = max+1`.
  Non-resumable runs (strategy uninstalled) land in `state=failed`
  with `convergence_reason=recovery-failed:strategy-not-installed:X`.

### Files

| File | Role |
|---|---|
| `core/compute/bootstrap.sh` | Plugin venv setup. `CORVIN_COMPUTE_MINIMAL=1` skips sklearn for disk-constrained hosts |
| `core/compute/requirements.txt` | `scikit-learn>=1.4`, `numpy>=1.26` (Bayesian backend) |
| `corvin_compute/driver.py` | Sequential `ComputeRun` — single-threaded reference |
| `corvin_compute/parallel.py` | `ParallelDriver` — batched ThreadPoolExecutor |
| `corvin_compute/state.py` | `RunStore` + on-disk layout under `compute/runs/<id>/` |
| `corvin_compute/budget.py` | `Budget` + `evaluate_termination` (convergence/stall/budget) |
| `corvin_compute/iteration.py` | `IterRecord` + `param_fingerprint` (sha256:16-char) |
| `corvin_compute/audit.py` | Five-event allow-list emitter + Tier-3 redactor |
| `corvin_compute/strategies/{base,grid,random,bayesian}.py` | Three bundled strategies + Protocol |
| `corvin_compute/worker.py` | asyncio Unix-socket daemon (one tenant per worker) |
| `corvin_compute/transport.py` | Length-prefixed JSON framing (sync + async halves) |
| `corvin_compute/client.py` | Sync client used by the Forge MCP bridge |
| `corvin_compute/cli.py` | `python -m corvin_compute {serve,submit,status,result}` |
| `corvin_compute/mcp_bridge.py` | Tool definitions consumed by the Forge MCP server |
| `corvin_compute/recovery.py` | Phase 13.9 — non-terminal-run resume on worker boot |
| `operator/forge/forge/_compute_discovery.py` | Socket probe + 5 s TTL cache + one-shot audit |
| `operator/forge/forge/mcp_server.py` | Tools/list + tools/call wiring (conditional + fail-loud) |
| `operator/forge/forge/cache.py` | Parametric cache: honours `x-cache-key: true` |
| `core/gateway/corvin_gateway/tenant_config.py` | `spec.compute: ComputeConfig` schema slot |
| `operator/voice/hooks/path_gate.py` | Protects `<corvin_home>/**/compute/**` + worker.sock |
| `core/compute/systemd/corvin-compute@.service` | systemd-user template unit |
| `core/compute/tests/test_*.py` | 98 cases across the 10 sub-phases |

### MCP surface

```
mcp__forge__compute_run     → submit a run (returns IMMEDIATELY)
mcp__forge__compute_status  → poll progress (Top-K with fingerprints only)
mcp__forge__compute_result  → read terminal outcome (server-side wait_s up to 30 s)
mcp__forge__compute_abort   → request graceful termination
```

Tools are only advertised when the worker socket
`<corvin_home>/tenants/<tid>/compute/worker.sock` is reachable;
discovery is cached for 5 s. First miss per process emits
`compute.worker_unreachable` (WARNING); subsequent misses are silent.

### Tenant config

```yaml
spec:
  compute:
    enabled: true                        # default ON — opt-out per tenant if needed
    max_parallel_iterations:    4        # clamp [1, 16]
    max_concurrent_runs:        2        # clamp [1, 8]
    max_iterations_per_run:     200      # clamp [1, 10000]
    max_wall_clock_per_run_s:   600      # clamp [1, 86400]
    top_k_size:                 5        # clamp [1, 10]
    disallow_llm_strategies:    false
    strategies_allowed:         ["grid", "random", "bayesian"]
```

`extra="forbid"` on the Pydantic model — unknown keys raise (mirror of
ADR-0007 Phase 3.1 schema strictness).

### Audit chain (three-tier)

**Tier 1 — hash chain (LLM-visible via `voice-audit verify`)** — six new
event types in `EVENT_SEVERITY`:

| Event | Severity | Carries (allow-listed) |
|---|---|---|
| `compute.run_started` | INFO | `tool_name`, `strategy`, `budget` |
| `compute.iteration_completed` | INFO | `iter`, `loss`, `wall_ms`, `param_fingerprint`, `cache_hit`, `strategy` |
| `compute.run_terminal` | INFO | `state`, `total_iterations`, `total_wall_s`, `best_loss`, `convergence_reason` |
| `compute.run_failed` | WARNING | `iter`, `error_class`, `error_message` (200-char cap) |
| `compute.worker_unreachable` | WARNING | `tenant_id`, `attempted_socket` |
| `compute.run_recovering` | INFO | `resume_from_iter`, `history_size` |

`param_fingerprint = sha256(canonical_json(params))[:16]` — the only
LLM-visible representation of the params. Operationally correlatable
across the chain, no PII leak. The audit emitter validates `details`
against per-event allow-lists; extra keys raise
`AuditFieldNotAllowed` (regression gate for the metadata-only rule).

**Tier 2 — artifact directory** (operator-only):

```
<corvin_home>/tenants/<tid>/compute/runs/<run_id>/
├── manifest.json                  # tool_name, strategy, budget, sensitive_fields
├── summary.json                   # rolling: best_iter, best_loss, state, top_k
├── iterations/0001.json ...       # append-only per iter; clear params for non-sensitive
```

Mode `0o600`. Path-gate (Layer 10) extended with two protected paths:
- `<corvin_home>/**/compute/**` (the artifact tree)
- `<corvin_home>/**/compute/worker.sock` (explicit socket protection)

**Tier 3 — per-field `x-sensitive: true`** at the tool schema. A
sensitive field's value is replaced by `<hash:<12-char-sha256>>` BEFORE
the iter file hits disk. Non-sensitive fields pass through verbatim
(operator can read them; the chain still never sees them).
`redact_sensitive_fields(params, sensitive_fields)` is the redactor.

### Strategy interface (promotable via Skill-Forge)

Strategies satisfy `corvin_compute.strategies.base.Strategy`:

```python
class Strategy(Protocol):
    name: str
    def suggest_batch(self, history, n) -> list[ParamSet]: ...
    def update(self, history, new_results) -> None: ...
    def should_stop(self, history) -> tuple[bool, str]: ...
```

Strategy bodies run **inside the worker process**, NOT inside bwrap.
Deliberate trust boundary: strategies are operator-curated code with
promotion gates, equivalent in trust to the audit chain or path-gate
hook itself.

### Cost contract

The plugin MUST NOT `import anthropic` (or `openai` or
`google-cloud-aiplatform`). The Phase 13.1 CI lint walks every
`corvin_compute/**/*.py` AST and fails the suite on a forbidden
import. The `requirements.txt` is also checked. Future LLM-aware
strategies authenticate via `claude -p --max-turns 1 --no-tools`
subprocess (subscription-native; mirror of Layer-11 dialectic).

### Parametric cache (`x-cache-key`)

Forge's cache (`operator/forge/forge/cache.py::cache_key`) now honours
`x-cache-key: true` annotations per tool-schema field:

- At least one field opts in → only opted-in fields contribute to the
  cache key (deterministic-tool boilerplate fields like
  `_artifacts_dir` or `verbose` no longer cause misses).
- No field opts in → full-payload key (legacy pre-13.7 behaviour).

Back-compat: existing tools without annotations exhibit byte-identical
cache behaviour.

### What you, as Claude Code, must NOT do (Layer 25)

- **Don't put parameter values into any audit-event detail field.**
  The `_ALLOWED_FIELDS` allow-list is the structural defence; the
  regression gate (`test_iteration_event_rejects_params_in_clear`)
  fails the suite if a future edit smuggles raw values into the
  chain. DSGVO baseline + L23 precedent.
- **Don't bypass the worker.** Forged tools called inside the loop
  go through Forge's `run_tool`; there is no second-class path
  that skips bwrap or the policy clamp.
- **Don't auto-start the worker from any bridge or adapter code.**
  Operator action remains the gate. The bridge may emit a
  `compute.worker_unreachable` WARNING (one-shot per process) when
  `spec.compute.enabled: true` and the socket is absent; it MUST
  NOT spawn the daemon.
- **Don't share strategy state across runs.** Each `compute_run`
  is self-contained; the Bayesian GP, the grid index, the random
  RNG seed all live for one run. Hierarchical optimisation is the
  operator's concern — submit a second run with a tighter grid
  derived from the first's `best_params`.
- **Don't widen `_PROTECTED_HINTS`'s `compute` entry into a
  case-insensitive substring match without re-running
  `test_path_gate.py`.** The hint catches `eval` / heredoc cases
  referencing compute paths; broadening the match would
  false-positive on common English words.
- **Don't move recovery into the asyncio loop.** The Phase 13.9
  scan walks disk and runs `strategy.update()` per resumed run;
  both are sync work that belongs on a worker thread. The current
  `asyncio.to_thread(self._recover_pending)` is the load-bearing
  shape; running it on the loop would block socket accepts.
- **Don't make compute-tool calls cost LLM tokens per iteration.**
  The whole driver MUST cost zero Anthropic tokens during
  execution. `submit_run` is one MCP call; `compute_status` is
  one per poll (user-controlled cadence); `compute_result` is one.
  Strategies that genuinely need LLM help authenticate via
  `claude -p` subprocess (NOT via `ANTHROPIC_API_KEY` SDK calls);
  the tenant's `disallow_llm_strategies: true` blocks that family
  globally.
- **Don't ship the plugin as a hard runtime dependency of voice /
  cowork / forge.** The `try: import corvin_compute` shim in
  `mcp_server.py` is the contract — single-operator deployments
  that never enable compute keep working byte-identically.
- **Don't lower `MAX_FRAME_BYTES` (4 MiB) below the Forge stdout
  cap.** Run-submission payloads (`param_grid`) shouldn't come
  close, but a tenant submitting a 5 MiB grid as one op should
  hit a clean transport error, not be silently truncated.
- **Don't widen the discovery cache (`_CACHE_TTL_S = 5.0`) above
  ~30 s.** Worker restarts are visible within ~5 s; longer caches
  would let an LLM see ghost tools that point at a stopped worker.
  Sub-second caches burn the connect syscall on every list-tools
  poll for no gain.
- **Don't make `strategies_allowed` a tenant-level allowlist that
  defaults to empty.** The current default `["grid", "random",
  "bayesian"]` is the published baseline. An empty default would
  break the opt-in path: `enabled: true` would land but every run
  would be rejected with `StrategyNotAllowed`.

### References

- `Corvin-ADR: decisions/0013-compute-worker-plugin.md` — the design ADR
- `Corvin-ADR: decisions/0013-implementation-plan.md` — sub-phase fanout
- ADR-0001 — AWP's DelegationLoopRunner pattern (origin)
- ADR-0007 Phase 3.1 — `tenant.corvin.yaml` schema extension
- ADR-0012 — Large-Data Snapshot Layer (hard prerequisite for the
  `data_handle` parameter on `compute_run`)
- Layer 6 (Forge) — `run_tool()` primitive the worker calls into
- Layer 7 (Skill-Forge) — strategy promotion mechanism
- Layer 10 (path-gate) — extended with two new globs
- Layer 11 (`dialectic.py`) — subscription-native LLM template
- L23 (voice-transcribe) — metadata-only-audit precedent
- L22 (`WorkerEngine`) — engine-layer separation this layer does
  NOT cross (tool invocations stay engine-agnostic via Forge)

## Layer 32 — Strict Anonymisation Snapshot Mode (ADR-0023)

Closes the data-locality gap that L24 (Large-Data Snapshot) left open for
privacy-sensitive corpora. L24 already prevents raw CSV/JSON/Parquet
bytes from entering the LLM context; L32 goes one step further: the
**aggregated statistics that do reach the LLM** are themselves anonymised
so that a re-identification attack on the snapshot is structurally
infeasible. The mode is **opt-in per operator** (`strict_anonymization:
true` in `data_policy.yaml`) and default-off for backward compatibility.

The load-bearing user requirement (verbatim): *"Bau mal was ein, was das
noch anonymisiert und strukturell verankert ist, sodass wirklich keine
privaten Daten im Language Model landen."* — really no private data in
the language model.

### Three structural mechanisms

**1. k-Anonymity bucketing of distinct-counts**

Column statistics include a `distinct` count (approximate HyperLogLog).
In a small dataset even an approximate count reveals cardinality: if
`distinct=1` and `rowcount=3` the column is a quasi-identifier. L32
replaces every raw distinct count with a sentinel bucket:

| Bucket string | Raw range (k default = 5) |
|---|---|
| `"unique"` | distinct < k — the privacy-sensitive sentinel |
| `"1-4"` | 1 ≤ distinct < k (alias for k=5 default) |
| `"5+"` | distinct ≥ k and < 10 |
| `"0"` | zero distinct (all-null column) |

The `"unique"` sentinel makes it **mathematically impossible** to
distinguish "1 distinct value" from "4 distinct values" when `k=5` —
preventing count-cardinality fingerprinting. The operator sets
`k_anonymity_threshold` in `data_policy.yaml`; default is 5.

**2. Proportional Laplace noise on rowcount estimates**

The snapshot's `estimated_rowcount` field is noised with
`Laplace(0, scale)` where `scale = rowcount_laplace_scale × max(rowcount, 1)`.
This makes noise **proportional to the order-of-magnitude** of the
dataset:

- 1 M rows, scale_ratio=0.01 → `scale=10 000` → ±~14 000 rows (1.4%)
- 1 K rows, scale_ratio=0.01 → `scale=10` → ±~14 rows (1.4%)
- 5 rows, scale_ratio=0.01 → `scale=1` → ±~1 row (20%)

Absolute noise never dominates small datasets nor becomes negligible on
large ones. Operator sets `rowcount_laplace_scale` in `data_policy.yaml`;
default 1.0 (conservative; production deployments use 0.01–0.1).

**3. Post-projection PII scan (the structural safety net)**

After bucketing replaces stats AND sample rows are cleared to `[]`, a
second pass walks the entire projected snapshot dict recursively and
matches six regex patterns against every string leaf:

| Pattern name | Coverage |
|---|---|
| `email` | RFC-5322 address |
| `iban` | ISO 13616 IBAN (15-34 chars) |
| `credit_card` | Luhn-16-digit PAN |
| `phone_e164` | E.164 international format |
| `us_ssn` | `\d{3}-\d{2}-\d{4}` form |
| `de_steuer_id` | German 11-digit Steuer-ID |

Any match is a **policy violation**. Behaviour on violation:

| `reject_on_pii_leak` | Effect |
|---|---|
| `true` (default) | `ToolError` raised; snapshot NOT delivered to LLM; `data.anonymisation_rejected_pii_leak` (WARNING) audited |
| `false` (advisory) | Snapshot delivered with `pii_leak_detected: true` flag; same audit event at WARNING |

The scan runs AFTER projection (not before) so any redaction-strategy
leak from bucketed statistics is still caught. The regex match count
and class names land in the audit event; the matched values NEVER do.

### Policy configuration (`data_policy.yaml`)

Four new optional fields alongside the existing L24 fields:

```yaml
apiVersion: corvin/v1
kind: DataPolicy
spec:
  # --- Layer 24 fields (unchanged) ---
  pii_backend: regex+headers
  default_strategy: redact
  snapshot_token_cap: 4000

  # --- Layer 32 additions ---
  strict_anonymization: false        # opt-in; default false
  k_anonymity_threshold: 5           # distinct < k → "unique" bucket
  rowcount_laplace_scale: 1.0        # scale_ratio for proportional noise
  reject_on_pii_leak: true           # enforcing (true) vs advisory (false)
```

`_validate()` rejects `k_anonymity_threshold < 2`, `rowcount_laplace_scale
≤ 0`, and non-boolean `reject_on_pii_leak` with `PolicyError` fail-closed.

### Integration points in the MCP pipeline

The strict-anonymisation pass is wired into `mcp_handlers.py` as
`_apply_strict_layer()`, called BEFORE the L24 token-cap gate in both:

- `call_data_register()` — initial snapshot generation
- `call_data_snapshot()` — regenerated snapshots

Pipeline order (per call):

```
L24 snapshot generation (schema + stats + sample)
  ↓
L32 apply_strict_anonymisation()       ← bucket distinct, clear sample, noise rowcount
  ↓
L32 scan_for_pii_leaks()              ← post-scan regex walk
  ↓  (reject or advisory on violation)
L24 token-cap gate                    ← existing behaviour unchanged
  ↓
LLM context
```

When `strict_anonymization=false` (default), `_apply_strict_layer()` is
a no-op and the pipeline is byte-identical to L24.

### Audit-chain events (metadata only, two new types)

Registered in `forge/security_events.py::EVENT_SEVERITY`:

| Event | Severity | Allow-listed fields |
|---|---|---|
| `data.strict_anonymisation_applied` | INFO | `data_handle`, `dropped_keys`, `columns`, `rows_noised`, `k_threshold`, `scale_ratio` |
| `data.anonymisation_rejected_pii_leak` | WARNING | `data_handle`, `pii_class_counts` (dict of class→int), `total_matches`, `reject_on_pii_leak` |

The allow-list in `mcp_handlers.py::_STRICT_AUDIT_ALLOWED_*` enforces
at the boundary — extra keys raise `AuditFieldNotAllowed`. Matched PII
values, sample rows, and snapshot content **NEVER** land in the chain.
The regression gate `test_no_raw_value_in_audit` walks every emitted
event and fails the suite if a raw string value appears in the details.

### Module structure

```
operator/forge/forge/corvin_data/
├── strict_anonymizer.py   # apply_strict_anonymisation() + scan_for_pii_leaks()
├── data_policy.py         # extended with 4 new DataPolicy fields
└── mcp_handlers.py        # _apply_strict_layer() wired into both handlers
```

**`strict_anonymizer.py` public API:**

| Function | Signature | Returns |
|---|---|---|
| `apply_strict_anonymisation` | `(snapshot, policy) → (anonymised_payload, dropped_keys_count)` | Modified snapshot dict + count of dropped keys |
| `scan_for_pii_leaks` | `(data, policy) → (scanned_payload, rejected, match_count, matched_classes)` | Possibly-rejected payload + metadata |
| `_distinct_class` | `(distinct_count, k_threshold)` | Bucket string |
| `_nulls_class` | `(null_count, rowcount)` | Null-percentage bucket |
| `_noised_rowcount` | `(rowcount, scale_ratio)` | Noised integer |
| `_laplace_noise` | `(scale)` | `float` Laplace sample |

### Test surface (`test_corvin_data_strict_anonymizer.py`, 102 assertions)

All 102 assertions green. Key test classes:

| Class | Cases | Coverage |
|---|---|---|
| `BucketingTests` | 12 | `_distinct_class` sentinel ("unique"), edge at k boundary, zero distinct |
| `NullBucketingTests` | 6 | Null-% bucketing: 0%, 1-10%, 11-50%, 51-99%, 100% |
| `LaplaceNoiseTests` | 8 | Scale-ratio math, proportionality, non-negative result clamp, zero-rowcount safe |
| `ProjectionTests` | 14 | Stats replaced with buckets, sample cleared, rowcount noised, dropped_keys counted |
| `PostScanTests` | 18 | Each of 6 PII classes triggers rejection, clean snapshot passes, advisory mode passes |
| `AuditMetadataTests` | 12 | No raw values in audit events, allow-list enforcement, `pii_class_counts` structure |
| `PolicyValidationTests` | 10 | k<2 rejected, scale≤0 rejected, non-bool reject_on_pii_leak rejected |
| `BackwardCompatTests` | 10 | L24 tests (451 assertions) all green when `strict_anonymization=false` |
| `McpHandlerIntegrationTests` | 12 | `data_register` + `data_snapshot` call `_apply_strict_layer`, noop on default policy |

### What you, as Claude Code, must NOT do (Layer 32)

- **Don't accept a "privacy-off mode" env var or CLI flag.** There is
  no `CORVIN_STRICT_ANON=0` escape hatch. The operator sets
  `strict_anonymization: false` in `data_policy.yaml` (the documented
  opt-out); adding a bypass env var turns a deliberate operator policy
  choice into a per-subprocess accident waiting to happen.
- **Don't put matched PII values into any audit-event detail field.**
  The `_STRICT_AUDIT_ALLOWED_*` allow-lists in `mcp_handlers.py`
  enforce it at the boundary. `pii_class_counts` carries `{class: count}`
  only — never the value that matched. The `test_no_raw_value_in_audit`
  regression gate fails the suite if this slips.
- **Don't run the post-scan PII walk BEFORE projection.** The ordering
  (project → post-scan) is load-bearing: the scan validates that
  bucketed stats contain no raw PII after projection. Running the scan
  before projection would miss leaks introduced by the bucketing code
  itself (e.g. a bug that copies a raw value into the `"unique"` bucket
  string).
- **Don't lower `k_anonymity_threshold` below 2.** `k=1` makes the
  "unique" sentinel equivalent to the raw count — defeats the purpose.
  The `_validate()` check is the regression gate.
- **Don't widen the 6 PII regex patterns to free-form input.** Each
  pattern is curated with explicit false-positive testing; extending
  the set requires a regression test proving the false-positive rate
  stays acceptable on clean corpora. New patterns need a `PolicyError`
  on unrecognised `pii_backend` values to surface misconfiguration.
- **Don't bypass `_apply_strict_layer()` by calling
  `apply_strict_anonymisation` directly in any non-test code path.**
  The MCP handler is the only sanctioned write path to the LLM context
  for data snapshots. Direct calls skip the audit-chain emission and
  the reject-on-pii gate, converting a policy violation into a silent
  LLM-context write.
- **Don't make strict mode the default** (`strict_anonymization: true`
  in bundled `data_policy.yaml`). Backward compatibility with L24 is
  the published contract; operators who need L32 opt in explicitly.
  Flipping the default would break every existing forge tool that
  registers data handles without expecting bucketed stats.
- **Don't move the snapshot pipeline outside the path-gated forge
  tree.** The existing L24 rule applies: `DataRegistry` writes to
  `<forge_root>/data/handles/*.json` which is covered by path-gate's
  `forge` rel-part check. L32 does not add new on-disk artefacts;
  the strict-anon pass is in-memory within the MCP handler.
- **Don't accept `rowcount_laplace_scale > 10`.** At scale_ratio=10
  the noise swamps the signal on any dataset under 1M rows — the
  projected rowcount would be useless for the LLM. Cap at 10 in
  `_validate()` if not already present; document it as the maximum
  meaningful value.
- **Don't emit `data.strict_anonymisation_applied` when
  `strict_anonymization=false`.** The event fires only on the active
  code path; a no-op pass must be silent. Emitting on every snapshot
  would saturate the chain on operators who never opted in.

### References

- `Corvin-ADR: decisions/0023-strict-anonymization-snapshot-mode.md` — the ADR
- `operator/forge/forge/corvin_data/strict_anonymizer.py` — core module
- `operator/forge/forge/corvin_data/data_policy.py` — policy schema extension
- `operator/forge/forge/corvin_data/mcp_handlers.py` — MCP pipeline wiring
- `operator/forge/tests/test_corvin_data_strict_anonymizer.py` — 102-assertion E2E
- `operator/forge/forge/security_events.py` — 2 new `data.*` event types
- Layer 24 (L24) — Large-Data Snapshot Layer; L32 is additive on top of it
- GDPR Art. 5(1)(c) — data minimisation (k-anonymity bucketing)
- EU AI Act Art. 15 — accuracy / robustness (post-scan rejection prevents
  incorrect/misleading statistics reaching the model)

---

## DSI v1 — External DataSource Interface Specification (ADR-0106)

DSI v1 adds a **versioned external data-source interface** on top of the existing
ADR-0026 `DataSourceAdapter` Protocol. It introduces class-level identity metadata,
a `BaseDataSourceAdapter` ABC, a `DSIv1ConnectionManifest` validation layer with
L34/L35 compliance gates, a console UI for managing connections, and bridges into
L24 (`data_register`) and L25 (`compute_run`).

> ADR-0026 and the existing 13 builtin adapters are fully preserved — DSI v1 is
> additive. Old manifests without `dsi_version` continue to work unchanged.

### M1 — Protocol finalisation

**`core/compute/corvin_compute/fabric/datasources/protocol.py`**

New additions (backward-compatible):

| Symbol | Kind | Purpose |
|---|---|---|
| `DSIError` | exception | Base for all DSI v1 errors |
| `DSIConnectionError` | exception | Connectivity failures |
| `DSIAuthError` | exception | Authentication failures |
| `DSISchemaError` | exception | Schema introspection failures |
| `DSITimeoutError` | exception | Timeout during ping/connect |
| `PingResult(ok, latency_ms, detail)` | frozen dataclass | Return value of `ping()` |
| `SchemaColumn(name, dtype, nullable, pii_tag, cardinality_class)` | frozen dataclass | Column descriptor |
| `BaseDataSourceAdapter` | ABC | Base class all adapters now inherit |

**`BaseDataSourceAdapter` required class attributes** (enforced by `__init_subclass__`):

```python
class BaseDataSourceAdapter:
    DSI_VERSION: ClassVar[str] = "1"
    adapter_name: ClassVar[str]     # machine ID: "s3_parquet"
    display_name: ClassVar[str]     # human label: "S3 Parquet"
    description: ClassVar[str]      # one-line purpose
    supported_formats: ClassVar[frozenset]
    locality: ClassVar[str]         # "local" or "any"
    network_egress: ClassVar[str]   # "none" or "any"
    config_schema: ClassVar[dict]   # JSON Schema for adapter config

    def ping(self, timeout_s: float = 5.0) -> PingResult:
        return PingResult(ok=True, latency_ms=0.0, detail="no-op")
```

All 13 builtin adapters now inherit from `BaseDataSourceAdapter` and declare the
required class attributes. `locality="local"` / `network_egress="none"` only for
`local_file`; all cloud adapters use `"any"`.

### M2 — ConnectionManifest validation + DataSourceRegistry

**`core/compute/corvin_compute/fabric/datasources/manifest.py`**

`DSIv1ConnectionManifest` dataclass fields:

| Field | Type | Notes |
|---|---|---|
| `dsi_version` | `Literal["1"]` | Distinguishes from ADR-0026 manifests |
| `name` | `str` | `[a-z][a-z0-9_-]{0,63}` |
| `adapter` | `str` | Must match a registered adapter |
| `config` | `dict` | Adapter-specific, not validated here |
| `data_classification` | `str` | `PUBLIC`/`INTERNAL`/`CONFIDENTIAL`/`SECRET` |
| `secrets` | `list[str]` | Env-var names only — never values |
| `data_residency` | `str` | `any`/`eu`/`de`/`us`/`local` |
| `tags` | `list[str]` | Optional operator tags |
| `pii_scan` | `bool` | Default `True` |
| `read_only` | `bool` | Default `True` |
| `auto_refresh_schema` | `bool` | Default `False` |
| `snapshot_options` | `SnapshotOptions \| None` | Optional snapshot tuning |
| `description` | `str \| None` | One-line human description |

`is_dsiv1_manifest(raw)` — returns `True` if `dsi_version == "1"`, used to
distinguish DSI v1 from legacy ADR-0026 manifests.

`validate_dsiv1_manifest(raw)` — validates and returns `DSIv1ConnectionManifest`;
raises `DSIv1PolicyError(ValueError)` on constraint violations.

**`core/compute/corvin_compute/fabric/datasources/registry.py`**

Five new functions alongside the existing ADR-0026 surface:

| Function | Purpose |
|---|---|
| `register(manifest_dict, tenant_id, *, audit_writer, _l34_guard, _l35_gate)` | Validate manifest → optional L34/L35 gate checks → **audit-first** `datasource.registered` → write 0600 JSON file |
| `unregister(name, tenant_id, *, audit_writer)` | Audit-first `datasource.unregistered` → delete manifest file |
| `test_connection(name, tenant_id, *, timeout_s, audit_writer)` | Load manifest → instantiate adapter → call `ping()` → emit `datasource.connection_tested` |
| `describe_adapter(adapter_name, tenant_id)` | Return DSI v1 class-level metadata as `dict` (no instance created) |
| `list_connections_v1(tenant_id)` | Return all DSI v1 manifests (`dsi_version=="1"`) as list of dicts |

**Audit-first invariant:** `datasource.registered` and `datasource.unregistered`
are written to the L16 hash chain **before** any filesystem mutation.

**L34/L35 integration:** `register()` accepts `_l34_guard` and `_l35_gate` as
optional keyword arguments. The console route injects real guards; tests pass
`None` to skip.

`AuditWriter = Callable[[str, str, dict[str, Any]], None]` — `(event_type, severity, details)`.

### M3 — Console Data Sources page

**Backend: `core/console/corvin_console/routes/data_sources.py`**

Seven endpoints mounted at `/v1/console/data-sources`:

| Method | Path | CSRF | Purpose |
|---|---|---|---|
| `GET` | `/data-sources/adapters` | No | List all builtin DSI v1 adapters with class-level metadata |
| `GET` | `/data-sources` | No | List all registered DSI v1 connections for the session's tenant |
| `POST` | `/data-sources` | Yes | Register a new DSI v1 connection (`RegisterRequest` body) |
| `GET` | `/data-sources/{name}` | No | Get a single connection with `adapter_meta` attached |
| `POST` | `/data-sources/{name}/test` | Yes | Ping the connection; returns `DSIPingResult` |
| `DELETE` | `/data-sources/{name}` | Yes | Unregister a connection (returns 204) |
| `GET` | `/data-sources/{name}/audit` | No | Last N audit events for this connection name |

`_make_ds_audit_writer(tenant_id)` builds an `AuditWriter` from the forge audit
chain. Returns a no-op writer if the forge import fails (best-effort observability).

`_get_registry()` lazy-imports `DataSourceRegistry`; returns a 503 `ImportError`
response if the compute plugin is not installed.

**Frontend: `core/console/corvin_console/web-next/src/pages/data-sources.tsx`**

React page at `/app/data-sources` with:

- Connection list with `data_classification` badge, adapter badge, data-residency badge
- Filter by name / adapter / description
- **Register modal** — adapter picker (from `GET /adapters`) → adapter-specific config form → classification / residency / secrets form → POST
- **Detail dialog** — adapter metadata table, connectivity test button + result, recent audit events list
- Inline unregister with confirmation prompt

**Routing:** `lazy-pages.ts` + `App.tsx` → `<Route path="data-sources" element={<DataSourcesPage />} />`

### M4 — DSI Bridge into L24 (`data_register`)

**`operator/forge/forge/corvin_data/mcp_handlers.py`**

`data_register` now accepts two mutually-exclusive input paths:

| Parameter | Old behaviour | New behaviour |
|---|---|---|
| `path` | Required | Optional (one of `path` / `connection` required) |
| `connection` | — | Optional DSI v1 connection name |

When `connection=` is provided `_call_data_register_connection()` runs:
1. Loads the DSI v1 manifest (raises if not found or not DSI v1)
2. Calls `describe_adapter()` for class-level metadata
3. Builds a `snapshot_token` text block (adapter name, locality, classification,
   config schema shape, description) — the LLM-facing context injection
4. Mints a data handle (`data_<22>`) with path `dsi://<name>`
5. Returns `{data_handle, snapshot, oversized: false}`

The snapshot payload carries adapter metadata only — no connection config values,
no secrets, no raw data. GDPR Art. 5 (data minimisation) applies.

### M5 — DSI Bridge into L25 (`compute_run`)

**`core/compute/corvin_compute/mcp_bridge.py`**

`compute_run` schema extended with:

```json
"datasources": {
  "type": "array",
  "items": { "type": "string" },
  "description": "List of DSI v1 connection names to expose to the compute worker."
}
```

The `datasources` parameter is accepted and stored in the run manifest.
Vault credential injection at bwrap spawn time is a future milestone — the
`datasources` list is the declared interface surface for that work.

### Audit events (DSI v1 — 3 new types)

| Event | Severity | Allow-listed fields |
|---|---|---|
| `datasource.registered` | INFO | `name`, `adapter`, `data_classification`, `data_residency`, `tenant_id` |
| `datasource.unregistered` | INFO | `name`, `tenant_id` |
| `datasource.connection_tested` | INFO | `name`, `adapter`, `ok`, `latency_ms`, `tenant_id` |

Connection config, secrets, and raw error details never appear in audit fields.

### What you, as Claude Code, must NOT do (DSI v1)

- **Don't put secret values into `DSIv1ConnectionManifest.secrets`.** The field
  holds env-var names only (e.g. `"AWS_SECRET_ACCESS_KEY"`) — never the actual
  secret value. This is the same vault/bwrap pattern as L16 v3.
- **Don't write the manifest file before the audit event.** Audit-first is a
  load-bearing invariant for `register()` and `unregister()`.
- **Don't store connection config values in audit event details.**  Allow-listed
  fields are `name`, `adapter`, `data_classification`, `data_residency`,
  `tenant_id` — nothing from `config`.
- **Don't call `connect()` in the L24 bridge (`_call_data_register_connection`).**
  The method requires vault-injected credentials inside bwrap; the bridge only
  reads class-level metadata and builds a schema description for LLM context.
- **Don't skip the `is_dsiv1_manifest()` check when reading manifests.** Old
  ADR-0026 manifests (no `dsi_version` field) must not be passed to DSI v1 paths.
- **Don't bypass `validate_dsiv1_manifest()` on registration.** The
  `DSIv1PolicyError` is the structural defence against misclassified or
  malformed manifests reaching the L34/L35 gate.
- **Don't use positional arguments for `tenant_id` in registry functions.**
  keyword-only per ADR-0007.

### References

- `Corvin-ADR: decisions/0106-dsi-v1-external-datasource-interface.md` — the ADR
- `core/compute/corvin_compute/fabric/datasources/protocol.py` — M1 protocol
- `core/compute/corvin_compute/fabric/datasources/manifest.py` — M2 manifest validation
- `core/compute/corvin_compute/fabric/datasources/registry.py` — M2 registry functions
- `core/compute/corvin_compute/fabric/datasources/builtin/` — 13 builtin adapters (all updated)
- `core/console/corvin_console/routes/data_sources.py` — M3 backend (7 endpoints)
- `core/console/corvin_console/web-next/src/pages/data-sources.tsx` — M3 frontend
- `operator/forge/forge/corvin_data/mcp_handlers.py` — M4 `data_register(connection=)` bridge
- `core/compute/corvin_compute/mcp_bridge.py` — M5 `compute_run(datasources=)` schema extension
- Layer 24 (L24) — `data_register` MCP tool extended by M4
- Layer 25 (L25) — `compute_run` schema extended by M5
- Layer 34 (L34) — data classification gate injected into `registry.register()`
- Layer 35 (L35) — egress gate injected into `registry.register()`
- Layer 16 (L16) — secret-vault pattern; `secrets` field holds env-var names only

