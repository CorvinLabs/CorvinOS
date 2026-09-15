# RAG Integration — Layer 43 Implementation

**Status:** Phase 1 — Manifest Spec & Validation Framework (ACTIVE)

This directory contains the implementation of ADR-0089: Multi-Provider RAG Integration with Manifest-Based Registry.

## Structure

```
rag-integration/
├── schemas/
│   └── rag-provider-manifest.schema.json    # JSON Schema v7 for manifests
├── validator/
│   └── manifest_validator.py                # Validation framework + CLI
├── examples/
│   ├── elasticsearch-docs.yaml              # ✅ Valid example
│   └── invalid-example.yaml                 # ❌ Invalid example (for testing)
├── tests/
│   └── test_manifest_validator.py           # Test suite
└── README.md                                 # This file
```

## Phase 1: Manifest Spec & Validation

### What's Implemented

✅ **JSON Schema** (`rag-provider-manifest.schema.json`)
- Complete OpenAPI v3-compatible schema
- Covers all RAG provider types (http-api, grpc, kafka, file-search)
- Validates auth, classification, zone-gates, erasure handlers

✅ **Validator Framework** (`manifest_validator.py`)
- YAML/JSON file parsing
- JSON Schema validation
- Compliance checks:
  - No hardcoded secrets (pattern detection)
  - Zone gate consistency
  - Auth configuration validation
  - Data classification rules
  - GDPR erasure handler presence
- CLI: `python -m validator.manifest_validator <file.yaml>`

✅ **Test Suite** (`test_manifest_validator.py`)
- 20+ unit tests covering schema + compliance
- Run: `pytest tests/`

✅ **Examples**
- `elasticsearch-docs.yaml` — Valid, fully-commented example
- `invalid-example.yaml` — Multiple violations for testing

## Usage

### Validate a Manifest (Phase 1)

```bash
# From this directory:
python validator/manifest_validator.py examples/elasticsearch-docs.yaml

# Output:
# ✅ VALID — Provider: elasticsearch-docs
```

```bash
# Invalid manifest:
python validator/manifest_validator.py examples/invalid-example.yaml

# Output:
# ❌ INVALID
# Errors:
#   • token_env_var 'invalid-var-name' invalid. Must be uppercase...
#   • compliance_zone.required=true but allowed_regions is empty...
# Warnings:
#   ⚠️  data_type=CONFIDENTIAL should set requires_approval=true...
#   ⚠️  No erasure_handler defined...
```

### Running Tests

```bash
pip install jsonschema pyyaml pytest
pytest tests/ -v
```

Expected: 20+ tests pass ✅

## Phase 1 Deliverables Checklist

- [x] JSON Schema v1.0 (rag.corvin.io/v1)
- [x] Validator framework (YAML/JSON parsing)
- [x] Compliance checks:
  - [x] No hardcoded secrets
  - [x] Auth token env-var format
  - [x] Zone gate consistency
  - [x] Classification requirements
  - [x] Erasure handler presence (GDPR)
  - [x] Response schema validation
- [x] Test suite (20+ tests)
- [x] Examples (valid + invalid)
- [x] CLI tool

## Next Phases

**Phase 2 (Jul 2026):** Registry + CLI
- Registry storage structure
- `corvin-rag register/list/show/delete` commands
- Health-check gate

**Phase 3 (Aug 2026):** Query API
- RAG Orchestrator
- Parallel multi-provider queries
- Result ranking & caching

**Phase 4 (Sep 2026):** Console UI
- `/app/rag` page
- Manifest upload
- Query tester

**Phase 5 (Sep 2026):** Compliance Gates
- Layer 34 data classification
- Layer 36 GDPR erasure
- Layer 16 audit events

## Files to Integrate

Once Phase 1 is complete:

1. Add to `operator/bridges/shared/`:
   - `rag_orchestrator.py` (Phase 3)
   - `rag_registry.py` (Phase 2)

2. Add to `core/console/corvin_console/routes/`:
   - `rag.py` (Phase 4)

3. Add to `core/console/corvin_console/web-next/src/pages/`:
   - `rag.tsx` (Phase 4)

4. Add to CLI:
   - `operator/bin/corvin-rag` (Phase 2)

## Compliance Notes

All manifests are validated against:

- ✅ **GDPR Art. 17** (Right to Forgotten) — erasure_handler required
- ✅ **EU AI Act Art. 14** (Data Residency) — compliance_zone enforcement
- ✅ **EU AI Act Art. 13** (Transparency) — provider metadata logged
- ✅ **Layer 10** (Path-Gate) — no hardcoded secrets
- ✅ **Layer 34** (Data Classification) — data_type enforced
- ✅ **Layer 36** (GDPR Erasure) — erasure handler declares support

## References

- ADR-0089: Layer 43 RAG Integration Manifests
- `rag-provider-manifest.schema.json` — Complete schema definition
- Examples: `examples/elasticsearch-docs.yaml`

---

**Implementation started:** 2026-06-04  
**Phase 1 ETA:** 2026-06-30  
**Phase 1 Status:** IN PROGRESS
