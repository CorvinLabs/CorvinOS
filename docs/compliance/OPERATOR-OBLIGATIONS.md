# Operator Obligations — EU AI Act Art. 28-30

**Applies to:** Any entity deploying Corvin in a production environment.  
**Basis:** EU AI Act 2026 Art. 28-30.

---

## Permitted Use Cases

Corvin may be deployed for any of the following without additional review:

- Internal staff coding assistance and code review
- Internal knowledge management and document Q&A
- Customer support automation with disclosed AI nature (/join disclosure)
- Research assistance and information retrieval
- Creative writing assistance

---

## Prohibited Use (without Architecture Review + DPO Approval)

The following use cases require explicit Architecture Review and DPO approval
before deployment, because they may trigger Annex III High-Risk classification:

| Use case | Risk | Required action |
|---|---|---|
| Automated CV/resume screening | Annex III §3 | Architecture Review + Notified Body |
| Automated credit or loan eligibility | Annex III §5 | Architecture Review + Notified Body |
| Automated benefit or welfare entitlement | Annex III §5 | Architecture Review + Notified Body |
| Law enforcement or recidivism prediction | Annex III §7 | Architecture Review + Notified Body |
| Biometric identification of natural persons | Art. 5(3) | Prohibited without explicit authorization |
| Influence/manipulation campaigns targeting behavior | Art. 5(2) | Prohibited |

---

## Pre-Deployment Checklist (eu_production Profile)

Before going live with `deployment_profile: eu_production`:

- [ ] **DPIA completed** — Use `docs/compliance/DPIA-TEMPLATE.md`. Record date in `dpia_date`.
- [ ] **DPO review** — Your DPO or legal team signs off on the DPIA and this checklist.
- [ ] **Permitted use confirmed** — The deployment falls under Permitted Use above.
- [ ] **operator_declaration** filled in `tenant.corvin.yaml::spec.operator_declaration`.
- [ ] **bridge.sh doctor** — Run and confirm all CRITICAL checks pass.
- [ ] **Annex IV** — Generate, review, and sign `corvin-annex-iv generate`.
- [ ] **Privacy Notice** — Deploy `docs/compliance/PRIVACY-NOTICE-TEMPLATE.md` for users.
- [ ] **Incident response** — DPO knows the `corvin-incident` CLI and Art. 73 15-day window.

---

## `tenant.corvin.yaml` Declaration Block

Add this to your `spec:` block before eu_production deployment:

```yaml
spec:
  deployment_profile: eu_production   # or eu_production_ollama

  operator_declaration:
    version: "1.0"
    declared_by: "Jane Doe, DPO"         # stored locally; never in audit chain
    declared_at: "2026-07-01T09:00:00Z"
    permitted_use: "internal-coding-assistant"
    dpia_completed: true
    dpia_date: "2026-06-15"

  # Pin the compliance manifest version for eu_production
  # Run `cat compliance/manifest-version.txt` to get the current version.
  compliance_manifest:
    min_version: "1.0.0"
```

Corvin will refuse to start in `eu_production` mode without a complete
declaration. `bridge.sh doctor` reports CRITICAL for missing/incomplete
declaration and WARNING when `compliance_manifest.min_version` is not set.

---

## Ongoing Obligations

| Obligation | Frequency | Tool |
|---|---|---|
| Monitor for serious incidents | Continuous (auto) | L39 IncidentAutoDetector |
| Review open incidents | Weekly | `corvin-incident list --status open` |
| Notify supervisory authority for serious incidents | Within 15 days of detection | `corvin-incident notify-draft` |
| Re-run DPIA on significant configuration change | Per change | docs/compliance/DPIA-TEMPLATE.md |
| Audit chain verify | Daily (automated) | `corvin-audit-verify.timer` |
| Incident scan (consent-bypass, disclosure-failure, PII) | Daily (automated) | `corvin-incident-scan.timer` |
| Compliance manifest check | On every boot | `bridge.sh doctor` |
