# Forge Bundle — sharing forged artifacts as one ZIP (ADR-2229)

**Source:** `core/forge_bundle/` · **Tests:** `tests/forge_bundle/` · **ADR:** `corvin_decisions/decisions/ADR-2229-*.md`

A Forge Bundle carries Skills, Tools, Layer definitions and Plugins from one
install to another. It is an **envelope**, not a new artifact format: every
artifact keeps its own forge's serialization, and importing one only
**proposes**: each artifact enters through its own forge's existing intake, so
it passes the same gates as a locally forged one.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Envelope schema + fail-closed validator | **built** — pure library, **no entry point yet** (no route, no CLI) |
| 2 | Export (console route + CLI, reuses `SkillPackager`) | not built |
| 3 | Import → per-forge intake, incl. a new tool quarantine | not built |
| 4 | Console UI in the Forge panel | not built |

## Archive layout

```
forge-bundle.json                                   envelope (the only root file)
artifacts/skill/<id>@<version>/<id>-<version>.zip   ADR-0674 skill ZIP
artifacts/tool/<id>@<version>/spec.json + impl      Tool Forge spec + one impl file
artifacts/layer/<id>@<version>/manifest.json        ADR-2222 layer manifest
artifacts/plugin/<id>@<version>/plugin.zip          ADR-0511 plugin ZIP
```

## Envelope (`format_version: 1`)

```json
{
  "format": "corvin.forge-bundle",
  "format_version": 1,
  "id": "acme-automation",
  "version": "2.0.0",
  "created_at": "2026-10-06T12:00:00Z",
  "description": "optional, ≤ 2000 chars",
  "artifacts": [
    {
      "kind": "tool", "id": "csv.count", "version": "0.2.0",
      "files": [{"path": "artifacts/tool/csv.count@0.2.0/impl.py", "sha256": "<64 hex>", "size": 123}],
      "requires": [{"kind": "skill", "id": "summarize", "version": "1.0.0"}]
    }
  ]
}
```

Strict parsing: unknown keys are refused. Ids use the ADR-0674 safe-segment
alphabet (`^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$`, no `..`), versions are semver,
`kind` ∈ `skill|tool|layer|plugin`, one version per `(kind, id)`. A requirement
without `version` accepts any version.

## Validation — `validate_bundle(data: bytes, *, known=None) -> BundleReport`

Six stages, in order, fail-closed. The first defect raises
`BundleRejected(stage, reason)`; nothing is extracted or written either way.

| Stage | Rejects |
|---|---|
| `container` | unreadable ZIP · > 50 MiB compressed / > 200 MiB uncompressed / > 2 000 entries · absolute, `..`, empty-segment, backslash, `:` or control-char names · symlink and encrypted entries · names equal up to case (Windows/macOS collisions) · compression ratio > 100:1 on files ≥ 1 MiB · any file outside `artifacts/` except the envelope |
| `envelope` | missing / > 1 MiB / non-UTF-8-JSON envelope · any schema defect above |
| `integrity` | a declared file missing, an undeclared file present, size or sha256 mismatch, CRC failure |
| `references` | an in-bundle requirement on the wrong version · any dependency cycle (iterative DFS) |
| `staleness` | (only with `known`) a requirement outside the bundle that the target does not have |
| `secrets` | a credential-shaped string (private key, AWS/GitHub/Slack/Google/`sk-` keys, JWT) in the envelope or any UTF-8 payload, including inside nested `.zip` payloads, which get the same container rules · a `.zip` nested two levels deep |

`known` is the target install's inventory, `{kind: {id: versions}}`, injected
by the caller so the validator stays pure. Without it, requirements that point
outside the bundle come back in `report.unchecked_references`. An importer must
pass `known`.

The secret stage reuses `core.pii.sensitive.detect_sensitive_types` (fail-closed: a
scan error rejects) but only its **credential** detectors. Its prose detectors
(names, addresses, @-handles, entropy, `token = …`) fire on ordinary source code
and on every sha256 in the envelope. A rejection names the detector, never the
matched value. Non-UTF-8 payloads are not scanned and are listed in
`report.unscanned_files`; they are never reported as clean.

`report.origin_verified` is always `False`: format v1 carries no origin proof.
Checksums prove the bundle was not altered in transit, not who made it. Any UI
must show "unverified origin".

## Never in a bundle

Audit records, tenant ids, registry status (`proposed`/`accepted`/promoted),
call counts, operator config, secrets. Everything imported starts in its forge's
entry state.

## Relation to the other ZIP formats in the tree

`core/skills/skill_packager.py` (ADR-0674, one skill) and `core/plugins/staging.py`
(ADR-0511, one plugin) are reused as payload formats / intakes. `core/package_manager/`
+ `routes/packages.py` (its docstring cites ADR-0268, which is *task-engine-parameters*
in this corpus; it sits behind the default-off flag `package_marketplace_ui`) and
`core/awpkg/` (workflow packages) are separate, and this ADR does not reconcile them.
