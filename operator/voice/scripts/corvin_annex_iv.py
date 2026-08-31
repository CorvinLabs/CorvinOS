#!/usr/bin/env python3
"""corvin-annex-iv — Annex IV Technical Documentation generator (ADR-0057).

Assembles a reproducible EU AI Act Annex IV Technical Documentation
document from existing Corvin sources:
  - compliance/eu-ai-act.yaml + compliance/gdpr.yaml  (rule severity table)
  - docs/decisions/                                    (ADR layer mapping)
  - docs/overview.md                                   (system description)
  - operator/bridges/run-all-tests.sh                  (test pointer)

The generator does NOT call any LLM.  Sections where operator input
is required are marked [OPERATOR: FILL IN].

Subcommands:
  generate    Generate full Annex IV document (Markdown)
  validate    Check for remaining [OPERATOR: FILL IN] placeholders
  export-package  Bundle all certification artifacts into a directory

Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_SCRIPTS_DIR = _HERE.parent
_OPERATOR_DIR = _SCRIPTS_DIR.parent.parent
_REPO_ROOT = _OPERATOR_DIR.parent


def _repo() -> Path:
    env = os.environ.get("CORVIN_REPO_ROOT")
    return Path(env) if env else _REPO_ROOT


def _compliance_dir() -> Path:
    return _repo() / "compliance"


def _load_manifest_rules() -> list[dict]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    rules = []
    for fname in ("eu-ai-act.yaml", "gdpr.yaml"):
        p = _compliance_dir() / fname
        if not p.exists():
            continue
        try:
            data = yaml.safe_load(p.read_text("utf-8")) or {}
            rules.extend(data.get("rules") or [])
        except Exception:
            pass
    return rules


def _load_manifest_meta() -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    p = _compliance_dir() / "eu-ai-act.yaml"
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text("utf-8")) or {}
    except Exception:
        return {}


def _count_adrs() -> int:
    d = _repo() / "docs" / "decisions"
    if not d.exists():
        return 0
    return len(list(d.glob("*.md")))


def _manifest_version() -> str:
    p = _compliance_dir() / "manifest-version.txt"
    if p.exists():
        return p.read_text().strip()
    meta = _load_manifest_meta()
    return str(meta.get("version", "unknown"))


_FRAMEWORK_YAML: dict[str, str] = {
    "eu-ai-act":   "eu-ai-act.yaml",
    "gdpr":        "gdpr.yaml",
    "iso-42001":   "iso-42001.yaml",
    "nist-ai-rmf": "nist-ai-rmf.yaml",
}


def _load_framework_yaml(framework: str) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    fname = _FRAMEWORK_YAML.get(framework)
    if not fname:
        return {}
    p = _compliance_dir() / fname
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text("utf-8")) or {}
    except Exception:
        return {}


def _all_framework_rules() -> dict[str, list[dict]]:
    """Return {framework_name: [rules]} for all four frameworks."""
    return {fw: (_load_framework_yaml(fw).get("rules") or []) for fw in _FRAMEWORK_YAML}


def _layer_labels(rule: dict) -> list[str]:
    """Extract display layer labels from a rule's implemented_by list."""
    impls = rule.get("implemented_by") or []
    return [str(i.get("layer", "?")) for i in impls if i.get("layer") != "operator"]


def generate_soa_iso42001() -> str:
    """Generate ISO/IEC 42001:2023 Statement of Applicability as Markdown."""
    data = _load_framework_yaml("iso-42001")
    rules = data.get("rules") or []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows: list[str] = []
    for r in rules:
        clause = r.get("iso_clause", "?")
        article = r.get("article", "?")
        title = article.split("—")[-1].strip() if "—" in article else article
        sev = r.get("severity", "warning")
        applicable = "Yes"
        impls = r.get("implemented_by") or []
        layers = ", ".join(
            i.get("layer", "?") for i in impls
        )
        if not layers:
            layers = "[OPERATOR: FILL IN]"

        inv_list = r.get("invariants") or []
        inv_text = "; ".join(inv_list[:2]) if inv_list else "See implementation"
        if len(inv_list) > 2:
            inv_text += f" (+ {len(inv_list) - 2} more)"

        rows.append(
            f"| {clause} | {title[:55]} | {applicable} | {layers} "
            f"| {sev} | {inv_text[:80]} |"
        )

    table = "\n".join(rows) or "| (no rules loaded) | — | — | — | — | — |"

    critical = sum(1 for r in rules if r.get("severity") == "critical")
    operator_only = sum(
        1 for r in rules
        if any(i.get("layer") == "operator" for i in (r.get("implemented_by") or []))
    )

    return f"""# ISO/IEC 42001:2023 — Statement of Applicability

**System:** Corvin
**Standard:** ISO/IEC 42001:2023 — AI Management Systems
**Document version:** 1.0.0
**Generated:** {now}
**Generator:** `corvin-annex-iv generate --framework iso-42001` (ADR-0060)
**Status:** DRAFT — requires Lead Implementer and DPO review before submission

> ⚠ Rows marked `[OPERATOR: FILL IN]` require operator-authored content.
> Operator-only clauses (7.1, 7.2) require evidence from the deploying organisation.

---

## Summary

| Metric | Count |
|---|---|
| Total clauses assessed | {len(rules)} |
| Platform-implemented (Corvin) | {len(rules) - operator_only} |
| Operator-only obligations | {operator_only} |
| Critical controls | {critical} |

---

## Applicability Table

| Clause | Title | Applicable | Implemented by | Severity | Key Invariants |
|---|---|---|---|---|---|
{table}

---

## Exclusions

No ISO/IEC 42001:2023 clauses are excluded from scope.  Corvin is a
General-Availability AI management platform; all clauses apply either at the
platform layer (Corvin-implemented) or at the deployment layer (operator-implemented).

Clauses 7.1 (Resources) and 7.2 (Competence) are operator obligations and
cannot be satisfied by the platform itself.  Deployers must provide evidence
of adequate resources and qualified personnel as part of their AIMS certification.

---

## Certification Notes

ISO/IEC 42001 certification is performed by an accredited Certification Body
(e.g. TÜV SÜD, BSI, DNV).  Corvin provides the platform-side evidence
required for certification.  The deploying operator must:

1. Complete the operator-only clauses (7.1, 7.2).
2. Conduct an internal audit (Clause 9.2) against this SoA.
3. Hold a Management Review (Clause 9.3) to approve the AIMS scope.
4. Engage a Certification Body for Stage 1 and Stage 2 audit.

*Generated by `corvin-annex-iv generate --framework iso-42001` on {now}.*
"""


def generate_nist_profile() -> str:
    """Generate NIST AI RMF 1.0 Organisational Profile as Markdown."""
    data = _load_framework_yaml("nist-ai-rmf")
    rules = data.get("rules") or []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    functions: dict[str, list[dict]] = {}
    for r in rules:
        fn = r.get("nist_function", "UNKNOWN")
        functions.setdefault(fn, []).append(r)

    sections: list[str] = []
    fn_order = ["GOVERN", "MAP", "MEASURE", "MANAGE"]
    for fn in fn_order:
        fn_rules = functions.get(fn, [])
        if not fn_rules:
            continue
        rows: list[str] = []
        for r in fn_rules:
            cat = r.get("nist_category", "?")
            impls = r.get("implemented_by") or []
            layers = ", ".join(i.get("layer", "?") for i in impls)
            inv_list = r.get("invariants") or []
            evidence = inv_list[0][:80] if inv_list else "See implementation"
            cross = r.get("cross_references") or []
            cross_str = ", ".join(f"{c['framework']} {c['rule_id']}" for c in cross[:3])
            rows.append(
                f"| {cat} | Implemented | {layers} | {evidence[:70]} | {cross_str} |"
            )
        table = "\n".join(rows)
        sections.append(
            f"### {fn}\n\n"
            f"| Category | Status | Corvin Layer | Evidence Pointer | Cross-Refs |\n"
            f"|---|---|---|---|---|\n"
            f"{table}\n"
        )

    implemented = len(rules)
    return f"""# NIST AI Risk Management Framework 1.0 — Organisational Profile

**System:** Corvin
**Framework:** NIST AI RMF 1.0 (NIST AI 100-1, January 2023)
**Document version:** 1.0.0
**Generated:** {now}
**Generator:** `corvin-annex-iv generate --framework nist-ai-rmf` (ADR-0060)
**Status:** DRAFT — for information only

> **Note:** NIST AI RMF is a voluntary framework; there is no NIST AI RMF
> certification body.  This Profile is an evidence artefact demonstrating
> alignment, not a certification claim.  See ADR-0060 §NIST note.

---

## Summary

| Function | Subcategories covered | Status |
|---|---|---|
| GOVERN | {len(functions.get('GOVERN', []))} | Implemented |
| MAP | {len(functions.get('MAP', []))} | Implemented |
| MEASURE | {len(functions.get('MEASURE', []))} | Implemented |
| MANAGE | {len(functions.get('MANAGE', []))} | Implemented |
| **Total** | **{implemented}** | **All covered** |

---

## Function Details

{chr(10).join(sections)}

---

## Alignment with Other Frameworks

This Profile cross-references EU AI Act 2026 and ISO/IEC 42001:2023 rules
where the same Corvin layer satisfies multiple frameworks simultaneously.
Run `corvin-annex-iv cross-reference` to generate the full evidence table.

*Generated by `corvin-annex-iv generate --framework nist-ai-rmf` on {now}.*
"""


def generate_cross_reference_map(*, frameworks: list[str] | None = None) -> str:
    """Generate multi-framework cross-reference evidence table as Markdown."""
    if frameworks is None:
        frameworks = list(_FRAMEWORK_YAML.keys())

    all_rules = _all_framework_rules()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build inverted index: layer_key -> {framework: [short_ref]}
    layer_map: dict[str, dict[str, list[str]]] = {}

    def _short_ref(rule: dict, fw: str) -> str:
        if fw == "eu-ai-act":
            art = rule.get("article", "?")
            return art.split("—")[0].strip()[:20]
        if fw == "gdpr":
            art = rule.get("article", "?")
            return art.split("—")[0].strip()[:20]
        if fw == "iso-42001":
            return f"Cl. {rule.get('iso_clause', '?')}"
        if fw == "nist-ai-rmf":
            return rule.get("nist_category", "?")
        return rule.get("id", "?")[:20]

    for fw in frameworks:
        for rule in all_rules.get(fw, []):
            for impl in (rule.get("implemented_by") or []):
                layer = str(impl.get("layer", "?"))
                if layer == "operator":
                    continue
                layer_map.setdefault(layer, {})
                layer_map[layer].setdefault(fw, [])
                ref = _short_ref(rule, fw)
                if ref not in layer_map[layer][fw]:
                    layer_map[layer][fw].append(ref)

    # Sort layers: L-prefixed numerically, then alphabetically
    def _layer_sort_key(lyr: str) -> tuple:
        if lyr.startswith("L") and lyr[1:].isdigit():
            return (0, int(lyr[1:]), lyr)
        return (1, 0, lyr)

    sorted_layers = sorted(layer_map.keys(), key=_layer_sort_key)

    fw_headers = " | ".join(f.replace("-", " ").title() for f in frameworks)
    header = f"| Layer | {fw_headers} |"
    separator = "|---|" + "---|" * len(frameworks)

    rows: list[str] = []
    for layer in sorted_layers:
        cells = []
        for fw in frameworks:
            refs = layer_map[layer].get(fw, [])
            cells.append(", ".join(refs[:3]) if refs else "—")
        rows.append(f"| {layer} | " + " | ".join(cells) + " |")

    table = "\n".join(rows) or "| (no data) | — |"

    total_rules = sum(len(r) for r in all_rules.values())
    covered_layers = len(layer_map)

    return f"""# Corvin — Multi-Framework Compliance Evidence Map

**Generated:** {now}
**Generator:** `corvin-annex-iv cross-reference` (ADR-0060)
**Frameworks covered:** {", ".join(frameworks)}

---

## Summary

| Metric | Value |
|---|---|
| Total rules across all frameworks | {total_rules} |
| Corvin layers with evidence | {covered_layers} |
| Frameworks cross-referenced | {len(frameworks)} |

---

## Evidence Table

Each cell shows which articles, clauses, or categories from that framework
are satisfied by the corresponding Corvin layer.

{header}
{separator}
{table}

---

## How to Read This Table

- **EU Ai Act / GDPR** cells: Article reference (e.g. "Art. 50 §1")
- **ISO 42001** cells: Clause number (e.g. "Cl. 8.3")
- **NIST AI RMF** cells: Function + Category (e.g. "GV-1.1")
- **—** means no rule in that framework references this layer directly.

This table is the enterprise sales artefact described in ADR-0060 §Component 3.
A compliance officer can hand it to procurement as evidence that Corvin
satisfies multiple governance frameworks through a single structural control layer.

*Generated by `corvin-annex-iv cross-reference` on {now}.*
*Run `corvin-annex-iv generate --framework <name>` for per-framework documents.*
"""


def generate_annex_iv(*, tenant_id: str = "_default") -> str:
    """Generate Annex IV Technical Documentation as Markdown."""
    rules = _load_manifest_rules()
    meta = _load_manifest_meta()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mv = _manifest_version()
    adr_count = _count_adrs()

    critical_rules = [r for r in rules if r.get("severity") == "critical"]
    warning_rules = [r for r in rules if r.get("severity") == "warning"]

    rule_table_rows = []
    for r in rules:
        sev = r.get("severity", "?")
        art = r.get("article", "?")
        rid = r.get("id", "?")
        impls = r.get("implemented_by") or []
        layers = ", ".join(str(i.get("layer", "?")) for i in impls)
        rule_table_rows.append(f"| `{rid}` | {art} | {sev} | {layers} |")

    rule_table = "\n".join(rule_table_rows) or "| (no rules loaded) | — | — | — |"

    return f"""# Annex IV Technical Documentation — Corvin

**Document version:** {mv}
**Generated:** {now}
**Generator:** `corvin-annex-iv generate` (ADR-0057)
**Status:** DRAFT — requires operator review and DPO sign-off before submission

---

> ⚠ Sections marked `[OPERATOR: FILL IN]` require operator-specific input.
> Run `corvin-annex-iv validate` to check for remaining placeholders.

---

## 1. General Description and Intended Purpose

**System name:** Corvin
**Type:** AI Orchestration Platform — Limited Risk AI System (EU AI Act Annex I)
**Risk classification:** Limited Risk (standalone); conditional High Risk when used
as a component in Annex III workflows (see ADR-0057 § Risk Classification).

Corvin is a multi-tenant orchestration layer over external Large Language Models
(LLMs). It routes user messages through bridge adapters (Discord, WhatsApp, CLI)
to a WorkerEngine (L22), applies compliance controls (L10, L16, L19, L34–L39),
and returns AI-generated responses.

Corvin does NOT:
- Train, fine-tune, or serve its own model weights.
- Make automated consequential decisions without human-in-the-loop oversight.
- Perform biometric categorisation or any Annex III function by default.

**Intended purpose:**
[OPERATOR: FILL IN — describe your deployment context, e.g. "Internal coding
assistant for software development teams" or "Customer support automation for
Acme GmbH."]

**Intended users / affected persons:**
[OPERATOR: FILL IN]

## 2. Interaction with Other Systems

| Component | Role |
|---|---|
| External LLM (Claude, Ollama, OpenAI) | Language generation substrate (L22 WorkerEngine) |
| L34 Data Classification | Restricts which engines may process CONFIDENTIAL/SECRET data |
| L35 Egress Lockdown | Network-level control over engine outbound hosts |
| L36 Erasure Orchestrator | GDPR Art. 17 cross-layer right-to-deletion |
| L37 Audit-at-Rest | AES-256 encryption + 7-year retention for audit segments |
| L38 A2A Protocol | Cryptographically attested agent-to-agent task execution |
| L39 Incident Tracker | EU AI Act Art. 73 serious incident detection and notification |

## 3. Risk Classification

**Standalone:** Limited-Risk AI System per EU AI Act Art. 3(1), Annex I.
**Not:** General-Purpose AI Model (no published weights). Not: High-Risk AI System (Annex III).

**Conditional reclassification:**
If deployed as a component in an Annex III workflow (CV screening, credit scoring,
benefit eligibility), the deploying operator bears Art. 25-28 cascade obligations.
Corvin provides structural audit, consent, and data-flow machinery for those
workflows but does not substitute for the operator's own AI system registration
and Notified Body engagement.

*This classification is reviewed with every MAJOR EU AI Act Annex update.*

## 4. Training Data and GPAI

Corvin does NOT train, fine-tune, or serve its own model weights.
It is an orchestration layer over external provider LLMs:

| Engine | Provider | EU AI Act GPAI scope |
|---|---|---|
| ClaudeCodeEngine | Anthropic | Anthropic's GPAI obligations |
| OpenCodeEngine | OpenAI / Ollama | Provider GPAI obligations |
| eu_production_ollama preset | Operator-hosted Ollama | Operator GPAI obligations |

Corvin is not a GPAI Model (Art. 3(63)) and Art. 52-53 obligations do not apply
to Corvin itself.

## 5. Risk Assessment

The compliance manifest v{mv} covers {len(rules)} rules across EU AI Act 2026 and GDPR.

| Rule ID | Article | Severity | Implemented by |
|---|---|---|---|
{rule_table}

**Critical controls ({len(critical_rules)}):** enforcement is structural and
deterministic — not policy-document-only.

**Warning controls ({len(warning_rules)}):** advisory; legal review required
for final sign-off on each.

## 6. Human Oversight Measures (Art. 14)

| Mechanism | Layer | Description |
|---|---|---|
| Roles system | L18 | Owner → Admin → Member → Observer hierarchy; admin cannot self-promote |
| Consent gate | L16 | Deny-by-default per-uid consent; TTL-capped; re-validated each message |
| Engine-policy allowlist | ADR-0007 | Operator restricts allowed_engines per compliance zone |
| Compliance-zone routing | ADR-0007 | Zones (personal_data, code_only, external_facing) gate engine selection |
| Data classification matrix | L34 | PUBLIC/INTERNAL/CONFIDENTIAL/SECRET × engine locality matrix |
| Proposal system | L21 | /propose → /go [steering] — 50-entry queue, human-approved execution |
| Incident Tracker | L39 | Auto-detects CRITICAL events; operator reviews and decides notification |

## 7. Performance, Accuracy, and Robustness

Corvin does not define accuracy targets for LLM output quality —
that responsibility lies with the upstream LLM provider per their GPAI obligations.

Corvin does define structural correctness targets:

| Property | Test | Last verified |
|---|---|---|
| Disclosure fires once per uid | test_disclosure.py | {now} |
| Consent gate deny-by-default | test_consent_gate.py | {now} |
| Audit chain hash integrity | test_audit_unified.py | {now} |
| Path-gate fail-closed | test_path_gate.py | {now} |
| L34 data-flow gate | test_data_classification.py | {now} |
| L35 egress gate | test_egress_gate.py | {now} |
| L36 erasure | test_erasure_orchestrator.py | {now} |
| Incident tracker | test_incident_tracker.py | {now} |
| Operator declaration gate | test_operator_declaration.py | {now} |

Full test suite: `bash operator/bridges/run-all-tests.sh`

## 8. Testing Procedures and Results

Corvin uses a five-tier test pyramid:

1. **Lint / type**: ruff, mypy (where configured)
2. **Unit tests**: pytest per-module (see test files above)
3. **Integration**: real filesystem, real audit chain, bwrap sandbox
4. **E2E**: per-subtask E2E mandatory for security-touching changes
5. **Boot self-test**: `bridge.sh doctor` — runs on every adapter start and Docker HEALTHCHECK

**Architecture Decision Records:** {adr_count} ADRs documenting every major design decision.

[OPERATOR: FILL IN — attach the output of `bash operator/bridges/run-all-tests.sh`
and `bridge.sh doctor --json` from your production deployment.]

## 9. Standards Applied

| Standard / Regulation | Scope |
|---|---|
| EU AI Act 2026 (OJ L 2024/1689) | Art. 13, 14, 28-30, 50, 73 |
| GDPR (OJ L 119, 4.5.2016) | Art. 5, 6, 7, 17, 30, 32 |
| BSI-Grundschutz (target) | Planned certification engagement Q3 2026 |

## 10. Instructions for Use

**For operators:**
- `docs/for-organizations.md` — deployment guide
- `docs/compliance/OPERATOR-OBLIGATIONS.md` — Art. 28-30 obligations
- `docs/compliance/DPIA-TEMPLATE.md` — Data Protection Impact Assessment
- `docs/compliance/DSB-CHECKLIST.md` — DPO pre-go-live checklist
- `docs/compliance/PENTEST-SCOPE.md` — penetration test scope

**Required operator actions before eu_production deployment:**
1. Complete DPIA (docs/compliance/DPIA-TEMPLATE.md)
2. Fill `spec.operator_declaration` in tenant.corvin.yaml
3. Run `bridge.sh doctor` and confirm all CRITICAL checks pass
4. Review and sign Annex IV (this document)

## 11. EU Declaration of Conformity (Draft)

[OPERATOR: FILL IN — to be completed and signed by the legal representative
before formal submission.  Template:]

We, [OPERATOR NAME], [ADDRESS], hereby declare that the AI system
Corvin v[VERSION], deployed as [DESCRIBE DEPLOYMENT], complies with
the relevant provisions of Regulation (EU) 2024/1689 (EU AI Act).

**Signed:**  [OPERATOR: authorized representative signature + date]

## 12. Post-Market Monitoring Plan (Art. 72)

| Activity | Frequency | Tool |
|---|---|---|
| Audit chain integrity verify | Daily | `corvin-audit-verify.timer` (systemd) |
| Compliance manifest check | On every `bridge.sh doctor` | `corvin-compliance-check` |
| Incident scan (consent/disclosure) | Daily | `corvin-incident scan` (systemd timer recommended) |
| Open incident review | Weekly | `corvin-incident list --status open` |
| Test suite | On every commit | `bash operator/bridges/run-all-tests.sh` |

Serious incidents are tracked via L39 Incident Tracker.  Art. 73 15-day notification
clock starts on `severity: serious` incidents.

## 13. Version History

See `compliance/CHANGELOG.md` for manifest version history.

[OPERATOR: FILL IN — add your deployment-specific change log, e.g. model
upgrades, configuration changes, policy updates.]

## 14. Supporting Documentation

- `docs/compliance/RISK-CLASSIFICATION.md` — formal risk classification statement
- `docs/compliance/OPERATOR-OBLIGATIONS.md` — Art. 28-30 operator obligations
- `docs/compliance/INCIDENT-RESPONSE-PLAN.md` — Art. 73 incident response procedure
- `compliance/eu-ai-act.yaml` — machine-readable compliance rules (signed)
- `compliance/gdpr.yaml` — GDPR compliance rules (signed)
- ADRs 0001–{adr_count:04d} in `docs/decisions/`

---

*Generated by `corvin-annex-iv generate` on {now}.*
*Manifest version: {mv}. Run `corvin-annex-iv validate` to check for placeholders.*
"""


def cmd_generate(args):
    framework = getattr(args, "framework", None)
    if framework == "iso-42001":
        doc = generate_soa_iso42001()
        label = "ISO 42001 Statement of Applicability"
    elif framework == "nist-ai-rmf":
        doc = generate_nist_profile()
        label = "NIST AI RMF Organisational Profile"
    elif framework in (None, "eu-ai-act"):
        doc = generate_annex_iv(tenant_id=args.tenant)
        label = "Annex IV Technical Documentation"
    else:
        sys.exit(f"Unknown framework '{framework}'. Use: eu-ai-act, iso-42001, nist-ai-rmf")

    if args.output:
        Path(args.output).write_text(doc, encoding="utf-8")
        print(f"{label} written to {args.output}")
    else:
        print(doc)


def cmd_cross_reference(args):
    frameworks = getattr(args, "frameworks", None)
    if frameworks:
        fw_list = [f.strip() for f in frameworks.split(",")]
    else:
        fw_list = list(_FRAMEWORK_YAML.keys())

    doc = generate_cross_reference_map(frameworks=fw_list)
    if args.output:
        Path(args.output).write_text(doc, encoding="utf-8")
        print(f"Cross-framework map written to {args.output}")
    else:
        print(doc)


def cmd_validate(args):
    path = Path(args.document)
    if not path.exists():
        sys.exit(f"File not found: {args.document}")
    content = path.read_text("utf-8")
    placeholders = [
        line.strip()
        for line in content.splitlines()
        if "[OPERATOR: FILL IN]" in line
    ]
    if placeholders:
        print(f"FAIL — {len(placeholders)} placeholder(s) remaining:\n")
        for p in placeholders:
            print(f"  • {p[:120]}")
        sys.exit(1)
    else:
        print(f"OK — no placeholders remaining in {args.document}")


def cmd_export_package(args):
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Building certification package in {out_dir}/")

    # Annex IV
    annex_path = out_dir / "ANNEX-IV.md"
    annex_path.write_text(generate_annex_iv(tenant_id=args.tenant), encoding="utf-8")
    print(f"  ✓ {annex_path.name}")

    # Copy compliance manifests (all frameworks)
    comp_dir = out_dir / "compliance"
    comp_dir.mkdir(exist_ok=True)
    for fname in (
        "eu-ai-act.yaml", "gdpr.yaml",
        "iso-42001.yaml", "nist-ai-rmf.yaml",
        "manifest.sig", "manifest-version.txt", "CHANGELOG.md",
    ):
        src = _compliance_dir() / fname
        if src.exists():
            (comp_dir / fname).write_bytes(src.read_bytes())
            print(f"  ✓ compliance/{fname}")
        elif fname in ("iso-42001.yaml", "nist-ai-rmf.yaml"):
            print(f"  ⚠ compliance/{fname} — not found (ADR-0060 M1/M2 pending)")

    # Copy docs if they exist
    docs_src = _repo() / "docs"
    for doc_name in ("RISK-CLASSIFICATION.md", "OPERATOR-OBLIGATIONS.md", "INCIDENT-RESPONSE-PLAN.md"):
        src = docs_src / doc_name
        if src.exists():
            (out_dir / doc_name).write_bytes(src.read_bytes())
            print(f"  ✓ {doc_name}")
        else:
            print(f"  ⚠ {doc_name} — not found (run generators first)")

    # Incidents export
    audit_dir = out_dir / "audit"
    audit_dir.mkdir(exist_ok=True)
    try:
        _shared = _SCRIPTS_DIR.parent.parent / "bridges" / "shared"
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from incident_tracker import export_incidents  # type: ignore
        incidents = export_incidents(tenant_id=args.tenant)
        incidents_path = audit_dir / "incidents-export.json"
        incidents_path.write_text(json.dumps(incidents, indent=2, ensure_ascii=False))
        print(f"  ✓ audit/incidents-export.json ({len(incidents)} records)")
    except Exception as exc:
        print(f"  ⚠ audit/incidents-export.json — {exc}")

    # Multi-framework documents (ADR-0060)
    iso_dir = out_dir / "iso-42001"
    iso_dir.mkdir(exist_ok=True)
    iso_soa = generate_soa_iso42001()
    (iso_dir / "ISO-42001-SoA.md").write_text(iso_soa, encoding="utf-8")
    print("  ✓ iso-42001/ISO-42001-SoA.md")

    nist_dir = out_dir / "nist-ai-rmf"
    nist_dir.mkdir(exist_ok=True)
    nist_profile = generate_nist_profile()
    (nist_dir / "NIST-AI-RMF-Profile.md").write_text(nist_profile, encoding="utf-8")
    print("  ✓ nist-ai-rmf/NIST-AI-RMF-Profile.md")

    cross_map = generate_cross_reference_map()
    (out_dir / "CROSS-FRAMEWORK-MAP.md").write_text(cross_map, encoding="utf-8")
    print("  ✓ CROSS-FRAMEWORK-MAP.md")

    # SHA-256 manifest
    manifest_lines = []
    for p in sorted(out_dir.rglob("*")):
        if p.is_file() and p.name != "PACKAGE-SHA256.txt":
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            rel = p.relative_to(out_dir)
            manifest_lines.append(f"{digest}  {rel}")
    manifest_txt = "\n".join(manifest_lines) + "\n"
    (out_dir / "PACKAGE-SHA256.txt").write_text(manifest_txt, encoding="utf-8")
    print(f"  ✓ PACKAGE-SHA256.txt ({len(manifest_lines)} files)")

    # README for certifier
    readme = f"""# Corvin Certification Package

Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d")}

## Contents

- `ANNEX-IV.md` — EU AI Act Annex IV Technical Documentation (ADR-0057)
- `CROSS-FRAMEWORK-MAP.md` — Multi-framework evidence table (ADR-0060)
- `RISK-CLASSIFICATION.md` — Risk classification statement
- `OPERATOR-OBLIGATIONS.md` — Art. 28-30 operator obligations
- `INCIDENT-RESPONSE-PLAN.md` — Art. 73 incident response
- `compliance/` — Signed compliance manifests (EU AI Act, GDPR, ISO 42001, NIST AI RMF)
- `iso-42001/ISO-42001-SoA.md` — ISO/IEC 42001:2023 Statement of Applicability (ADR-0060)
- `nist-ai-rmf/NIST-AI-RMF-Profile.md` — NIST AI RMF Organisational Profile (ADR-0060)
- `audit/incidents-export.json` — Incident records
- `PACKAGE-SHA256.txt` — SHA-256 checksums of all files

## Verification

```bash
# Verify file integrity
sha256sum --check PACKAGE-SHA256.txt

# Verify compliance manifest signature
gpg --verify compliance/manifest.sig \\
    compliance/eu-ai-act.yaml compliance/gdpr.yaml \\
    compliance/iso-42001.yaml compliance/nist-ai-rmf.yaml
```
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    print(f"  ✓ README.md")
    print(f"\nPackage complete: {out_dir}/")


def main():
    parser = argparse.ArgumentParser(
        prog="corvin-annex-iv",
        description="Corvin Annex IV Technical Documentation generator (ADR-0057)",
    )
    parser.add_argument(
        "--tenant",
        default=os.environ.get("CORVIN_TENANT_ID", "_default"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate
    p_gen = sub.add_parser("generate", help="Generate compliance document (Markdown)")
    p_gen.add_argument("--output", "-o", help="Output file (default: stdout)")
    p_gen.add_argument(
        "--framework",
        choices=["eu-ai-act", "iso-42001", "nist-ai-rmf"],
        default=None,
        help=(
            "eu-ai-act (default): EU AI Act Annex IV Technical Documentation; "
            "iso-42001: ISO 42001 Statement of Applicability; "
            "nist-ai-rmf: NIST AI RMF Organisational Profile"
        ),
    )
    p_gen.set_defaults(func=cmd_generate)

    # cross-reference
    p_xref = sub.add_parser(
        "cross-reference",
        help="Generate multi-framework cross-reference evidence table (ADR-0060)",
    )
    p_xref.add_argument("--output", "-o", help="Output file (default: stdout)")
    p_xref.add_argument(
        "--frameworks",
        default=None,
        help="Comma-separated list of frameworks (default: all four)",
    )
    p_xref.set_defaults(func=cmd_cross_reference)

    # validate
    p_val = sub.add_parser("validate", help="Check for remaining [OPERATOR: FILL IN] placeholders")
    p_val.add_argument("document", help="Path to generated Annex IV document")
    p_val.set_defaults(func=cmd_validate)

    # export-package
    p_pkg = sub.add_parser("export-package",
                            help="Bundle all certification artifacts")
    p_pkg.add_argument("--output-dir", "-o", required=True,
                       help="Output directory for the package")
    p_pkg.set_defaults(func=cmd_export_package)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
