# Corvin Compliance Layer

## Overview

The **Compliance Layer** (`corvin_compliance_reports`) is a regulator-defensible PDF report generator that produces transparency artefacts from the Corvin audit chain.

**Core principle:** Transparency is a structural feature, never gated by commercial features. All three baseline reports ship FREE for every operator.

### Baseline Reports (Phase II)

1. **EU AI Act Article 50** — Active-Disclosure Evidence Report
   - Demonstrates compliance with transparency obligations
   - AI system documentation, risk assessment, human oversight
   - Event-driven, tenant-specific, time-windowed

2. **GDPR Article 30** — Records of Processing Activities (RoPA)
   - Data subject rights, retention, cross-border transfers
   - Lawful basis documentation, processing audit trail
   - Event-driven, tenant-specific, time-windowed

3. **Audit-Chain Integrity Attestation**
   - Cryptographic proof of unbroken audit chain
   - Anchor hash, chain integrity validation
   - Detects tampering or event loss

**Enterprise Phase V** (separate repo) adds premium variants: scheduled generation, custom templates, WORM archival, breach notification workflows. Baseline artefacts remain open-source and license-agnostic.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Corvin Core Runtime                  │
│  (audit chain: License, Console, Compute, Gateway, etc.)    │
└────────────┬────────────────────────────────────────────────┘
             │
             │ [audit events]
             │
    ┌────────▼────────────────────────────────────────┐
    │      Compliance Layer (corvin_compliance)      │
    │                                                  │
    │  ┌─ CLI Entry Point ─────────────────────────┐  │
    │  │ $ python -m corvin_compliance_reports... │  │
    │  └────────────────────┬──────────────────────┘  │
    │                       │                          │
    │  ┌────────────────────┴──────────────────────┐  │
    │  │     Report Generator Dispatch             │  │
    │  │  (AI Act 50 | GDPR 30 | Audit Attestation)  │
    │  └────────────────────┬──────────────────────┘  │
    │                       │                          │
    │  ┌────────────────────┴──────────────────────┐  │
    │  │   Audit Query Subsystem                  │  │
    │  │  • Time-window filtering (start_ts/end_ts)  │
    │  │  • Tenant isolation (_default or custom) │  │
    │  │  • Event extraction & validation         │  │
    │  └────────────────────┬──────────────────────┘  │
    │                       │                          │
    │  ┌────────────────────┴──────────────────────┐  │
    │  │   Report Template Engine                 │  │
    │  │  • Markdown → PDF rendering              │  │
    │  │  • Metadata injection (anchor_hash, etc) │  │
    │  │  • Page estimation                       │  │
    │  └────────────────────┬──────────────────────┘  │
    │                       │                          │
    │  ┌────────────────────┴──────────────────────┐  │
    │  │   Audit Emitter (always best-effort)     │  │
    │  │  • Logs: report_generated / failed       │  │
    │  │  • Allow-list enforced (no PII/secrets)  │  │
    │  │  • Never blocks PDF delivery             │  │
    │  └────────────────────────────────────────────┘  │
    └────────────────────────────────────────────────┘
             │
             │ [PDF output]
             │
    ┌────────▼─────────────────────┐
    │  Operator Artifact Store      │
    │  (tenant-{type}-{epoch}.pdf)  │
    └───────────────────────────────┘
```

---

## Core Components

### 1. CLI Interface (`cli.py`)

**Entry point for all report generation.**

```bash
# List available reports
python -m corvin_compliance_reports.cli list

# Generate AI Act evidence (last 30 days, default tenant)
python -m corvin_compliance_reports.cli generate ai-act-50 --since 30d

# Generate GDPR RoPA for custom tenant, last quarter
python -m corvin_compliance_reports.cli generate gdpr-30 \
    --tenant acme \
    --since 90d \
    --output /tmp/acme-ropa.pdf

# Generate audit attestation (silent mode)
python -m corvin_compliance_reports.cli generate audit-attestation \
    --quiet
```

**Arguments:**
- `report_type` — One of: `ai-act-50`, `gdpr-30`, `audit-attestation`
- `--tenant ID` — Tenant isolation (default: `_default`)
- `--since DUR` — Window start (e.g., `7d`, `30d`, `90d`, `12h`)
- `--until DUR` — Window end, before now (default: `0` = now)
- `--output PATH` — PDF destination (auto-named if omitted)
- `--quiet` — Suppress progress; only print path

**Duration parsing:** `30d` → 30 × 86400 seconds; supports `s`, `m`, `h`, `d`, `w`.

### 2. Audit Query (`audit_query.py`)

**Abstracts audit chain access for filtering and extraction.**

- **Time-window filtering:** Extracts events within `[start_ts, end_ts)`
- **Tenant isolation:** Filters by tenant_id at query boundary
- **Event deduplication:** Validates chain integrity during extraction
- **Pagination support:** For large event sets

Used by all three report generators to fetch and validate events.

### 3. Report Generators

#### AI Act Evidence (`ai_act_evidence.py`)
```python
ai_act_evidence.generate(
    tenant_id="...",
    start_ts=...,
    end_ts=...,
    output_path=Path("report.pdf"),
)
```

Produces evidence for Article 50 transparency obligations:
- AI system purpose and risk classification
- Human oversight and intervention logs
- Training data provenance (if applicable)
- Decision explanation and audit trail
- User rights & complaints handling

#### GDPR RoPA (`gdpr_ropa.py`)
```python
gdpr_ropa.generate(
    tenant_id="...",
    start_ts=...,
    end_ts=...,
    output_path=Path("report.pdf"),
)
```

Records of Processing Activities per Article 30:
- Controller / Processor identification
- Processing categories and lawful basis
- Data subject types and retention periods
- International transfers
- Security measures and DPA references
- Sub-processor log

#### Audit Attestation (`audit_attestation.py`)
```python
audit_attestation.generate(
    tenant_id="...",
    start_ts=...,
    end_ts=...,
    output_path=Path("report.pdf"),
)
```

Cryptographic integrity proof:
- Event count and anchor hash
- Chain continuity validation
- Timestamp attestation
- Tamper detection evidence
- Formal attestation statement

### 4. Template Engine (`templates.py`)

**Markdown → PDF rendering with metadata injection.**

- Jinja2-based template evaluation
- Page estimation (heuristic)
- Metadata encoding (anchor_hash, chain_intact, etc.)
- Standard PDF metadata (title, author, creation date)
- Support for embedded images and tables

All templates are Unicode-safe and include:
- Report type and tenant identification
- Time window and generation timestamp
- Operator and Corvin version info
- Audit chain anchor hash (if applicable)

### 5. Audit Emitter (`audit.py`)

**Best-effort compliance event logging — never blocks report delivery.**

#### Allowed Event Fields

```
compliance.report_generated  (INFO)
├─ report_type            (ai_act_art_50, gdpr_art_30_ropa, audit_chain_attestation)
├─ tenant_id              (string)
├─ period_start_ts        (unix epoch)
├─ period_end_ts          (unix epoch)
├─ total_events           (count)
├─ chain_intact           (boolean)
├─ anchor_hash            (string, if attestation)
└─ page_count_estimate    (integer)

compliance.report_failed  (WARNING)
├─ report_type            (string)
├─ tenant_id              (string)
├─ reason                 (render-error, chain-invalid, etc.)
├─ period_start_ts        (unix epoch)
└─ period_end_ts          (unix epoch)
```

#### Forbidden Fields (Never Logged)
- `report_body`, `pdf_bytes`, `raw_events` — Body confidentiality
- `customer_id`, `token` — Align with license audit rules
- Any PII, secrets, or full report paths

**Rationale:** Audit events are for compliance visibility (which reports were generated when), not for storing sensitive data. Report content stays in the PDF, not in logs.

---

## Data Flow Diagram

```
          Operator Trigger
                 │
                 ▼
        ┌────────────────┐
        │   CLI Parser   │
        │ (validate args)│
        └────────┬───────┘
                 │
                 ▼
    ┌───────────────────────┐
    │ Dispatch to Generator │
    │  • ai_act_evidence    │
    │  • gdpr_ropa          │
    │  • audit_attestation  │
    └────────────┬──────────┘
                 │
                 ▼
    ┌───────────────────────┐
    │  Audit Query Module   │
    │ • Fetch events        │
    │ • Filter by [t0, t1]  │
    │ • Validate chain      │
    │ • Tenant isolation    │
    └────────────┬──────────┘
                 │
                 ▼
    ┌───────────────────────┐
    │  Template Rendering   │
    │ • Build Markdown      │
    │ • Encode metadata     │
    │ • Estimate pages      │
    │ • Render to PDF       │
    └────────────┬──────────┘
                 │
                 ▼
    ┌───────────────────────┐
    │   Audit Emitter       │
    │ • Log report_generated│
    │ • (or report_failed)  │
    │ • Best-effort only    │
    │ • Never blocks output │
    └────────────┬──────────┘
                 │
                 ▼
        ┌────────────────┐
        │  Write PDF     │
        │  Return path   │
        └────────────────┘
```

---

## Testing

Unit tests cover:
- CLI argument parsing and duration parsing (`test_cli.py`)
- Audit query filtering and event extraction (`test_audit_query.py`)
- Report generation end-to-end (`test_generators.py`)
- Audit event emission (`test_audit_emitter.py`)

**Run tests:**
```bash
cd core/compliance
python -m pytest -xvs tests/
```

---

## Compliance & Licensing

**Baseline Rule:**
> Don't gate compliance-report generation on any commercial feature flag.  
> Transparency is a structural feature.

- All three baseline reports are **Apache-2.0 licensed** and ship free
- No feature-gating on trial/free/paid tiers
- Operators can always generate and share reports with regulators
- Enterprise variants (Phase V) add premium scheduling, custom templates, and WORM archival
- Baseline artefacts remain immutable in this repository

---

## Integration Points

### Audit Chain Access
- Reads from the **forge** security_events system
- Requires `audit_query` permissions in operator config
- Respects tenant-id isolation boundary

### License System
- Does not gate report generation
- May read license metadata for documentation only
- Events emitted to compliance audit stream, not license audit stream

### Gateway & Console
- Called by operator CLI or enterprise scheduler
- Results stored in tenant artifact directories
- May be integrated into web UI (Phase V)

---

## Troubleshooting

### Report generation fails with "render-error"
- Check audit chain availability and permissions
- Verify tenant_id exists
- Ensure time window contains events

### Audit emission warnings
- Non-critical; report PDF always succeeds
- Check forge security_events module connectivity
- Verify audit allow-list hasn't been modified

### Chain integrity validation fails
- Audit chain may be corrupted or partially missing
- Check storage backend health
- Manual chain recovery via forge audit tooling

---

## Future (Phase V)

Enterprise plugin adds:
- **Scheduled generation** — cron-like report runs
- **Custom templates** — Jinja2 template library + library management
- **WORM archival** — immutable report storage
- **Breach notification** — automated regulator alerts
- **Advanced scheduling** — complex time windows, multi-tenant batching

Baseline always remains free and license-agnostic.
