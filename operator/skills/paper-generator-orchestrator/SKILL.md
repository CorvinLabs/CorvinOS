# Paper Generator Orchestrator: Automated Academic Paper Production

**Scope:** End-to-end orchestration for generating complete academic papers  
**Input:** Concepts (MD files) + ADRs (markdown) + title + authors  
**Output:** Complete paper.pdf (production-ready) + all intermediate artifacts  
**Complexity:** High (multi-agent orchestration with quality gates)  
**Time Investment:** First run ~2-4 hours, subsequent runs ~1-2 hours (template cached)

---

## Overview

This skill automates the entire academic paper generation workflow from Phase 1 (Research) through Phase 5 (Assembly). It:

1. **Orchestrates parallel research agents** to find references
2. **Validates narrative structure** before writing
3. **Generates content with depth heuristics** (enforces min paragraphs per section)
4. **Creates publication-quality diagrams** (SVG → PDF pipeline)
5. **Assembles final LaTeX** with automatic BibTeX formatting and compilation

The skill is **not just a template**—it enforces quality gates and re-runs work when gates fail.

---

## How to Invoke

```
/academic-paper-generator

Inputs:
  - Concept files (path to directory of .md files)
  - ADR files (path to directory of .md files)
  - Paper title (string)
  - Authors (list of strings with affiliations)
  - Output directory (path)
  - Scope (short: 4-8 pages, full: 12-20 pages, xfull: 20+ pages)

Outputs:
  - paper.pdf (final, production-ready)
  - paper.tex (LaTeX source)
  - paper.bib (BibTeX references)
  - figures/ (all diagrams in PDF + SVG source)
  - REPORT.md (documentation of choices made)
```

---

## Five-Phase Automated Workflow

### Phase 1: Automated Research & Reference Collection

**Parallel Research Agents (3-4 agents, concurrent):**

1. **Agent: Academic References**
   ```
   Prompt: "Find 20 peer-reviewed academic papers on [topic]. 
   Return: author, year, title, venue, URL, 1-line relevance to [thesis]"
   Schema: {papers: [{author, year, title, venue, url, relevance}]}
   ```

2. **Agent: Regulatory & Standards References**
   ```
   Prompt: "Find official text + commentary on: GDPR Art 30/32, EU AI Act Art 5/10/50, 
   HIPAA 45 CFR 164, PCI DSS Req 10. Return: regulation, article, requirement, link."
   Schema: {regulations: [{reg_name, article, text, link}]}
   ```

3. **Agent: Industry Incident Analysis**
   ```
   Prompt: "Find 5 major compliance/security incidents (2015-2025): 
   Equifax, Facebook, SolarWinds, [others]. Return: incident name, year, 
   root cause (in compliance terms), regulatory impact."
   Schema: {incidents: [{name, year, root_cause, regulatory_impact, url}]}
   ```

4. **Agent: Competitive/Related Work**
   ```
   Prompt: "Find papers on: compliance-as-code, formal verification of security, 
   audit trail design, multi-tenant isolation. Return: author, title, how it 
   differs from our approach."
   Schema: {related_work: [{author, title, relevance, differs_how}]}
   ```

**Output Aggregation:**
- Merge results into single reference database (CSV/JSON)
- Validate every URL is live (ping test)
- Flag missing references as errors (workflow pauses)

**Quality Gate:**
- Minimum 40 total references across all categories
- Every major section has ≥3 assigned references
- At least 1 incident per introduction section

---

### Phase 2: Automated Narrative Arc Validation

**Step 1: Extract Core Claim from Concepts/ADRs**
```
Scan all input files for patterns like:
  - "The problem is: X"
  - "We propose: Y"
  - "The key insight: Z"
  
Consolidate into single one-sentence claim.
Validate claim is:
  - ✓ Falsifiable (not obviously true)
  - ✓ Novel (not already known)
  - ✓ Significant (has implications)
```

**Step 2: Build Incident → Problem → Solution → Proof Chain**
```
Incidents:  [Equifax(2017), Facebook(2018), SolarWinds(2020)]
            ↓
Problem:    Common pattern: compliance mechanisms treated as optional features
            ↓
Solution:   Make compliance structural invariants (enforced by architecture)
            ↓
Proof:      CorvinOS deployed 12 months, 50+ tenants, zero incidents
```

**Step 3: Generate Section Outline with Word Counts**
```
Introduction:        1000-1200 words (40% of body)
  - Hook (incidents): 300 words
  - Gap (current): 300 words
  - Thesis (ours): 200 words
  - Roadmap: 200 words

Methodology:         700-900 words (25% of body)
  - Formal model: 300 words
  - Threat model: 200 words
  - Principles: 200 words

Results:            1000-1200 words (35% of body)
  - Overview: 100 words
  - Mechanisms (×12): 2000 words total (150-200 per mechanism)

Conclusion:         300-400 words (remaining)

Total Target: 4000-5000 words (short), 8000-12000 words (full)
```

**Quality Gate:**
- Narrative arc is validated as: problem → solution → proof ✓
- Section word counts are reasonable and consistent
- At least one diagram is allocated to each major section
- References are pre-mapped to sections

---

### Phase 3: Automated Content Generation

**Per-Mechanism Content Template:**

For each mechanism (M1-M12, or custom from ADRs):
```
Agent Prompt:
"Write a 250-300 word section on [Mechanism Name] for an academic paper.

Include:
1. Regulatory Basis (1 paragraph, 50-70 words)
   - Cite the regulation [Article X]
   - Explain what it protects
   - Why current implementations fail

2. Structural Solution (1 paragraph, 80-100 words)
   - How we enforce it
   - Why it cannot be disabled
   - Implementation detail

3. Verification (1 paragraph, 50-70 words)
   - How auditors verify it
   - Complexity (O(n) or O(1))
   - Concrete verification method

Use references to:
  [GDPR2016], [EUAIAct2024], [IncidentRef], [AcademicRef]
  
Write in academic English. Be specific, not vague."

Schema: {
  title: string,
  regulatory_basis: string,
  solution: string,
  verification: string,
  references_used: [string],
  word_count: number
}
```

**Per-Section Content Generation:**

```
For Introduction:
  Agent: "Write introduction for paper on compliance-as-architecture.
          Hook: Equifax/Facebook/SolarWinds incidents (why they matter).
          Gap: Current approaches fail (cite ComplianceAsCode[2019]).
          Thesis: Compliance must be structural, not configurable.
          Cite: [Equifax2017], [Facebook2018], [SolarWinds2020], [ComplianceAsCode2019]"

For Methodology:
  Agent: "Write methodology section explaining formal model (CTL*),
          threat model (env vars, signals, code patching),
          design principles (fail-closed, audit-first).
          Use math where appropriate. Cite [Emerson2008], [FormalMethods2015]"

For Related Work:
  Agent: "Compare our approach to prior work on:
          - Compliance-as-Code
          - Formal Verification
          - Cryptographic Audit Trails
          - Multi-Tenant Isolation
          Cite: [ComplianceAsCode2019], [FormalMethods2015], etc.
          Explain how our approach differs (structural vs configurable)."
```

**Depth Enforcement:**
- Minimum 3-5 paragraphs per mechanism ✓
- Minimum word count per section enforced ✓
- All \cite{} commands resolved to actual references ✓
- No placeholder phrases like "...and so on" ✓

**Quality Gate:**
- Every section meets minimum word count
- Every \cite{} is resolved (no undefined citations)
- Density heuristics are met (3-5 para per major section)
- No section is <100 words or >500 words without justification

---

### Phase 4: Automated Diagram Generation & Conversion

**Diagram Specification from Mechanisms:**

```json
{
  "diagrams": [
    {
      "id": "threat_model",
      "title": "Threat Model: Four Adversarial Tactics",
      "type": "flow",
      "description": "Show how env-var injection, signal injection, code patching, 
                      and feature flags are all blocked by structural enforcement",
      "tool": "mermaid",  // or "graphviz", "tikz"
      "source": "figures/threat_model.mmd",
      "caption": "Figure 1: Threat Model. Four adversarial tactics are structurally blocked 
                  by enforcement below the configuration layer. An attacker cannot override 
                  binary-encoded invariants with environment variables or configuration."
    },
    {
      "id": "mechanisms_matrix",
      "title": "12 Mechanisms Overview Matrix",
      "type": "table",
      "description": "Table showing: mechanism name, regulatory basis, properties 
                      (non-toggleable, fail-closed, boot-wired, hash-protected)",
      "tool": "tikz",
      "source": "figures/mechanisms_matrix.tikz",
      "caption": "Figure 2: Compliance Mechanisms Matrix. Each mechanism is non-toggleable, 
                  fail-closed by design, and boot-wired. All are protected by cryptographic 
                  audit integrity."
    },
    // ... more diagrams
  ]
}
```

**Automated SVG → PDF Conversion:**

```bash
# For each diagram source file:
for source in figures/*.mmd figures/*.graphviz figures/*.tikz; do
  # Convert to SVG (tool-dependent)
  mermaid "$source" -o "${source%.mmd}.svg"      # if mermaid
  # OR
  dot -Tsvg "$source" -o "${source%.graphviz}.svg"  # if graphviz
  
  # Convert SVG to PDF (reliable method)
  inkscape --export-type=pdf "${source%.mmd}.svg" -o "${source%.mmd}.pdf"
  
  # Validate PDF
  pdfinfo "${source%.mmd}.pdf" > /dev/null || ERROR "PDF conversion failed"
done
```

**Figure Reference Integration:**

```latex
\begin{figure}[H]
\centering
\includegraphics[width=0.95\textwidth]{figures/threat_model.pdf}
\caption{Figure 1: Threat Model...}
\label{fig:threat_model}
\end{figure}

\textit{As shown in Figure~\ref{fig:threat_model}, ...}
```

**Quality Gate:**
- All SVG source files exist and are version-controlled ✓
- All PDF outputs are valid (pdfinfo succeeds) ✓
- No PNG files in figures directory (SVG/PDF only) ✓
- Every figure has a caption ✓
- Every figure is referenced in text (grep check) ✓

---

### Phase 5: Automated LaTeX Assembly & Compilation

**Template Generation:**

```latex
\documentclass[11pt,a4paper]{article}

% Generate from metadata:
\usepackage{arxiv}
\usepackage[utf8]{inputenc}
\usepackage{hyperref}
\usepackage{amsmath}
\usepackage{graphicx}

\title{[TITLE_FROM_INPUT]}
\author{[AUTHORS_WITH_AFFILIATIONS_FROM_INPUT]}
\date{[CURRENT_DATE]}

\hypersetup{
    pdftitle=[TITLE_FROM_INPUT],
    pdfauthor=[AUTHORS_COMMA_SEPARATED],
    pdfkeywords=[KEYWORDS_FROM_CONTENT]
}

\begin{document}
\maketitle

% Auto-generated from Phase 3 outputs
\begin{abstract}
[ABSTRACT: 150-200 words, single paragraph]
\end{abstract}

\section{Introduction}
[AUTO_GENERATED_FROM_AGENTS + references]

\section{Methodology}
[AUTO_GENERATED_FROM_AGENTS + references]

\section{Results}
[AUTO_GENERATED_MECHANISMS + references]

\section{Related Work}
[AUTO_GENERATED_FROM_AGENTS + references]

\section{Conclusion}
[AUTO_GENERATED_FROM_AGENTS]

\bibliographystyle{plain}
\bibliography{paper}

\end{document}
```

**BibTeX Generation:**

```
Convert reference database (JSON/CSV) → .bib file:

For each reference:
  @article{AuthorYear,
    author = "First Last and Second Last",
    title = "Full Title",
    journal = "Journal Name",
    year = "YYYY",
    volume = "V",
    pages = "P1--P2",
    url = "https://..."
  }
```

**Compilation & Validation:**

```bash
# Step 1: Check for errors
tectonic paper.tex 2>&1 | tee paper.log

# Step 2: Validate output
pdfinfo paper.pdf 2>&1 | head -15

# Step 3: Quality checks
  - File size reasonable (20-500 KB)
  - Page count matches expectation
  - No undefined references (grep "undefined")
  - No missing figures (grep "Figure [0-9]" paper.tex | wc -l)
  
# Step 4: Return artifacts
  - paper.pdf ✓
  - paper.tex ✓
  - paper.bib ✓
  - figures/*.pdf ✓
  - REPORT.md (documentation)
```

**Quality Gate:**
- PDF compiles without errors or warnings ✓
- All figures render correctly (pdfinfo validates) ✓
- Page count is within expected range ✓
- No undefined references (\cite{} all resolved) ✓
- All figures referenced in text ✓

---

## Decision Matrix: When to Use This Skill

| Scenario | Recommendation | Why |
|----------|----------------|-----|
| Writing a new academic paper from scratch | Use this skill | Orchestrates all phases; enforces quality gates |
| Updating an existing paper | Use manually (keep SKILL for reference) | Incremental changes may not need full re-run |
| Quick blog post or technical report | Use academic-paper-generation SKILL only | Simpler workflow, fewer phases |
| Conference paper with tight deadline | Use this skill (short scope) | Generates in ~2 hours with all quality gates |
| Journal submission with months to refine | Use this skill as starting point, then manual | Generates draft; authors refine for publication |

---

## Common Failure Modes & Recovery

| Failure | Symptom | Recovery |
|---------|---------|----------|
| References not found | Agent returns empty list | Manually add 5-10 references; re-run Phase 1 |
| Narrative arc broken | Sections don't connect | Run Phase 2 validation manually; fix outline |
| Content too thin | Word counts below minimum | Re-run Phase 3 with higher target word counts |
| Figure fails to convert | SVG → PDF produces invalid PDF | Check SVG syntax; use Inkscape GUI for manual export |
| Compilation fails | pdflatex error on undefined macros | Check BibTeX references; regenerate .bib file |

---

## Integration with Other Skills

**Prerequisite Skills:**
- `academic-paper-generation` (understanding phases and quality gates)
- `loop-driven-engineering` (for iterating on research, content, figures)
- `dialectical-reasoning` (for narrative arc validation)

**Produces Output For:**
- arXiv submission (`paper.pdf` is direct upload)
- IEEE/ACM journal submission (minor formatting tweaks needed)
- Conference paper submission (verify page limits)
- Internal documentation / knowledge base

---

## Automation Roadmap

**Phase 1 (Research):** ✅ Fully automated (4 parallel agents)
**Phase 2 (Narrative):** ✅ Mostly automated (validation + outline generation)
**Phase 3 (Content):** ✅ Mostly automated (per-section + per-mechanism agents)
**Phase 4 (Figures):** 🟡 Semi-automated (diagram specs generated, conversion scripted, but manual review needed)
**Phase 5 (Assembly):** ✅ Fully automated (template-driven, validated at end)

**Future Enhancements:**
- Automated diagram generation from mechanism descriptions (text → Mermaid)
- Automated abstract extraction from introduction
- Automated keyword extraction from content
- Integration with arXiv API for direct submission
- Multi-language support (generate papers in DE, ES, FR, etc.)

---

## Example Invocation

```bash
/paper-generator-orchestrator \
  --concepts /path/to/concepts/*.md \
  --adrs /path/to/adrs/*.md \
  --title "Structural Enforcement of Regulatory Compliance" \
  --authors "Silvio Jurk (Corvin Labs)" "René de la Barre (Corvin Labs)" \
  --scope full \
  --output /home/user/Corvin-Publications/paper-003-compliance
```

**Expected Output:**
```
✓ Phase 1: Research (40 references found, 5 incidents analyzed)
✓ Phase 2: Narrative (outline validated, word counts planned)
✓ Phase 3: Content (8500 words generated, 45 \cite{} resolved)
✓ Phase 4: Figures (6 diagrams created, SVG→PDF converted)
✓ Phase 5: Assembly (paper.pdf compiled, 18 pages, 650 KB)

Artifacts:
  paper.pdf                          650 KB  ✓
  paper.tex                          45 KB   ✓
  paper.bib                          12 KB   ✓
  figures/                           (6 PDF + 6 SVG source files)
  REPORT.md                          8 KB    ✓

Quality Gates: ALL PASSED ✓
Ready for submission to arXiv / journals.
```

---

## Key Difference from Manual Process

| Step | Manual | Automated |
|------|--------|-----------|
| Research (Phase 1) | 2-4 hours | 30 minutes (parallel agents) |
| Narrative (Phase 2) | 1-2 hours | 15 minutes (validated outline) |
| Writing (Phase 3) | 4-8 hours | 1-2 hours (agent-generated, then refined) |
| Figures (Phase 4) | 2-3 hours | 30-45 minutes (spec→SVG→PDF) |
| Assembly (Phase 5) | 30-60 min | 10-15 minutes (template-driven) |
| **Total** | **10-18 hours** | **~2-4 hours** |
| **Quality Gates** | Manual review | Automated validation + gates |

**Net benefit:** 75% time savings + enforced quality gates.

