# Compliance Manifest Changelog

Entries record which legal change triggered a manifest update.
Format: `vMAJOR.MINOR.PATCH — YYYY-MM-DD — <regulation> <OJ/amendment ref>`

Versioning rules:
- `PATCH` — editorial fix, no behavioral change
- `MINOR` — new rule added or existing rule tightened (backward compatible)
- `MAJOR` — rule removed or relaxed (requires companion ADR + maintainer re-sign)

---

## v1.0.0 — 2026-05-26

**Baseline release.**

Covers:
- EU AI Act 2026 (OJ L 2024/1689) — Articles 13, 14, 50
- GDPR (OJ L 119, 4.5.2016) — Articles 5, 6, 7, 17, 30, 32

Rules added:
- `eua.art50.1.disclosure` — AI-system disclosure (L19)
- `eua.art50.4.marking` — AI-generated content marking (L19)
- `eua.art14.compliance_zone` — Compliance-zone routing
- `eua.art14.engine_policy` — Engine-policy allowlist
- `eua.art13.transparency` — Transparency reports (compliance-reports plugin)
- `gdpr.art6.7.consent` — Explicit consent gate (L16)
- `gdpr.art30.32.audit_chain` — Hash-chained audit log (L16)
- `gdpr.art32.secrets` — Secret vault capability split (L16)
- `gdpr.art32.path_gate` — Path-gate write protection (L10)
- `gdpr.art5.voice_transcription` — Voice transcription metadata only (L23)
- `gdpr.art17.erasure` — Right to erasure (L36)
- `gdpr.art5.anonymisation` — Data minimisation / k-Anonymity (L32)
- `gdpr.art5.pii_labels` — No PII in labels/logs/audit (L16)

Legal basis: EU AI Act full applicability date 2026-08-02; GDPR in force.
Signed by: maintainer (see tenant.corvin.yaml::spec.compliance_manifest.signer_fingerprint)
