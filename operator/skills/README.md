# CorvinOS Skills Library

Reusable skills for common tasks in CorvinOS development.

## Available Skills

### academic-paper-generation/
Five-phase methodology for writing production-ready academic papers.

**Use:** Documentation, research papers, conference submissions

**Time:** 10-18 hours (manual) or 2-4 hours (with orchestrator)

See `academic-paper-generation/SKILL.md` for full details.

### paper-generator-orchestrator/
End-to-end automation for academic paper generation via multi-agent orchestration.

**Use:** Fast-track paper generation from concepts + ADRs

**Time:** 2-4 hours (fully automated)

See `paper-generator-orchestrator/SKILL.md` for full details.

### academic-paper-writing-reference.md
Quick-reference guide for academic writing best practices.

**Use:** Citation density benchmarks, content heuristics, figure standards, anti-patterns

## Core Principles

1. Narrative Arc is Structural
2. Content Density Matters (3-5 paragraphs/section)
3. Citations are Distribution, not Sum (intro 41%, results 26%)
4. Figures Must Be Structural (captions, references, PDFs)
5. References Must Be Researched (40+ sources, no placeholders)

## Getting Started

1. Read `academic-paper-generation/GETTING_STARTED.md`
2. Choose workflow: fast (orchestrator) or deliberate (phase-by-phase)
3. Run `/paper-generator-orchestrator` or follow methodology manually

## Example: Paper from ADRs

```bash
/paper-generator-orchestrator \
  --concepts ./Corvin-ADR/concepts/*.md \
  --adrs ./Corvin-ADR/decisions/*.md \
  --title "Your Paper Title" \
  --authors "Author Name (Corvin Labs)" \
  --scope full \
  --venue arxiv \
  --output ./Corvin-Publications/paper-003/
```

### diagram-auditor-fixer/
Automated TikZ diagram quality assurance for academic papers.

**Dimensions:** Structural integrity, visual clarity, label quality, color/contrast, layout consistency

**Use:** Phase 4b of paper-generator-orchestrator; iterative refinement until 0 findings

See `diagram-auditor-fixer/README.md` and `/home/shumway/.claude/skills/diagram-auditor-fixer/SKILL.md`

### paper-compliance-auditor/
LaTeX document quality assurance for formatting, references, citations.

**Dimensions:** Reference integrity, citation consistency, cross-refs, formatting, bibliography, structure

**Use:** Phase 5a of paper-generator-orchestrator; iterative refinement until 0 findings

See `paper-compliance-auditor/README.md` and `/home/shumway/.claude/skills/paper-compliance-auditor/SKILL.md`

## Integration with Claude Code

These skills are auto-loaded when working in the CorvinOS repository.
Use `/academic-paper-generation`, `/paper-generator-orchestrator`, `/diagram-auditor-fixer`, or `/paper-compliance-auditor` slash commands.

---

**Added:** 2026-08-13  
**Status:** Production-ready (with quality assurance loop integrated)
