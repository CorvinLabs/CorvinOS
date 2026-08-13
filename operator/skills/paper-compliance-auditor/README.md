# Paper Compliance Auditor

LaTeX document quality assurance for academic papers — formatting, references, citations.

## Usage

```
/paper-compliance-auditor --latex paper.tex --bib paper.bib --iteration 1 --k-max 5
```

## What It Does

Audits papers across 6 compliance dimensions:
1. **Reference Integrity** — All citations resolved
2. **Citation Consistency** — Uniform citation style
3. **Cross-Reference Completeness** — All figures/sections labeled & referenced
4. **Formatting Consistency** — Margins, fonts, spacing uniform
5. **Bibliography Quality** — All entries complete
6. **Document Structure** — Abstract, sections, conclusion present

## Iterative Loop

- **k=1:** Parse structure & diagnose (find issues)
- **k=2–4:** Apply fixes & re-audit (measure delta)
- **k=5:** Final verification (0 findings = done)

## Example

```bash
# Audit paper compliance
/paper-compliance-auditor --latex paper.tex --bib paper.bib --mode audit-only

# Fix issues and re-audit iteratively
/paper-compliance-auditor --latex paper.tex --bib paper.bib --mode fix-and-measure
```

## Integration

Called by `paper-generator-orchestrator` at Phase 5a (document compliance).

See `/home/shumway/.claude/skills/paper-compliance-auditor/SKILL.md` for full specification.
