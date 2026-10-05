# Layer Forge — configuration generation for architectural layers (ADR-2222)

**Source:** `core/orchestration/layer_forge/` · **CLI entry point:** `scripts/layer_forge_cli.py`
**Tests:** `tests/layer_forge/`

Layer Forge is the fourth Forge system (alongside Skill/Tool/Plugin Forge,
ADR-2217), generating versioned *configuration* artifacts — Layer-Definitions,
Quality-Gates, Enforcement-Rules — instead of executable code. It makes layer
boundaries (L1–L44) testable and versionable instead of prose-only.

## MVP scope (what is built, ADR-2222)

- **Deterministic plan phase.** The caller supplies a fully-formed manifest —
  no LLM call in the MVP. LLM-driven planning (matching Skill-Creator's
  `claude -p` phase) is deferred to a follow-up ADR.
- **Five-phase pipeline:** VALIDATE (schema + dependency DAG) → TEST (quality
  gates, run as real pytest subprocesses) → ENFORCE (host-awareness) → AUDIT
  (local JSONL) → PROMOTE (proposed → accepted, through `LayerPrimitive`).
- **Host-awareness.** A manifest can declare `source_tree`/`runtime` paths plus
  a `cross_check`. On a repo-only checkout (no `/opt/corvin` runtime install)
  the runtime half is reported `SKIPPED` with reason `skipped_no_runtime_host`
  — never silently treated as a pass.
- **Race-free primitive.** `LayerPrimitive` is the only writer of per-layer
  state; it holds one stdlib-only file lock (`os.O_CREAT|O_EXCL`) per layer id
  and writes via temp-file + `os.replace` (atomic rename). No caller
  implements its own locking.

## Explicitly deferred (named, not hidden — ADR-2222 Consequences)

- No LLM-driven planning phase.
- No compile-time Mypy import-boundary enforcement (only host-awareness +
  schema checks ship in the MVP).
- No console routes / UI (the CLI is the entry point a future console route
  would call; see ADR-2217's pattern for how Skill/Tool/Plugin Forge wired
  theirs).
- No production hash-chained audit integration — the MVP writes a local
  `layer_forge_audit.jsonl` under the tenant root, not the
  `EVENT_SEVERITY`/`_EVENT_ALLOWLIST` system.

## Call graph (verified 2026-10-05, `grep` refutation round)

```
scripts/layer_forge_cli.py
  └─ LayerForgeOrchestrator.create_layer_definition()
       ├─ LayerRegistry.create()        (schema + DAG validation, writes 'proposed')
       ├─ QualityGateRunner.run_all_gates()   (subprocess pytest per gate)
       ├─ EnforcementChecker.check_host_awareness()
       ├─ LayerPrimitive.write_state()   (race-free promotion marker)
       └─ LayerRegistry.promote()        ('proposed' -> 'accepted')
```

The CLI itself has no production caller yet — this is the named, not hidden,
scope boundary above (console-integration follow-up ADR).

## Manifest shape

```json
{
  "id": "acme.audit-first-l34",
  "version": "0.1.0",
  "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
  "dependencies": [{"id": "base.rule", "type": "layer_definition"}],
  "quality_gates": [
    {"gate_id": "audit-first-check", "test_path": "tests/security/test_l34_audit_first.py"}
  ],
  "enforcement_rules": [
    {"rule_id": "no-l34-to-l5", "type": "compile_time", "description": "L34 must not call L5 directly"}
  ],
  "host_awareness": {
    "source_tree": {"paths": ["core/orchestration/layers/l34/enforce.py"]},
    "runtime": {"paths": ["core/orchestration/layers/l34/enforce.py"]},
    "cross_check": "sha256_match_or_fail"
  }
}
```

## CLI

```bash
python scripts/layer_forge_cli.py create manifest.json [--tenant-root PATH] [--skip-gates]
python scripts/layer_forge_cli.py get <id> [--tenant-root PATH]
python scripts/layer_forge_cli.py promote <id> <version> <to_status> [--tenant-root PATH]
```

Exit code `0` on `SUCCESS`, `1` on `FAILED` (fail-closed — nothing is written
to the registry on a validation, gate, or enforcement failure).
