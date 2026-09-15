# Diagram Auditor & Fixer

Automated TikZ diagram quality assurance for academic papers.

## Usage

```
/diagram-auditor-fixer --latex paper.tex --iteration 1 --k-max 5
```

## What It Does

Audits TikZ diagrams across 5 dimensions:
1. **Structural Integrity** — Valid TikZ syntax
2. **Visual Clarity** — No overlapping elements
3. **Label Quality** — Readable, spelled-out labels
4. **Color & Contrast** — Colorblind-safe, sufficient contrast
5. **Layout Consistency** — Regular spacing, alignment

## Iterative Loop

- **k=1:** Extract & diagnose (find issues)
- **k=2–4:** Apply fixes & re-audit (measure delta)
- **k=5:** Final verification (0 findings = done)

## Example

```bash
# Audit diagrams in paper.tex
/diagram-auditor-fixer --latex paper.tex --mode audit-only

# Fix issues and re-audit iteratively
/diagram-auditor-fixer --latex paper.tex --mode fix-and-measure
```

## Integration

Called by `paper-generator-orchestrator` at Phase 4b (diagram refinement).

See `/home/shumway/.claude/skills/diagram-auditor-fixer/SKILL.md` for full specification.
